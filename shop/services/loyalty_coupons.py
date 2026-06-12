"""Loyalty issued-coupon listing and cleanup (used or expired after TTL)."""

from __future__ import annotations

import logging

from django.db.models import Q
from django.utils import timezone

from shop.models import LoyaltyIssuedCoupon

logger = logging.getLogger(__name__)


def active_loyalty_coupons_queryset(*, user):
    """Unused coupons that have not yet expired."""
    now = timezone.now()
    return (
        LoyaltyIssuedCoupon.objects.filter(
            user=user,
            used_at__isnull=True,
            expires_at__gt=now,
        )
        .order_by('-created_at')
    )


def purge_stale_loyalty_coupons(*, user=None) -> int:
    """
    Delete loyalty coupons that are expired and unused, or already used.

    Expiry TTL is set when the coupon is issued (default 90 days via settings).
    """
    now = timezone.now()
    qs = LoyaltyIssuedCoupon.objects.filter(
        Q(used_at__isnull=False)
        | Q(used_at__isnull=True, expires_at__lte=now),
    )
    if user is not None:
        qs = qs.filter(user=user)
    deleted, breakdown = qs.delete()
    if deleted:
        logger.info(
            'loyalty-coupons: purged %s row(s) user=%s breakdown=%s',
            deleted,
            getattr(user, 'pk', None) if user is not None else 'all',
            breakdown,
        )
    return deleted


def remove_loyalty_coupon_after_use(coupon: LoyaltyIssuedCoupon) -> None:
    """Remove coupon from the wallet once it has been applied at checkout."""
    coupon_id = coupon.pk
    code = coupon.code
    coupon.delete()
    logger.info('loyalty-coupons: removed used coupon id=%s code=%s', coupon_id, code)


def _coupon_redeem_status(coupon: LoyaltyIssuedCoupon) -> str:
    if coupon.used_at:
        return 'used'
    if coupon.expires_at and coupon.expires_at <= timezone.now():
        return 'expired'
    return 'active'


def build_loyalty_redeem_history(user, *, store=None, limit: int = 20) -> list[dict]:
    """
    Points spent exchanging Super Coins for coupons, plus direct checkout redemptions.

    Coupon rows are account-wide. Checkout redemptions are filtered to ``store`` when given.
    """
    from decimal import Decimal

    from shop.models import Order
    from shop.serializers import order_code_for_order

    items: list[dict] = []

    coupon_qs = (
        LoyaltyIssuedCoupon.objects.filter(user=user)
        .select_related('order')
        .order_by('-created_at')
    )
    for row in coupon_qs[:limit]:
        items.append({
            'type': 'coupon',
            'points': int(row.points_spent or 0),
            'amount_aed': str(Decimal(str(row.amount_aed or 0)).quantize(Decimal('0.01'))),
            'coupon_code': row.code,
            'status': _coupon_redeem_status(row),
            'order_id': row.order_id,
            'order_code': (
                order_code_for_order(row.order)
                if row.order_id and row.order is not None
                else None
            ),
            'used_at': row.used_at.isoformat() if row.used_at else None,
            'expires_at': row.expires_at.isoformat() if row.expires_at else None,
            'at': row.created_at.isoformat() if row.created_at else None,
        })

    order_qs = (
        Order.objects.filter(user=user, loyalty_points_redeemed__gt=0)
        .select_related('store')
        .order_by('-created_at')
    )
    if store is not None:
        order_qs = order_qs.filter(store=store)
    for order in order_qs[:limit]:
        items.append({
            'type': 'checkout',
            'points': int(order.loyalty_points_redeemed or 0),
            'amount_aed': str(
                Decimal(str(order.loyalty_discount or 0)).quantize(Decimal('0.01')),
            ),
            'coupon_code': '',
            'status': 'applied',
            'order_id': order.pk,
            'order_code': order_code_for_order(order),
            'store_id': order.store_id,
            'used_at': order.created_at.isoformat() if order.created_at else None,
            'expires_at': None,
            'at': order.created_at.isoformat() if order.created_at else None,
        })

    items.sort(key=lambda row: row.get('at') or '', reverse=True)
    return items[:limit]
