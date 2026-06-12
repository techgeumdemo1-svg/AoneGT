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
