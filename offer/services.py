from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from catalog.models import Store
from shop.models import Cart, FCMDeviceToken, UserNotification
from shop.services.notifications import create_user_notification
from shop.services.push_notifications import send_push_notification
from shop.services.zoho_commerce import ZohoCommerceService

from .models import Coupon, CouponUsageLog

logger = logging.getLogger(__name__)


def _notify_new_coupon(coupon: Coupon, org_id: int) -> None:
    store = Store.objects.filter(zoho_org_id=str(org_id), is_active=True).first()
    if store is None:
        return

    coupon_name = (coupon.coupon_name or '').strip() or 'Offer'
    title = f'{store.name} — New offer: {coupon_name}'
    title = title[:100]
    body = (coupon.description or '')[:200] if coupon.description else coupon_name

    User = get_user_model()
    active_users = User.objects.filter(is_active=True)
    for user in active_users:
        try:
            create_user_notification(
                user=user,
                kind=UserNotification.Kind.OFFER,
                title=title,
                body=body,
            )
        except Exception:
            logger.exception(
                'Failed creating in-app coupon notification for user %s and coupon %s',
                user.pk,
                coupon.coupon_id,
            )

    try:
        token_list = list(
            FCMDeviceToken.objects.filter(is_active=True, push_enabled=True)
            .values_list('token', flat=True),
        )
        data_payload = {
            'type': 'new_coupon',
            'store_slug': str(store.slug or ''),
            'org_id': str(store.zoho_org_id or ''),
            'coupon_id': str(coupon.coupon_id or ''),
            'click_action': 'OPEN_STORE',
        }
        send_push_notification(
            tokens=token_list,
            title=title,
            body=body,
            data=data_payload,
        )
    except Exception:
        logger.exception(
            'Failed sending push coupon notification for coupon %s and org %s',
            coupon.coupon_id,
            org_id,
        )


def _as_decimal(value: Any, default: str = '0') -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal(default).quantize(Decimal('0.01'))


def get_store_org_id(store: Store) -> int:
    raw = (getattr(store, 'zoho_org_id', '') or '').strip()
    if not raw:
        raw = (getattr(settings, 'ZOHO_COMMERCE_ORGANIZATION_ID', '') or '').strip()
    return int(raw)


def _zoho_api_base_host() -> str:
    return (getattr(settings, 'ZOHO_API_BASE_HOST', '') or 'https://www.zohoapis.com').rstrip('/')


def get_user_store_cart(user, store: Store):
    cart = Cart.objects.filter(user=user).prefetch_related('items__product', 'items__store').first()
    if not cart:
        return None, []
    items = list(cart.items.filter(store_id=store.pk).select_related('product', 'store'))
    return cart, items


def cart_item_snapshot(item) -> dict[str, Any]:
    product = item.product
    return {
        'item_id': item.pk,
        'product_id': str(getattr(product, 'zoho_product_id', '') or '') if product else None,
        'category_id': str(getattr(product, 'zoho_category_id', '') or '') if product else '',
        'collection_id': str(getattr(product, 'zoho_collection_id', '') or '') if product else '',
        'quantity': int(item.quantity or 0),
        'unit_price': _as_decimal(product.price if product else '0'),
        'line_total': _as_decimal(product.price if product else '0') * int(item.quantity or 0),
    }


def get_cart_context(user, store: Store):
    cart, items = get_user_store_cart(user, store)
    snapshots = [cart_item_snapshot(item) for item in items]
    subtotal = sum((row['line_total'] for row in snapshots), Decimal('0')).quantize(Decimal('0.01'))
    return cart, snapshots, subtotal


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ('items', 'values', 'data', 'products', 'categories', 'collections'):
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
    return []


def _coupon_customer_allowed(coupon: Coupon, user) -> bool:
    customers = _json_dict(coupon.eligible_customers)
    if not customers:
        return True
    if customers.get('apply_to_all_customers'):
        return True
    rows = customers.get('customers') or customers.get('customer_ids') or []
    if not rows:
        return True
    user_id = str(getattr(user, 'pk', '') or '').strip()
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            candidate = str(row.get('user_id') or row.get('id') or row.get('customer_id') or '').strip()
        else:
            candidate = str(row).strip()
        if candidate and candidate == user_id:
            return True
    return False


def _is_active_limit(value: Any) -> bool:
    try:
        return int(value or 0) > 0
    except Exception:
        return False


def _quantity_in_items(items: list[dict[str, Any]], *, product_ids=None, categories=None, collections=None) -> int:
    product_ids = {str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip() for x in (product_ids or []) if str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip()}
    categories = {str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip() for x in (categories or []) if str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip()}
    collections = {str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip() for x in (collections or []) if str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip()}
    total = 0
    for item in items:
        item_product_id = str(item.get('product_id') or '').strip()
        item_category = str(item.get('category_id') or '').strip()
        item_collection = str(item.get('collection_id') or '').strip()
        matched = False
        if product_ids and item_product_id in product_ids:
            matched = True
        if categories and item_category in categories:
            matched = True
        if collections and item_collection in collections:
            matched = True
        if not (product_ids or categories or collections):
            matched = True
        if matched:
            total += int(item.get('quantity') or 0)
    return total


def _matched_line_total(items: list[dict[str, Any]], *, product_ids=None, categories=None, collections=None) -> Decimal:
    product_ids = {str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip() for x in (product_ids or []) if str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip()}
    categories = {str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip() for x in (categories or []) if str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip()}
    collections = {str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip() for x in (collections or []) if str((x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or x.get('zs_product_id') or x) if isinstance(x, dict) else x).strip()}
    total = Decimal('0')
    for item in items:
        item_product_id = str(item.get('product_id') or '').strip()
        item_category = str(item.get('category_id') or '').strip()
        item_collection = str(item.get('collection_id') or '').strip()
        matched = False
        if product_ids and item_product_id in product_ids:
            matched = True
        if categories and item_category in categories:
            matched = True
        if collections and item_collection in collections:
            matched = True
        if not (product_ids or categories or collections):
            matched = True
        if matched:
            total += _as_decimal(item.get('line_total', '0'))
    return total.quantize(Decimal('0.01'))


def coupon_is_applicable(coupon: Coupon, user, cart_items: list[dict[str, Any]], subtotal: Decimal) -> tuple[bool, str]:
    now = timezone.now()
    if not coupon.is_active:
        return False, 'This coupon is inactive.'
    if coupon.activation_time and coupon.activation_time > now:
        return False, 'This coupon is not active yet.'
    if coupon.expiry_time and coupon.expiry_time <= now:
        return False, 'This coupon has expired.'
    if coupon.restrict_for_guest_user and not getattr(user, 'is_authenticated', False):
        return False, 'This coupon is not available to guest users.'
    if not _coupon_customer_allowed(coupon, user):
        return False, 'This coupon is not applicable to your account.'
    if _is_active_limit(coupon.max_redemption_count) and coupon.redemption_count >= coupon.max_redemption_count:
        return False, 'This coupon has reached its redemption limit.'
    if _is_active_limit(coupon.max_redemption_count_per_user):
        used_count = CouponUsageLog.objects.filter(user_id=user.pk, coupon=coupon).count()
        if used_count >= coupon.max_redemption_count_per_user:
            return False, 'This coupon has already been used the maximum number of times for this user.'
    if coupon.minimum_order_value and subtotal < coupon.minimum_order_value:
        return False, 'Cart total does not meet the minimum order value.'

    if (coupon.coupon_type or '').lower() == 'item':
        eligible_products = _json_dict(coupon.eligible_products)
        if not (
            _json_list(eligible_products.get('products'))
            or _json_list(eligible_products.get('categories'))
            or _json_list(eligible_products.get('collections'))
        ):
            return True, ''
        if not cart_items:
            return True, ''
        if _matched_line_total(
            cart_items,
            product_ids=_json_list(eligible_products.get('products')),
            categories=_json_list(eligible_products.get('categories')),
            collections=_json_list(eligible_products.get('collections')),
        ) <= Decimal('0'):
            return False, 'Coupon not applicable to your cart.'

    elif (coupon.coupon_type or '').lower() == 'buyxgety':
        buy_products = _json_dict(coupon.buy_products)
        buy_qty = int(buy_products.get('quantity') or 0)
        if buy_qty > 0:
            if _quantity_in_items(
                cart_items,
                product_ids=_json_list(buy_products.get('products')),
                categories=_json_list(buy_products.get('categories')),
                collections=_json_list(buy_products.get('collections')),
            ) < buy_qty:
                return False, 'Coupon not applicable to your cart.'

    return True, ''


def calculate_coupon_discount(coupon: Coupon, cart_items: list[dict[str, Any]], subtotal: Decimal, shipping_amount: Decimal, currency: str) -> Decimal:
    discount_amounts = coupon.discount_amounts if isinstance(coupon.discount_amounts, list) else []
    max_discount_amount = _as_decimal(coupon.max_discount_amount or '0') if coupon.max_discount_amount else Decimal('0')
    coupon_type = (coupon.coupon_type or '').lower()
    discount_type = (coupon.discount_type or '').lower()

    def _discount_from_amounts() -> Decimal:
        currency_code = (currency or '').strip().upper()
        for row in discount_amounts:
            if isinstance(row, dict):
                row_currency = str(row.get('currency') or row.get('currency_code') or row.get('code') or '').strip().upper()
                if currency_code and row_currency == currency_code:
                    value = row.get('discount_value') or row.get('amount') or row.get('value') or row.get('discount_amount')
                    if value not in (None, ''):
                        return _as_decimal(value)
        for row in discount_amounts:
            if isinstance(row, dict):
                value = row.get('discount_value') or row.get('amount') or row.get('value') or row.get('discount_amount')
                if value not in (None, ''):
                    return _as_decimal(value)
            elif row not in (None, ''):
                return _as_decimal(row)
        return Decimal('0.00')

    if coupon_type == 'free_shipping':
        return shipping_amount.quantize(Decimal('0.01'))

    if coupon_type == 'transaction':
        if discount_type == 'percentage':
            discount = (subtotal * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
            if max_discount_amount > Decimal('0'):
                discount = min(discount, max_discount_amount)
            return discount.quantize(Decimal('0.01'))
        return _discount_from_amounts().quantize(Decimal('0.01'))

    if coupon_type == 'item':
        eligible_products = _json_dict(coupon.eligible_products)
        eligible_total = _matched_line_total(
            cart_items,
            product_ids=_json_list(eligible_products.get('products')),
            categories=_json_list(eligible_products.get('categories')),
            collections=_json_list(eligible_products.get('collections')),
        )
        if discount_type == 'percentage':
            discount = (eligible_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
            if max_discount_amount > Decimal('0'):
                discount = min(discount, max_discount_amount)
            return discount.quantize(Decimal('0.01'))
        return _discount_from_amounts().quantize(Decimal('0.01'))

    if coupon_type == 'buyxgety':
        get_products = _json_dict(coupon.get_products)
        eligible_total = _matched_line_total(cart_items, product_ids=_json_list(get_products.get('products')))
        if discount_type == 'percentage':
            discount = (eligible_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
            if max_discount_amount > Decimal('0'):
                discount = min(discount, max_discount_amount)
            return discount.quantize(Decimal('0.01'))
        return _discount_from_amounts().quantize(Decimal('0.01'))

    return Decimal('0.00')


def serialize_coupon(coupon: Coupon) -> dict[str, Any]:
    return {
        'coupon_id': coupon.coupon_id,
        'coupon_code': coupon.coupon_code,
        'coupon_name': coupon.coupon_name,
        'description': coupon.description,
        'coupon_type': coupon.coupon_type,
        'rule_type': coupon.rule_type,
        'discount_type': coupon.discount_type,
        'discount_value': coupon.discount_value,
        'discount_amounts': coupon.discount_amounts,
        'minimum_order_value': str(coupon.minimum_order_value) if coupon.minimum_order_value is not None else '',
        'max_discount_amount': coupon.max_discount_amount,
        'expiry_time': coupon.expiry_time,
    }


def cleanup_expired_coupons(org_id: int) -> int:
    now = timezone.now()
    deleted, _ = Coupon.objects.filter(org_id=org_id, expiry_time__isnull=False, expiry_time__lte=now).delete()
    return deleted


def get_applicable_coupons_for_store(user, store: Store) -> dict[str, list[dict[str, Any]]]:
    org_id = get_store_org_id(store)
    cleanup_expired_coupons(org_id)
    _cart, cart_items, subtotal = get_cart_context(user, store)
    coupons = Coupon.objects.filter(org_id=org_id, is_active=True)
    manual: list[dict[str, Any]] = []
    auto: list[dict[str, Any]] = []
    for coupon in coupons:
        allowed, _reason = coupon_is_applicable(coupon, user, cart_items, subtotal)
        if not allowed:
            continue
        payload = serialize_coupon(coupon)
        if (coupon.rule_type or '').lower() == 'manual':
            manual.append(payload)
        else:
            auto.append(payload)
    return {'manual_coupons': manual, 'auto_applied_coupons': auto}


def get_coupon_for_checkout(store: Store, coupon_code: str) -> Coupon | None:
    org_id = get_store_org_id(store)
    return Coupon.objects.filter(org_id=org_id, coupon_code__iexact=coupon_code.strip()).first()


def get_live_coupon_for_checkout(store: Store, coupon: Coupon) -> dict[str, Any]:
    url = f'{_zoho_api_base_host()}/commerce/v1/coupons/{coupon.coupon_id}'
    headers = ZohoCommerceService.admin_headers(store)
    response = requests.get(
        url,
        headers=headers,
        params={'organization_id': str(get_store_org_id(store))},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data.get('coupon') or data.get('data') or data if isinstance(data, dict) else {}


def sync_coupon_from_payload(store: Store, payload: dict[str, Any]) -> Coupon | None:
    if not isinstance(payload, dict):
        return None
    coupon_id = str(payload.get('coupon_id') or payload.get('id') or '').strip()
    if not coupon_id:
        return None
    org_id = get_store_org_id(store)
    if not bool(payload.get('is_active')):
        Coupon.objects.filter(coupon_id=coupon_id, org_id=org_id).delete()
        return None
    coupon, created = Coupon.objects.get_or_create(coupon_id=coupon_id, org_id=org_id)
    coupon.couponset_id = str(payload.get('couponset_id') or payload.get('coupon_set_id') or '').strip()
    coupon.coupon_name = str(payload.get('coupon_name') or payload.get('name') or '').strip()
    coupon.coupon_code = str(payload.get('coupon_code') or payload.get('code') or '').strip()
    coupon.description = str(payload.get('description') or '').strip()
    coupon.is_active = True
    coupon.status = str(payload.get('status') or '').strip()
    coupon.rule_type = str(payload.get('rule_type') or '').strip()
    coupon.coupon_type = str(payload.get('coupon_type') or payload.get('type') or '').strip()
    coupon.show_in_storefront = bool(payload.get('show_in_storefront'))
    coupon.restrict_for_guest_user = bool(payload.get('restrict_for_guest_user'))
    coupon.restrict_for_offline_payments = bool(payload.get('restrict_for_offline_payments'))
    coupon.stop_after_this_rule = bool(payload.get('stop_after_this_rule'))
    coupon.apply_once_per_order = bool(payload.get('apply_once_per_order'))
    coupon.type = str(payload.get('type') or '').strip()
    coupon.duration = str(payload.get('duration') or '').strip()
    coupon.discount_type = str(payload.get('discount_type') or '').strip()
    coupon.discount_by = str(payload.get('discount_by') or '').strip()
    coupon.apply_on = str(payload.get('apply_on') or '').strip()
    coupon.discount_value = str(payload.get('discount_value') or '').strip()
    coupon.discount_amounts = payload.get('discount_amounts') or []
    coupon.max_discount_amount = str(payload.get('max_discount_amount') or '').strip()
    coupon.max_redemption = int(payload.get('max_redemption') or 0)
    coupon.max_redemption_count = int(payload.get('max_redemption_count') or 0)
    if created:
        coupon.redemption_count = int(payload.get('redemption_count') or 0)
    coupon.max_redemption_count_per_user = int(payload.get('max_redemption_count_per_user') or 0)
    coupon.max_usage_per_transaction = int(payload.get('max_usage_per_transaction') or 0)
    coupon.max_discounted_product_count_per_cart = str(payload.get('max_discounted_product_count_per_cart') or '').strip()
    min_order = payload.get('minimum_order_value')
    coupon.minimum_order_value = None if min_order in (None, '') else _as_decimal(min_order).quantize(Decimal('0.001'))
    coupon.minimum_order_quantity = str(payload.get('minimum_order_quantity') or '').strip()
    activation_time = payload.get('activation_time') or payload.get('activation_time_utc')
    if activation_time not in (None, ''):
        try:
            coupon.activation_time = timezone.datetime.fromisoformat(str(activation_time).replace('Z', '+00:00'))
            if timezone.is_naive(coupon.activation_time):
                coupon.activation_time = timezone.make_aware(coupon.activation_time, timezone.get_current_timezone())
        except Exception:
            coupon.activation_time = None
    else:
        coupon.activation_time = None
    coupon.expiry_at = str(payload.get('expiry_at') or '').strip()
    expiry_time = payload.get('expiry_time') or payload.get('expires_at')
    if expiry_time not in (None, ''):
        try:
            coupon.expiry_time = timezone.datetime.fromisoformat(str(expiry_time).replace('Z', '+00:00'))
            if timezone.is_naive(coupon.expiry_time):
                coupon.expiry_time = timezone.make_aware(coupon.expiry_time, timezone.get_current_timezone())
        except Exception:
            coupon.expiry_time = None
    else:
        coupon.expiry_time = None
    coupon.eligible_products = payload.get('eligible_products') or {}
    coupon.buy_products = payload.get('buy_products') or {}
    coupon.get_products = payload.get('get_products') or {}
    coupon.eligible_customers = payload.get('eligible_customers') or {}
    coupon.eligible_shipping_zones = payload.get('eligible_shipping_zones') or {}
    coupon.raw_data = payload
    coupon.save()
    if created:
        try:
            _notify_new_coupon(coupon, org_id)
        except Exception:
            logger.exception(
                'Failed notifying users for newly created coupon %s',
                coupon.coupon_id,
            )
    return coupon


def sync_zoho_coupons_for_store(store: Store) -> dict[str, int]:
    org_id = get_store_org_id(store)
    url = f'{_zoho_api_base_host()}/commerce/v1/coupons'
    headers = ZohoCommerceService.admin_headers(store)
    response = requests.get(
        url,
        headers=headers,
        params={'organization_id': str(org_id)},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    rows = data.get('coupons') or data.get('data') or data.get('items') or [] if isinstance(data, dict) else []
    live_ids = set()
    synced = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        live_id = str(row.get('coupon_id') or row.get('id') or '').strip()
        if live_id:
            live_ids.add(live_id)
        try:
            detail_payload = get_live_coupon_for_checkout(store, Coupon(coupon_id=live_id, org_id=org_id, coupon_code='')) if live_id else row
            if isinstance(detail_payload, dict) and detail_payload:
                row = detail_payload
            coupon = sync_coupon_from_payload(store, row)
            if coupon is not None:
                synced += 1
        except Exception:
            logger.exception('Failed to sync coupon row for org %s', org_id)
    now = timezone.now()
    expired_deleted, _ = Coupon.objects.filter(org_id=org_id, expiry_time__isnull=False, expiry_time__lte=now).delete()
    missing_deleted, _ = Coupon.objects.filter(org_id=org_id).exclude(coupon_id__in=live_ids).delete()
    return {'synced': synced, 'expired_deleted': expired_deleted, 'missing_deleted': missing_deleted}


def increment_coupon_usage(coupon: Coupon, order_id: int, user_id: int, discount_amount: Decimal) -> CouponUsageLog:
    with transaction.atomic():
        coupon = Coupon.objects.select_for_update().get(pk=coupon.pk)
        coupon.redemption_count = int(coupon.redemption_count or 0) + 1
        coupon.save(update_fields=['redemption_count'])
        return CouponUsageLog.objects.create(
            user_id=user_id,
            coupon=coupon,
            coupon_id_str=coupon.coupon_id,
            coupon_code=coupon.coupon_code,
            org_id=coupon.org_id,
            order_id=order_id,
            discount_amount_applied=discount_amount,
            coupon_type=coupon.coupon_type,
            discount_type=coupon.discount_type,
        )


def patch_coupon_redemption_count(store: Store, coupon: Coupon, redemption_count: int) -> None:
    url = f'{_zoho_api_base_host()}/commerce/v1/coupons/{coupon.coupon_id}'
    headers = ZohoCommerceService.admin_headers(store)
    response = requests.patch(
        url,
        headers={**headers, 'Content-Type': 'application/json'},
        params={'organization_id': str(get_store_org_id(store))},
        json={'redemption_count': redemption_count},
        timeout=30,
    )
    response.raise_for_status()
