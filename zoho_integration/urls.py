from django.urls import path
from .views import (
    zoho_callback,
    MultiAccountZohoStoreListAPIView,
    MultiAccountZohoProductListAPIView,
    MultiAccountZohoCategoryListAPIView,
    MultiAccountZohoCategoryImageProxyAPIView,
)

urlpatterns = [
    path("callback/", zoho_callback),
    path("multi/stores/", MultiAccountZohoStoreListAPIView.as_view()),
    path("multi/accounts/<int:account_id>/products/<str:organization_id>/", MultiAccountZohoProductListAPIView.as_view()),
    path("multi/accounts/<int:account_id>/categories/<str:organization_id>/", MultiAccountZohoCategoryListAPIView.as_view()),
    path(
        "multi/accounts/<int:account_id>/categories/<str:organization_id>/<str:category_id>/image/",
        MultiAccountZohoCategoryImageProxyAPIView.as_view(),
    ),
]