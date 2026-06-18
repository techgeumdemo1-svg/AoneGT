"""DB-backed Super Coins settings with env fallbacks."""

from decimal import Decimal
from typing import Any, Optional

from django.conf import settings


def defaults_from_django_settings() -> dict[str, Any]:
    return {
        'aed_per_point_earned': max(1, int(getattr(settings, 'LOYALTY_AED_PER_POINT_EARNED', 100))),
        'point_value_aed': Decimal(str(getattr(settings, 'LOYALTY_POINT_VALUE_AED', '1'))),
        'min_points_to_redeem': max(0, int(getattr(settings, 'LOYALTY_MIN_POINTS_TO_REDEEM', 100))),
        'coupon_points_block': max(1, int(getattr(settings, 'LOYALTY_COUPON_POINTS_BLOCK', 100))),
        'coupon_credit_aed': Decimal(str(getattr(settings, 'LOYALTY_COUPON_CREDIT_AED', '100'))),
        'coupon_expiry_days': max(1, int(getattr(settings, 'LOYALTY_COUPON_EXPIRY_DAYS', 90))),
    }


def get_loyalty_program_settings():
    from shop.models import LoyaltyProgramSettings

    return LoyaltyProgramSettings.objects.get_or_create(
        pk=1,
        defaults=defaults_from_django_settings(),
    )[0]


def load_loyalty_config() -> dict[str, Any]:
    row = get_loyalty_program_settings()
    return {
        'aed_per_point_earned': max(1, int(row.aed_per_point_earned)),
        'point_value_aed': Decimal(str(row.point_value_aed)),
        'min_points_to_redeem': max(0, int(row.min_points_to_redeem)),
        'coupon_points_block': max(1, int(row.coupon_points_block)),
        'coupon_credit_aed': Decimal(str(row.coupon_credit_aed)),
        'coupon_expiry_days': max(1, int(row.coupon_expiry_days)),
        'updated_at': row.updated_at,
    }


_PATCH_FIELD_TO_MODEL = {
    'coupon_credit_aed_per_block': 'coupon_credit_aed',
}


def update_loyalty_program_settings(validated_data: dict, *, user=None):
    from shop.models import LoyaltyProgramSettings

    row = get_loyalty_program_settings()
    update_fields = ['updated_at']
    for key, value in validated_data.items():
        attr = _PATCH_FIELD_TO_MODEL.get(key, key)
        if not hasattr(row, attr):
            continue
        setattr(row, attr, value)
        update_fields.append(attr)
    if user is not None and getattr(user, 'is_authenticated', False):
        row.updated_by = user
        update_fields.append('updated_by')
    row.save(update_fields=update_fields)
    return row
