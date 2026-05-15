from __future__ import annotations

import logging
from typing import Any

try:
    from firebase_admin import messaging
except Exception:  # pragma: no cover - environment dependent import
    messaging = None

from shop.models import FCMDeviceToken

logger = logging.getLogger(__name__)


def _chunked(values: list[str], size: int):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _is_invalid_token_error(exc: Exception) -> bool:
    code = str(getattr(exc, 'code', '') or '').strip().lower()
    text = str(exc).strip().lower()
    haystack = f'{code} {text}'
    markers = (
        'invalid-registration-token',
        'registration-token-not-registered',
        'unregistered',
        'not-registered',
    )
    return any(marker in haystack for marker in markers)


def send_push_notification(tokens: list, title: str, body: str, data: dict) -> dict:
    """
    tokens: list of FCM token strings
    title: notification title
    body: notification body
    data: dict of string key-value pairs for deep link. Keys: type, store_slug, org_id,
          coupon_id, click_action
    Returns: {"success": int, "failure": int, "invalid_tokens": list}
    """
    result = {'success': 0, 'failure': 0, 'invalid_tokens': []}

    try:
        if messaging is None:
            logger.warning('Firebase messaging import unavailable; push notifications disabled.')
            return result

        token_list = [str(t).strip() for t in (tokens or []) if str(t).strip()]
        if not token_list:
            return result

        payload = {str(k): str(v) for k, v in (data or {}).items()}
        invalid_tokens = set()

        for chunk in _chunked(token_list, 500):
            message = messaging.MulticastMessage(
                tokens=chunk,
                notification=messaging.Notification(title=title or '', body=body or ''),
                data=payload,
            )
            response = messaging.send_each_for_multicast(message)
            result['success'] += int(response.success_count or 0)
            result['failure'] += int(response.failure_count or 0)

            for idx, item in enumerate(response.responses):
                if item.success:
                    continue
                exc = getattr(item, 'exception', None)
                if exc is not None and _is_invalid_token_error(exc):
                    invalid_tokens.add(chunk[idx])

        if invalid_tokens:
            invalid_list = list(invalid_tokens)
            FCMDeviceToken.objects.filter(token__in=invalid_list).update(is_active=False)
            result['invalid_tokens'] = invalid_list

        return result
    except Exception:
        logger.warning('Failed to send push notifications; push notifications disabled.', exc_info=True)
        return {'success': 0, 'failure': 0, 'invalid_tokens': []}
