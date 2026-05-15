from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from shop.views import RegisterDeviceView, UnregisterDeviceView, PushSettingsView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/devices/register/', RegisterDeviceView.as_view(), name='device-register'),
    path('api/devices/unregister/', UnregisterDeviceView.as_view(), name='device-unregister'),
    path('api/notifications/push-settings/', PushSettingsView.as_view(), name='notifications-push-settings'),
    path('api/catalog/', include('catalog.urls')),
    path('api/shop/', include('shop.urls')),
    path('api/internal/trigger-coupon-poll/', __import__('offer.views_internal_temp', fromlist=['TriggerCouponPollAPIView']).TriggerCouponPollAPIView.as_view(), name='internal-trigger-coupon-poll'),
    path('api/offer/', include('offer.urls')),
    path("zoho/", include("zoho_integration.urls")),
    # path("api/offers/", include("offers.urls"), name='offers'),
     path('api/admin/', include('superuser.urls')),
     
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
