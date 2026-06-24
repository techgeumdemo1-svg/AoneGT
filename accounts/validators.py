"""Shared field validators for accounts and shop serializers."""

from __future__ import annotations

import re
import unicodedata

from django.core.exceptions import ValidationError

_PHONE_SEPARATORS_RE = re.compile(r'[\s\-\.\(\)]+')
# E.164: + and 8–15 digits (ITU-T recommendation).
_E164_RE = re.compile(r'^\+[1-9]\d{7,14}$')
# UAE mobile: 05xxxxxxxx, 5xxxxxxxx, or 9715xxxxxxxx (optional leading 0).
_UAE_LOCAL_RE = re.compile(r'^(?:0?5\d{8}|9715\d{8})$')

_NAME_MIN_LENGTH = 2
_NAME_MAX_LENGTH = 150


def normalize_phone(value: str) -> str:
    cleaned = _PHONE_SEPARATORS_RE.sub('', (value or '').strip())
    if cleaned.startswith('00'):
        cleaned = '+' + cleaned[2:]
    return cleaned


def validate_phone_number(value: str) -> str:
    """Accept E.164 (+971…) or common UAE local mobile formats; return normalized storage form."""
    cleaned = normalize_phone(value)
    if not cleaned:
        raise ValidationError('Phone number is required.')

    if _E164_RE.match(cleaned):
        return cleaned

    if _UAE_LOCAL_RE.match(cleaned):
        if cleaned.startswith('05'):
            return '+971' + cleaned[1:]
        if cleaned.startswith('5'):
            return '+971' + cleaned
        if cleaned.startswith('971'):
            return '+' + cleaned

    raise ValidationError(
        'Enter a valid phone number (e.g. +971501234567 or 0501234567).',
    )


def validate_person_name(value: str, *, field_label: str, required: bool = True) -> str:
    """Letters (any script), spaces, hyphens, apostrophes, and periods only."""
    value = (value or '').strip()
    if not value:
        if required:
            raise ValidationError(f'{field_label} is required.')
        return ''

    if len(value) < _NAME_MIN_LENGTH:
        raise ValidationError(f'{field_label} must be at least {_NAME_MIN_LENGTH} characters.')

    if len(value) > _NAME_MAX_LENGTH:
        raise ValidationError(f'{field_label} must be at most {_NAME_MAX_LENGTH} characters.')

    for ch in value:
        if ch in " -'.":
            continue
        if unicodedata.category(ch).startswith('L'):
            continue
        raise ValidationError(
            f'{field_label} may only contain letters, spaces, hyphens, apostrophes, and periods.',
        )

    return value


_ADDRESS_MIN_LENGTH = 5
_ADDRESS_MAX_LENGTH = 500
_CITY_MIN_LENGTH = 2
_CITY_MAX_LENGTH = 120
_STATE_MAX_LENGTH = 120


def validate_address_line(value: str, *, field_label: str = 'Address') -> str:
    value = (value or '').strip()
    if not value:
        raise ValidationError(f'{field_label} is required.')
    if len(value) < _ADDRESS_MIN_LENGTH:
        raise ValidationError(f'{field_label} must be at least {_ADDRESS_MIN_LENGTH} characters.')
    if len(value) > _ADDRESS_MAX_LENGTH:
        raise ValidationError(f'{field_label} must be at most {_ADDRESS_MAX_LENGTH} characters.')
    return value


def validate_city_name(value: str) -> str:
    value = (value or '').strip()
    if not value:
        raise ValidationError('City is required.')
    if len(value) < _CITY_MIN_LENGTH:
        raise ValidationError(f'City must be at least {_CITY_MIN_LENGTH} characters.')
    if len(value) > _CITY_MAX_LENGTH:
        raise ValidationError(f'City must be at most {_CITY_MAX_LENGTH} characters.')
    for ch in value:
        if ch in " -'.":
            continue
        if unicodedata.category(ch).startswith('L') or ch.isdigit():
            continue
        raise ValidationError(
            'City may only contain letters, numbers, spaces, hyphens, apostrophes, and periods.',
        )
    return value


def validate_state_name(value: str) -> str:
    value = (value or '').strip()
    if not value:
        return ''
    if len(value) > _STATE_MAX_LENGTH:
        raise ValidationError(f'State must be at most {_STATE_MAX_LENGTH} characters.')
    for ch in value:
        if ch in " -'.":
            continue
        if unicodedata.category(ch).startswith('L') or ch.isdigit():
            continue
        raise ValidationError(
            'State may only contain letters, numbers, spaces, hyphens, apostrophes, and periods.',
        )
    return value


def validate_address_type(
    value: str,
    *,
    allowed_types: set[str],
    aliases: dict[str, str] | None = None,
) -> str:
    normalized = (value or '').strip().lower()
    if aliases:
        normalized = aliases.get(normalized, normalized)
    if normalized not in allowed_types:
        allowed = ', '.join(sorted(allowed_types))
        raise ValidationError(f'Invalid address type. Choose one of: {allowed}.')
    return normalized


def drf_validation_error(exc: ValidationError):
    """Map Django ValidationError to DRF ValidationError."""
    from rest_framework import serializers

    return serializers.ValidationError(list(exc.messages))


_ADDRESS_MIN_LENGTH = 5
_ADDRESS_MAX_LENGTH = 500
_CITY_MIN_LENGTH = 2
_CITY_MAX_LENGTH = 120
_STATE_MAX_LENGTH = 120


def validate_address_line(value: str, *, field_label: str = 'Address') -> str:
    value = (value or '').strip()
    if not value:
        raise ValidationError(f'{field_label} is required.')
    if len(value) < _ADDRESS_MIN_LENGTH:
        raise ValidationError(f'{field_label} must be at least {_ADDRESS_MIN_LENGTH} characters.')
    if len(value) > _ADDRESS_MAX_LENGTH:
        raise ValidationError(f'{field_label} must be at most {_ADDRESS_MAX_LENGTH} characters.')
    return value


def validate_city_name(value: str) -> str:
    value = (value or '').strip()
    if not value:
        raise ValidationError('City is required.')
    if len(value) < _CITY_MIN_LENGTH:
        raise ValidationError(f'City must be at least {_CITY_MIN_LENGTH} characters.')
    if len(value) > _CITY_MAX_LENGTH:
        raise ValidationError(f'City must be at most {_CITY_MAX_LENGTH} characters.')
    for ch in value:
        if ch in " -'.":
            continue
        if unicodedata.category(ch).startswith('L') or ch.isdigit():
            continue
        raise ValidationError(
            'City may only contain letters, numbers, spaces, hyphens, apostrophes, and periods.',
        )
    return value


def validate_state_name(value: str) -> str:
    value = (value or '').strip()
    if not value:
        return ''
    if len(value) > _STATE_MAX_LENGTH:
        raise ValidationError(f'State must be at most {_STATE_MAX_LENGTH} characters.')
    for ch in value:
        if ch in " -'.":
            continue
        if unicodedata.category(ch).startswith('L') or ch.isdigit():
            continue
        raise ValidationError(
            'State may only contain letters, numbers, spaces, hyphens, apostrophes, and periods.',
        )
    return value


def validate_address_type(
    value: str,
    *,
    allowed_types: set[str],
    aliases: dict[str, str] | None = None,
) -> str:
    normalized = (value or '').strip().lower()
    if aliases:
        normalized = aliases.get(normalized, normalized)
    if normalized not in allowed_types:
        allowed = ', '.join(sorted(allowed_types))
        raise ValidationError(f'Invalid address type. Choose one of: {allowed}.')
    return normalized


def drf_validation_error(exc: ValidationError):
    """Map Django ValidationError to a list for DRF serializers."""
    from rest_framework import serializers

    return serializers.ValidationError(list(exc.messages))
