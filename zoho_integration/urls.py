from django.urls import path
from .views import (
    zoho_callback,
    MultiAccountZohoStoreListAPIView,
    MultiAccountZohoProductListAPIView,
    MultiAccountZohoProductListQueryAPIView,
    MultiAccountZohoProductSearchAPIView,
    MultiAccountZohoProductDetailQueryAPIView,
    MultiAccountZohoCategoryListQueryAPIView,
    MultiAccountZohoCategoryListAonegtGroceryQueryAPIView,
    MultiAccountZohoSubCategoryListQueryAPIView,
    MultiAccountZohoCategorySearchAPIView,
    MultiAccountZohoCategoryImageProxyAPIView,
    MultiAccountZohoCategoryImageQueryAPIView,
    MultiAccountZohoBestDealsAPIView,
)

urlpatterns = [
    path("callback/", zoho_callback),
    path("multi/stores/", MultiAccountZohoStoreListAPIView.as_view()),
    path("multi/products/", MultiAccountZohoProductListQueryAPIView.as_view()),
    path("multi/products/search/", MultiAccountZohoProductSearchAPIView.as_view()),
    path("multi/best-deals/", MultiAccountZohoBestDealsAPIView.as_view()),
    path("multi/product-detail/", MultiAccountZohoProductDetailQueryAPIView.as_view()),
    path(
        "multi/categories/aonegt-grocery/",
        MultiAccountZohoCategoryListAonegtGroceryQueryAPIView.as_view(),
    ),
    path("multi/categories/", MultiAccountZohoCategoryListQueryAPIView.as_view()),
    path("multi/subcategories/", MultiAccountZohoSubCategoryListQueryAPIView.as_view()),
    path("multi/categories/search/", MultiAccountZohoCategorySearchAPIView.as_view()),
    path(
        "multi/categories/image/",
        MultiAccountZohoCategoryImageQueryAPIView.as_view(),
        name="zoho-multi-category-image-query",
    ),
    path("multi/accounts/<int:account_id>/products/<str:organization_id>/", MultiAccountZohoProductListAPIView.as_view()),
    path(
        "multi/accounts/<int:account_id>/categories/<str:organization_id>/<str:category_id>/image/",
        MultiAccountZohoCategoryImageProxyAPIView.as_view(),
    ),
]