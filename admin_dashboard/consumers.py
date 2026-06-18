from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

ADMIN_DASHBOARD_GROUP = 'admin_dashboard'


class AdminDashboardConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return

        await self.channel_layer.group_add(ADMIN_DASHBOARD_GROUP, self.channel_name)
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {
                    'event': 'connected',
                    'message': 'Admin dashboard realtime connected.',
                }
            )
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(ADMIN_DASHBOARD_GROUP, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data == 'ping':
            await self.send(text_data=json.dumps({'event': 'pong'}))

    async def admin_dashboard_event(self, event):
        await self.send(text_data=json.dumps(event['payload']))
