"""Rules for who may review a catalog product."""

from __future__ import annotations

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
