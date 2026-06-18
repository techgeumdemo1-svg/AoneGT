from django.urls import path

from .consumers import AdminDashboardConsumer

websocket_urlpatterns = [
    path('ws/admin/dashboard/', AdminDashboardConsumer.as_asgi()),
]
