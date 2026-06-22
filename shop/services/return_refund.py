"""Fair return refund: proportional line_total, order discounts, VAT, and shipping."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from shop.models import Order, OrderItem, OrderReturn

_MONEY = Decimal('0.01')


def _quantize(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_MONEY, rounding=ROUND_HALF_UP)


def _order_coupon_type(order: Order) -> str:
    try:
        from offer.models import CouponUsageLog

        usage = (
            CouponUsageLog.objects.filter(order_id=order.pk)
            .order_by('-used_at')
            .first()
        )
        if usage is not None:
            return (usage.coupon_type or '').strip().lower()
    except Exception:
        pass
    return ''


def _order_coupon_discount(order: Order) -> Decimal:
    from shop.services.zoho_books_invoice import _order_coupon_discount

    return _order_coupon_discount(order)


def _product_discount_to_allocate(order: Order) -> Decimal:
    """
    Order-level product discount to spread across lines by line_total share.

    buyxgety discounts are already stored in the get-item line_total; do not
    allocate the usage-log coupon amount onto buy lines.
    """
    loyalty = _quantize(order.loyalty_discount)
    coupon_amt = _order_coupon_discount(order)
    coupon_type = _order_coupon_type(order)
    if coupon_type == 'buyxgety':
        return loyalty
    return _quantize(loyalty + coupon_amt)


def _taxable_subtotal_for_vat(order: Order) -> Decimal:
    subtotal = _quantize(order.subtotal)
    product_discount = _product_discount_to_allocate(order)
    return _quantize(max(subtotal - product_discount, Decimal('0.00')))


def return_line_refund_amount(
    order_item: OrderItem,
    return_qty: int,
    order: Order,
) -> Decimal:
    """
    Refund for returned quantity of one order line:

      line_gross     = line_total × (return_qty / qty_ordered)
      discount_share = product_discount × (line_gross / order.subtotal)
      product_refund = line_gross − discount_share
      vat_share      = order.vat_amount × (product_refund / taxable_subtotal)
      shipping_share = order.shipping_amount × (line_gross / order.subtotal)
    """
    qty_ordered = int(order_item.quantity or 0)
    if qty_ordered <= 0 or return_qty <= 0:
        return Decimal('0.00')

    line_total = _quantize(order_item.line_total)
    subtotal = _quantize(order.subtotal)
    ratio = Decimal(str(return_qty)) / Decimal(str(qty_ordered))
    line_gross = (line_total * ratio).quantize(_MONEY, rounding=ROUND_HALF_UP)

    product_discount = _product_discount_to_allocate(order)
    if subtotal > 0:
        discount_share = (product_discount * line_gross / subtotal).quantize(
            _MONEY, rounding=ROUND_HALF_UP,
        )
    else:
        discount_share = Decimal('0.00')

    product_refund = max(line_gross - discount_share, Decimal('0.00'))

    taxable = _taxable_subtotal_for_vat(order)
    vat_amount = _quantize(order.vat_amount)
    if taxable > 0:
        vat_share = (vat_amount * product_refund / taxable).quantize(
            _MONEY, rounding=ROUND_HALF_UP,
        )
    else:
        vat_share = Decimal('0.00')

    shipping_amount = _quantize(order.shipping_amount)
    if subtotal > 0 and shipping_amount > 0:
        shipping_share = (shipping_amount * line_gross / subtotal).quantize(
            _MONEY, rounding=ROUND_HALF_UP,
        )
    else:
        shipping_share = Decimal('0.00')

    return (product_refund + vat_share + shipping_share).quantize(
        _MONEY, rounding=ROUND_HALF_UP,
    )


def return_refund_amount(order_return: OrderReturn) -> Decimal:
    """Total fair refund for all lines on a return."""
    order = order_return.order
    total = Decimal('0.00')
    for line in order_return.lines.select_related('order_item').all():
        total += return_line_refund_amount(
            line.order_item,
            int(line.quantity),
            order,
        )
    return _quantize(total)


def order_returns_refund_total(order: Order) -> Decimal:
    """Sum fair refund amounts for active returns on an order."""
    from shop.models import OrderReturn

    total = Decimal('0.00')
    active_statuses = (
        OrderReturn.Status.PENDING_ZOHO,
        OrderReturn.Status.SYNCED,
        OrderReturn.Status.COMPLETED,
    )
    returns = order.returns.filter(status__in=active_statuses).prefetch_related(
        'lines__order_item',
    )
    for ret in returns:
        total += return_refund_amount(ret)
    return _quantize(total)
