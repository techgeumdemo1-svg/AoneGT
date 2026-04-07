"""
Single entry for registration-time Zoho email checks (Inventory vs Commerce).

Commerce mode looks up the email on Zoho Commerce **sales orders** (API filter `email`),
not a generic “customer exists” endpoint. Aliases: commerce, zoho_commerce, commerce_salesorders.
"""
from django.conf import settings

from .zoho_commerce_contact import commerce_salesorders_email_exists, zoho_commerce_check_configured
from .zoho_inventory_contact import (
    ZohoContactCheckError,
    inventory_contact_email_exists,
    zoho_contact_check_configured,
)


def resolved_register_zoho_email_source() -> str:
    """Return 'commerce_salesorders' or 'inventory' from REGISTER_ZOHO_EMAIL_SOURCE."""
    raw = (getattr(settings, 'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory') or 'inventory').strip().lower()
    if raw in ('commerce_salesorders', 'commerce', 'zoho_commerce'):
        return 'commerce_salesorders'
    return 'inventory'


def registration_email_check_configured() -> bool:
    """True if env vars are set for the active REGISTER_ZOHO_EMAIL_SOURCE."""
    if resolved_register_zoho_email_source() == 'commerce_salesorders':
        return zoho_commerce_check_configured()
    return zoho_contact_check_configured()


def registration_email_exists_in_zoho(normalized_email: str) -> bool:
    if resolved_register_zoho_email_source() == 'commerce_salesorders':
        return commerce_salesorders_email_exists(normalized_email)
    return inventory_contact_email_exists(normalized_email)


__all__ = [
    'ZohoContactCheckError',
    'registration_email_check_configured',
    'registration_email_exists_in_zoho',
    'resolved_register_zoho_email_source',
]
