"""
Delivery zone fee calculation service.

City matching is case-insensitive and strips surrounding whitespace.
Unknown cities fall back to DEFAULT_SHIPPING_AMOUNT from Django settings.

COD surcharge ONLY applies to payment_method == 'cash_on_delivery'.
All other payment methods (card_on_delivery, pay_by_link, payment_gateway)
do NOT get the surcharge.
"""
from decimal import Decimal

from django.conf import settings

from shop.models import DeliveryZone

# Only cash_on_delivery triggers COD surcharge.
# card_on_delivery, pay_by_link, payment_gateway do NOT.
COD_PAYMENT_METHODS = {'cash_on_delivery'}


def get_shipping_fee(city: str, subtotal: Decimal, payment_method: str) -> Decimal:
    """
    Calculate the delivery fee for an order.

    Args:
        city: The customer's shipping city (from UserAddress or inline checkout field).
        subtotal: The order subtotal before shipping (AED).
        payment_method: The Order.PaymentMethod value string.
                        One of: cash_on_delivery, card_on_delivery,
                        pay_by_link, payment_gateway.

    Returns:
        The total shipping fee as a Decimal.
        - If city matches a zone: applies zone fee + COD surcharge if applicable.
        - If city is blank: returns Decimal('0') (no address entered yet).
        - If city matches no zone: falls back to DEFAULT_SHIPPING_AMOUNT from settings.
    """
    breakdown = get_shipping_fee_breakdown(city, subtotal, payment_method)
    return breakdown['total']


def get_shipping_fee_breakdown(city: str, subtotal: Decimal, payment_method: str) -> dict:
    """
    Same as get_shipping_fee() but returns a detailed dict with all components:

    Returns a dict with keys:
        total           - Decimal: total shipping amount charged
        delivery_fee    - Decimal: base zone delivery fee (0 if free delivery threshold met)
        cod_surcharge   - Decimal: COD surcharge applied (0 if not COD or not applicable)
        is_free         - bool: True when free delivery threshold was met
        zone_name       - str | None: matched zone name, None if unknown city or no city
        estimated_delivery_label - str: zone estimated delivery label (empty string if none)
    """
    if not city:
        return {
            'total': Decimal('0'),
            'delivery_fee': Decimal('0'),
            'cod_surcharge': Decimal('0'),
            'is_free': False,
            'zone_name': None,
            'estimated_delivery_label': '',
        }

    city_clean = city.strip().lower()
    zone = _find_zone_for_city(city_clean)

    if zone is None:
        # Unknown city - use DEFAULT_SHIPPING_AMOUNT as fallback
        default_fee = Decimal(str(getattr(settings, 'DEFAULT_SHIPPING_AMOUNT', '0')))
        surcharge = Decimal('0')
        if payment_method in COD_PAYMENT_METHODS and default_fee > 0:
            surcharge = Decimal('10')
        return {
            'total': default_fee + surcharge,
            'delivery_fee': default_fee,
            'cod_surcharge': surcharge,
            'is_free': False,
            'zone_name': None,
            'estimated_delivery_label': '',
        }

    # Free delivery when subtotal meets or exceeds threshold
    is_free = subtotal >= zone.free_delivery_threshold
    delivery_fee = Decimal('0') if is_free else zone.delivery_fee

    # COD surcharge ONLY for cash_on_delivery - not for card_on_delivery/pay_by_link/payment_gateway
    surcharge = zone.cod_surcharge if payment_method in COD_PAYMENT_METHODS else Decimal('0')

    return {
        'total': delivery_fee + surcharge,
        'delivery_fee': delivery_fee,
        'cod_surcharge': surcharge,
        'is_free': is_free,
        'zone_name': zone.name,
        'estimated_delivery_label': zone.estimated_delivery_label or '',
    }


def get_zone_info(city: str) -> dict | None:
    """
    Returns zone info dict for display purposes (e.g., estimated delivery label).
    Returns None if no zone matches.
    """
    if not city:
        return None
    city_clean = city.strip().lower()
    zone = _find_zone_for_city(city_clean)
    if zone is None:
        return None
    return {
        'zone_id': zone.pk,
        'zone_name': zone.name,
        'delivery_fee': str(zone.delivery_fee),
        'cod_surcharge': str(zone.cod_surcharge),
        'free_delivery_threshold': str(zone.free_delivery_threshold),
        'estimated_delivery_label': zone.estimated_delivery_label,
    }


def _find_zone_for_city(city_lower: str) -> DeliveryZone | None:
    """
    Find the first active DeliveryZone whose cities list contains the given city.
    Matching is case-insensitive. Returns None if no zone matches.
    Queries only active zones.
    """
    for zone in DeliveryZone.objects.filter(is_active=True).only(
        'pk',
        'name',
        'cities',
        'free_delivery_threshold',
        'delivery_fee',
        'cod_surcharge',
        'estimated_delivery_label',
    ):
        if any(c.strip().lower() == city_lower for c in zone.cities):
            return zone
    return None
