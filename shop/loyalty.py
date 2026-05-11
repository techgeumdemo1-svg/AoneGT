"""Loyalty math: earn rate (AED spend → points), redemption value (points → AED)."""

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.utils import timezone


def aed_per_point_earned() -> int:
    v = int(getattr(settings, 'LOYALTY_AED_PER_POINT_EARNED', 100))
    return max(1, v)


def point_value_aed() -> Decimal:
    return Decimal(str(getattr(settings, 'LOYALTY_POINT_VALUE_AED', '1')))


def min_points_to_redeem() -> int:
    return max(0, int(getattr(settings, 'LOYALTY_MIN_POINTS_TO_REDEEM', 100)))


def coupon_expiry_days() -> int:
    return max(1, int(getattr(settings, 'LOYALTY_COUPON_EXPIRY_DAYS', 90)))


def points_earned_for_purchase(final_total: Decimal, currency: str) -> int:
    """1 point per `aed_per_point_earned()` AED of final paid total (after loyalty discount)."""
    if (currency or '').upper() != 'AED':
        return 0
    step = aed_per_point_earned()
    spent = int(final_total.quantize(Decimal('1'), rounding=ROUND_DOWN))
    return max(0, spent // step)


def max_points_redeemable_for_total(gross_total: Decimal, point_value: Decimal) -> int:
    """Whole points such that discount does not exceed order total."""
    if gross_total <= 0 or point_value <= 0:
        return 0
    max_aed = gross_total / point_value
    return int(max_aed.quantize(Decimal('1'), rounding=ROUND_DOWN))


def default_coupon_expires_at():
    return timezone.now() + timedelta(days=coupon_expiry_days())
