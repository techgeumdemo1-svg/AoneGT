"""
Single entry for registration-time Zoho email checks (Inventory vs Commerce).
"""
from django.conf import settings

from .zoho_commerce_contact import commerce_salesorders_email_exists, zoho_commerce_check_configured
from .zoho_inventory_contact import (
    ZohoContactCheckError,
    inventory_contact_email_exists,
    zoho_contact_check_configured,
)


def registration_email_check_configured() -> bool:
    """True if env vars are set for the active REGISTER_ZOHO_EMAIL_SOURCE."""
    src = getattr(settings, 'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory')
    if src == 'commerce_salesorders':
        return zoho_commerce_check_configured()
    return zoho_contact_check_configured()


def registration_email_exists_in_zoho(normalized_email: str) -> bool:
    src = getattr(settings, 'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory')
    if src == 'commerce_salesorders':
        return commerce_salesorders_email_exists(normalized_email)
    return inventory_contact_email_exists(normalized_email)


__all__ = [
    'ZohoContactCheckError',
    'registration_email_check_configured',
    'registration_email_exists_in_zoho',
]
