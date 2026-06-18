"""Verify admin dashboard WebSocket auth and broadcast delivery."""

from __future__ import annotations

from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework_simplejwt.tokens import AccessToken

from admin_dashboard.realtime import broadcast_admin_dashboard_event
from aonegt.asgi import application

User = get_user_model()


class Command(BaseCommand):
    help = (
        'Connect to ws/admin/dashboard/ as a staff user, send a test event, '
        'and confirm the WebSocket client receives it.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            help='Staff user email for JWT (default: first active staff user).',
        )

    def handle(self, *args, **options):
        async_to_sync(self._run_test)(options.get('email'))

    async def _run_test(self, email: str | None):
        user = await sync_to_async(self._resolve_staff_user)(email)
        token = await sync_to_async(lambda: str(AccessToken.for_user(user)))()
        path = f'/ws/admin/dashboard/?token={token}'

        communicator = WebsocketCommunicator(application, path)
        connected, status = await communicator.connect()
        if not connected:
            raise CommandError(f'WebSocket connection failed (HTTP {status}).')

        hello = await communicator.receive_json_from()
        if hello.get('event') != 'connected':
            raise CommandError(f'Unexpected connect payload: {hello!r}')

        test_payload = {
            'event': 'dashboard.test',
            'message': 'Admin realtime test event.',
            'refresh': ['dashboard.summary'],
        }
        await sync_to_async(broadcast_admin_dashboard_event)(test_payload)
        received = await communicator.receive_json_from()

        await communicator.disconnect()

        if received != test_payload:
            raise CommandError(f'Broadcast payload mismatch.\nExpected: {test_payload}\nGot: {received}')

        self.stdout.write(self.style.SUCCESS('Admin dashboard realtime OK.'))
        self.stdout.write(f'  Staff user: {user.email}')
        self.stdout.write(f'  WebSocket: ws://127.0.0.1:8000{path}')
        self.stdout.write(f'  Connect event: {hello}')
        self.stdout.write(f'  Test event: {received}')

    def _resolve_staff_user(self, email: str | None):
        qs = User.objects.filter(is_active=True).filter(is_staff=True) | User.objects.filter(
            is_active=True,
            is_superuser=True,
        )
        if email:
            user = qs.filter(email__iexact=email.strip()).first()
            if user is None:
                raise CommandError(f'No active staff user with email {email!r}.')
            return user

        user = qs.order_by('id').first()
        if user is None:
            raise CommandError('No staff user found. Create one with is_staff=True first.')
        return user
