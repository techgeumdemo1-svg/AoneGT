"""Loyalty math: earn rate (AED spend → points), redemption value (points → AED)."""

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from django.utils import timezone

from shop.loyalty_config import load_loyalty_config

_config_cache = None


def clear_loyalty_config_cache() -> None:
    global _config_cache
    _config_cache = None


def _loyalty_config() -> dict:
    global _config_cache
    if _config_cache is None:
        _config_cache = load_loyalty_config()
    return _config_cache


def aed_per_point_earned() -> int:
    return _loyalty_config()['aed_per_point_earned']


def point_value_aed() -> Decimal:
    return _loyalty_config()['point_value_aed']


def min_points_to_redeem() -> int:
    return _loyalty_config()['min_points_to_redeem']


def coupon_points_block() -> int:
    """Points required per coupon block (default 100)."""
    return _loyalty_config()['coupon_points_block']


def coupon_credit_aed() -> Decimal:
    """Store credit (AED) per coupon block (default 100 AED per 100 points)."""
    return _loyalty_config()['coupon_credit_aed']


def coupon_aed_for_points(points: int) -> Decimal:
    """Convert whole coupon blocks to AED credit (100 pts → 100 AED by default)."""
    block = coupon_points_block()
    blocks = int(points) // block
    return (coupon_credit_aed() * blocks).quantize(Decimal('0.01'))


def validate_points_for_coupon(points: int) -> Optional[str]:
    """
    Return an error message if points cannot be exchanged for a coupon, else None.
    """
    block = coupon_points_block()
    minimum = max(min_points_to_redeem(), block)
    if points < minimum:
        return (
            f'At least {minimum} Super Coins are required to generate a coupon '
            f'({coupon_credit_aed()} AED credit per {block} coins).'
        )
    if points % block != 0:
        return f'Redeem Super Coins in multiples of {block} (e.g. {block}, {block * 2}).'
    return None


def coupon_expiry_days() -> int:
    return _loyalty_config()['coupon_expiry_days']


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
