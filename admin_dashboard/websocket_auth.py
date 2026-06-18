from __future__ import annotations

import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)


@database_sync_to_async
def _get_staff_user_from_token(token_str: str):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        token = AccessToken(token_str)
        user = User.objects.get(pk=token['user_id'])
    except (TokenError, User.DoesNotExist, KeyError):
        return AnonymousUser()

    if not user.is_active or not (user.is_staff or user.is_superuser):
        return AnonymousUser()
    return user


def _extract_token(scope) -> str:
    query_string = scope.get('query_string', b'').decode()
    params = parse_qs(query_string)
    token_list = params.get('token', [])
    if token_list:
        return token_list[0].strip()

    for name, value in scope.get('headers', []):
        if name.lower() != b'authorization':
            continue
        auth = value.decode().strip()
        if auth.lower().startswith('bearer '):
            return auth[7:].strip()
    return ''


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            token = _extract_token(scope)
            scope['user'] = (
                await _get_staff_user_from_token(token)
                if token
                else AnonymousUser()
            )
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
