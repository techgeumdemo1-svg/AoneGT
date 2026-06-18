import os

import django
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aonegt.settings')
django.setup()

from admin_dashboard.routing import websocket_urlpatterns  # noqa: E402
from admin_dashboard.websocket_auth import JWTAuthMiddlewareStack  # noqa: E402

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': JWTAuthMiddlewareStack(
            URLRouter(websocket_urlpatterns),
        ),
    },
)
