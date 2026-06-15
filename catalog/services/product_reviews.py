"""Rules for who may review a catalog product."""

from __future__ import annotations

from catalog.models import ProductReview
from shop.models import Order, OrderItem


def user_has_delivered_purchase(user, product) -> bool:
    """True if the user bought this product on at least one delivered (synced) order."""
    if user is None or not getattr(user, 'pk', None):
        return False
    return OrderItem.objects.filter(
        order__user_id=user.pk,
        order__status=Order.Status.SYNCED,
        product_id=product.pk,
    ).exists()


def user_review_for_product(user, product) -> ProductReview | None:
    if user is None or not getattr(user, 'pk', None) or product is None:
        return None
    return ProductReview.objects.filter(user_id=user.pk, product_id=product.pk).first()


def user_can_review_product(user, product) -> bool:
    """Delivered purchase and no existing review."""
    if user_review_for_product(user, product) is not None:
        return False
    return user_has_delivered_purchase(user, product)


def order_allows_product_reviews(order: Order) -> bool:
    """Reviews are allowed for line items on delivered (synced) orders."""
    return order.status == Order.Status.SYNCED


def pending_review_lines_for_order(user, order: Order) -> list[dict]:
    """
    Order line items the user can still review (synced order, not yet reviewed).

    One entry per product (deduped). Used on GET /api/shop/orders/detail/.
    """
    if user is None or not getattr(user, 'pk', None):
        return []
    if order.user_id != user.pk:
        return []
    if not order_allows_product_reviews(order):
        return []

    items = [oi for oi in order.items.all() if oi.product_id]
    if not items:
        return []

    product_ids = {oi.product_id for oi in items}
    reviewed_ids = set(
        ProductReview.objects.filter(
            user_id=user.pk,
            product_id__in=product_ids,
        ).values_list('product_id', flat=True),
    )

    currency = ((order.currency or '') or 'AED').strip() or 'AED'
    store_id = order.store_id
    seen_products: set[int] = set()
    lines: list[dict] = []

    for oi in items:
        if oi.product_id in reviewed_ids or oi.product_id in seen_products:
            continue
        seen_products.add(oi.product_id)
        product = getattr(oi, 'product', None)
        zoho_product_id = (getattr(product, 'zoho_product_id', '') or '').strip()
        image_url = (getattr(product, 'image_url', '') or '').strip()
        unit = oi.unit_price
        line = {
            'order_item_id': oi.pk,
            'product_id': oi.product_id,
            'zoho_product_id': zoho_product_id,
            'product_name': oi.product_name,
            'sku': oi.sku or '',
            'image_url': image_url,
            'quantity_ordered': int(oi.quantity or 0),
            'unit_price': str(unit),
            'unit_price_display': f'{currency} {unit}',
            'can_review': True,
        }
        if zoho_product_id and store_id:
            line['review_path'] = (
                f'/api/catalog/stores/products/reviews/?store_id={store_id}'
                f'&zoho_product_id={zoho_product_id}'
            )
        lines.append(line)

    return lines
