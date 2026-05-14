"""
TEMPORARY FILE — FOR TESTING ONLY

This file is temporary — for testing only.
To remove: delete this file and remove its URL entry from urls.py.
POLL_TRIGGER_SECRET must be set in Render's environment variables before use,
and should be deleted from Render's dashboard after testing is complete.
"""
from __future__ import annotations

import logging
import os

from django.core.management import call_command
from django.http import JsonResponse
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class TriggerCouponPollAPIView(APIView):
    """Synchronous, temporary endpoint to trigger `poll_zoho_coupons`.

    Security: requires header `X-Poll-Secret` matching env `POLL_TRIGGER_SECRET`.
    No authentication is used (this endpoint is intentionally callable without JWT).
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request, *args, **kwargs):
        secret_env = os.environ.get('POLL_TRIGGER_SECRET')
        header = (request.headers.get('X-Poll-Secret') or '').strip()

        if not secret_env or not header or header != secret_env:
            return JsonResponse({'status': 'error', 'message': 'forbidden'}, status=403)

        try:
            # Run synchronously as requested. This will execute management command
            # `offer/management/commands/poll_zoho_coupons.py` in-process.
            call_command('poll_zoho_coupons')
            return JsonResponse({'status': 'ok', 'message': 'poll triggered successfully'}, status=200)
        except Exception as exc:  # catch-all for visibility in this temporary endpoint
            logger.exception('Failed to run poll_zoho_coupons via internal trigger')
            return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)


__all__ = ['TriggerCouponPollAPIView']
