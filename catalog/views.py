from typing import Optional

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Banner, Store, Product, ProductReview
from .services.zoho_commerce_products import (
    ZohoCommerceProductError,
    build_product_editpage_url,
    build_products_list_url,
    zoho_commerce_proxy_get,
)
from .services.zoho_product_ids import extract_zoho_category_id_from_detail as _extract_zoho_category_id
from .services.zoho_sites import (
    fetch_zoho_shop_products,
    fetch_zoho_shops_from_accounts,
)
from shop.services.zoho_commerce import ZohoCommerceError
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService
from zoho_integration.views import (
    _extract_image_url,
    _extract_price,
    _product_summary,
    build_image_url,
)
from .serializers import (
    StoreListSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    ProductReviewCreateSerializer,
    ProductReviewReadSerializer,
    UserReviewedProductSerializer,
    StoreAdminSerializer,
    ProductAdminSerializer,
    BannerSerializer,
    BannerAdminSerializer,
)
from .services.product_reviews import (
    user_can_review_product,
    user_review_for_product,
)


def _optional_store_for_zoho_proxy(request):
    raw = request.query_params.get('store_id')
    if raw is None or str(raw).strip() == '':
        return None, None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    store = Store.objects.filter(pk=pk).first()
    if not store:
        return None, Response(
            {'detail': 'Store not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return store, None


class ProductPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class BannerListAPIView(generics.ListAPIView):
    """
    GET — active banners for carousel.

    Query: store_id (optional). When set, returns banners with no store (global)
    plus banners for that store.
    """

    permission_classes = [AllowAny]
    serializer_class = BannerSerializer

    def get_queryset(self):
        qs = Banner.objects.filter(is_active=True).select_related('store').order_by('sort_order', 'id')
        raw = self.request.query_params.get('store_id')
        if raw is None or str(raw).strip() == '':
            return qs
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            return Banner.objects.none()
        return qs.filter(Q(store_id__isnull=True) | Q(store_id=sid))


class BannerAdminListCreateAPIView(generics.ListCreateAPIView):
    """Staff only (JWT + is_staff). GET all banners; POST add."""

    permission_classes = [IsAdminUser]
    queryset = Banner.objects.select_related('store').order_by('sort_order', 'id')
    serializer_class = BannerAdminSerializer


class BannerAdminDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE one banner."""

    permission_classes = [IsAdminUser]
    queryset = Banner.objects.select_related('store').all()
    serializer_class = BannerAdminSerializer


class StoreListAPIView(generics.ListAPIView):
    """
    GET — list all active stores (your 9 storefronts).
    """
    serializer_class = StoreListSerializer
    queryset = Store.objects.filter(is_active=True)


class StoreProductListAPIView(generics.ListAPIView):
    """
    GET — paginated products for one store.
    Query: search (name/sku), page, page_size
    """
    serializer_class = ProductListSerializer
    pagination_class = ProductPageNumberPagination

    def get_queryset(self):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'], is_active=True)
        qs = Product.objects.filter(store=store, is_active=True).order_by('name')
        q = (self.request.query_params.get('search') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))
        return qs


class ZohoCommerceShopListAPIView(APIView):
    """
    GET — list shops from Zoho Commerce sites index in a mobile-friendly shape.

    Query:
    - account_id=<zoho account pk> (optional): fetch shops for one configured
      ZohoCommerceAccount; omitted means all active accounts.
    """

    def get(self, request):
        raw_account_id = (request.query_params.get('account_id') or '').strip()
        account_id = None
        if raw_account_id:
            try:
                account_id = int(raw_account_id)
            except (TypeError, ValueError):
                return Response(
                    {'status': 'error', 'message': 'account_id must be an integer.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            data = fetch_zoho_shops_from_accounts(account_id=account_id)
        except ZohoCommerceError as e:
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'status': 'success',
                'message': 'Stores fetched successfully',
                'mode': 'accounts',
                'processed_account_count': data['processed_account_count'],
                'count': len(data['shops']),
                'stores': data['shops'],
                'errors': data['errors'],
            },
            status=status.HTTP_200_OK,
        )


class ZohoCommerceShopProductListAPIView(APIView):
    """
    GET — list products for a selected Zoho shop id.
    """

    def get(self, request, shop_id: str):
        account = (request.query_params.get('account') or 'primary').strip().lower()
        page = request.query_params.get('page', 1)
        per_page = request.query_params.get('per_page', 20)
        try:
            shop, products = fetch_zoho_shop_products(
                shop_id, page=page, per_page=per_page, account=account,
            )
        except ZohoCommerceError as e:
            msg = str(e)
            st = status.HTTP_404_NOT_FOUND if 'not found' in msg.lower() else status.HTTP_503_SERVICE_UNAVAILABLE
            return Response({'status': 'error', 'message': msg}, status=st)
        return Response(
            {
                'status': 'success',
                'message': 'Products fetched successfully',
                'account': account,
                'organization_id': shop.get('organization_id', ''),
                'shop': shop,
                'count': len(products),
                'products': products,
            },
            status=status.HTTP_200_OK,
        )


class ZohoCommerceProductsProxyAPIView(APIView):
    """
    GET — forwards query string to Zoho Commerce list products API; response body is Zoho JSON.

    Query (optional): ``store_id`` (local Store pk — uses ``zoho_org_id`` for org header),
    filter_by, sort_column, sort_order, page_start_from, per_page
    """

    def get(self, request):
        store, err = _optional_store_for_zoho_proxy(request)
        if err:
            return err
        url = build_products_list_url(dict(request.query_params))
        try:
            http_status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if isinstance(payload, (dict, list)):
            return Response(payload, status=http_status)
        return Response({'detail': payload}, status=http_status)


class ZohoCommerceProductDetailProxyAPIView(APIView):
    """
    GET — Zoho Commerce product edit-page API (full product payload for one product_id).

    Query (optional): ``store_id`` — same as list proxy.
    """

    def get(self, request, product_id: str):
        store, err = _optional_store_for_zoho_proxy(request)
        if err:
            return err
        try:
            url = build_product_editpage_url(product_id)
            http_status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            msg = str(e)
            st = (
                status.HTTP_400_BAD_REQUEST
                if 'required' in msg.lower()
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            return Response({'detail': msg}, status=st)
        if isinstance(payload, (dict, list)):
            return Response(payload, status=http_status)
        return Response({'detail': payload}, status=http_status)


class StoreProductDetailAPIView(APIView):
    """
    GET — single product; store_id must match the product's store (safe for scoped IDs).
    """

    def get(self, request, store_id, pk):
        store = get_object_or_404(Store, pk=store_id, is_active=True)
        product = get_object_or_404(
            Product.objects.select_related('store'),
            pk=pk,
            store=store,
            is_active=True,
        )
        return Response(ProductDetailSerializer(product).data, status=status.HTTP_200_OK)


def _resolve_store_product_by_zoho_query(request):
    raw_s = (request.query_params.get('store_id') or '').strip()
    zoho_product_id = (request.query_params.get('zoho_product_id') or '').strip()
    if not raw_s or not zoho_product_id:
        raise ValidationError(
            {'detail': 'Query parameters store_id and zoho_product_id are required.'},
        )
    try:
        store_id = int(raw_s)
    except ValueError:
        raise ValidationError({'detail': 'store_id must be an integer.'})
    store = get_object_or_404(Store, pk=store_id, is_active=True)
    product = (
        Product.objects.filter(
            store=store,
            zoho_product_id=zoho_product_id,
            is_active=True,
        )
        .select_related('store')
        .first()
    )
    if product is None:
        raise ValidationError(
            {'zoho_product_id': 'No active product with this Zoho id for this store.'},
        )
    return store, product


def _product_review_user_status(request, product) -> dict:
    """Flags for the authenticated user on a product's review page."""
    if not request.user.is_authenticated:
        return {
            'user_has_reviewed': False,
            'can_review': False,
            'user_review': None,
        }
    user_review = user_review_for_product(request.user, product)
    has_reviewed = user_review is not None
    return {
        'user_has_reviewed': has_reviewed,
        'can_review': user_can_review_product(request.user, product),
        'user_review': (
            ProductReviewReadSerializer(user_review).data if user_review is not None else None
        ),
    }


class StoreProductReviewListCreateAPIView(generics.ListCreateAPIView):
    """
    GET — list reviews for a product (public).
    POST — add a review (authenticated). Allowed only if the user bought this product on a
    delivered order (``Order.status == synced``). One review per user per product.

    Query (required): ``store_id``, ``zoho_product_id``.
    Example: ``/api/catalog/stores/products/reviews/?store_id=4&zoho_product_id=7501414000004708555``
    """

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ProductReviewCreateSerializer
        return ProductReviewReadSerializer

    def _review_store_product(self):
        if hasattr(self, '_review_store_product_cache'):
            return self._review_store_product_cache
        self._review_store_product_cache = _resolve_store_product_by_zoho_query(self.request)
        return self._review_store_product_cache

    def get_queryset(self):
        _, product = self._review_store_product()
        return ProductReview.objects.filter(
            product_id=product.pk,
            product__store_id=product.store_id,
        ).select_related('user')

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        _, product = self._review_store_product()
        ctx['product'] = product
        return ctx

    def perform_create(self, serializer):
        serializer.save()

    def list(self, request, *args, **kwargs):
        from django.db.models import Avg

        store, product = self._review_store_product()
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        reviews = ProductReview.objects.filter(product=product)
        review_count = reviews.count()
        row = reviews.aggregate(a=Avg('rating'))
        avg = row.get('a')
        average_rating = round(float(avg), 2) if avg is not None else None
        return Response(
            {
                'store_id': store.pk,
                'zoho_product_id': product.zoho_product_id,
                'product_id': product.pk,
                'review_count': review_count,
                'average_rating': average_rating,
                **_product_review_user_status(request, product),
                'reviews': serializer.data,
            },
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        review = serializer.instance
        return Response(
            {
                'message': 'Review submitted.',
                'user_has_reviewed': True,
                'can_review': False,
                'review': ProductReviewReadSerializer(review).data,
            },
            status=status.HTTP_201_CREATED,
        )


class UserReviewedProductListAPIView(generics.ListAPIView):
    """
    GET — products the authenticated user has already reviewed.

    Optional query: ``store_id`` to filter by store.
    Example: ``/api/catalog/stores/products/reviews/mine/?store_id=4``
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserReviewedProductSerializer

    def get_queryset(self):
        qs = (
            ProductReview.objects.filter(user=self.request.user)
            .select_related('product', 'product__store')
            .order_by('-created_at')
        )
        raw = self.request.query_params.get('store_id')
        if raw is not None and str(raw).strip() != '':
            try:
                store_id = int(raw)
            except (TypeError, ValueError):
                raise ValidationError({'store_id': 'store_id must be an integer.'})
            qs = qs.filter(product__store_id=store_id)
        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        payload = {
            'count': queryset.count(),
            'results': serializer.data,
        }
        raw = request.query_params.get('store_id')
        if raw is not None and str(raw).strip() != '':
            payload['store_id'] = int(raw)
        return Response(payload)


class StoreProductRatingAPIView(APIView):
    """
    GET — average rating summary for a product (public).

    Query (required): ``store_id``, ``zoho_product_id``.
    Example: ``/api/catalog/stores/products/rating/?store_id=4&zoho_product_id=7501414000004708555``
    """

    permission_classes = [AllowAny]

    def get(self, request):
        from django.db.models import Avg

        store, product = _resolve_store_product_by_zoho_query(request)
        reviews = ProductReview.objects.filter(product=product)
        review_count = reviews.count()
        row = reviews.aggregate(a=Avg('rating'))
        avg = row.get('a')
        average_rating = round(float(avg), 2) if avg is not None else None
        return Response(
            {
                'store_id': store.pk,
                'zoho_product_id': product.zoho_product_id,
                'product_id': product.pk,
                'review_count': review_count,
                'average_rating': average_rating,
            },
        )


def _related_as_bool(value, default: bool = False) -> bool:
    raw = (value or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'y', 'on'}


def _related_collect_category_descendant_ids(categories: list, root_category_id: str) -> list:
    """Same tree walk as zoho multi-product category filter (visible rows only)."""
    root_id = str(root_category_id or '').strip()
    if not root_id:
        return []
    children_map: dict[str, list[str]] = {}
    for c in categories:
        if not isinstance(c, dict):
            continue
        cid = str(c.get('category_id') or c.get('id') or '').strip()
        parent_id = str(c.get('parent_category_id') or c.get('parent_id') or '').strip()
        if not cid:
            continue
        children_map.setdefault(parent_id, []).append(cid)
    result: list[str] = []
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        for child in children_map.get(current, []):
            if child not in seen:
                stack.append(child)
    return result


def _related_extract_zoho_category_id_from_detail(detail: dict) -> str:
    """Best-effort category id from Zoho Commerce product detail JSON."""
    return _extract_zoho_category_id(detail)


class RelatedProductSuggestionListAPIView(generics.ListAPIView):
    """
    GET — related product suggestions for one product in a store.

    Query:
    - store_id OR organization_id (one required) — organization_id matches Store.zoho_org_id
    - product_id (required unless zoho_product_id is used) — catalog Product.pk
    - zoho_product_id (optional alternative to product_id) — Product.zoho_product_id for this store
    - zoho_category_id (optional) — Zoho category; peers from Zoho then mapped to local Product rows
    - same_zoho_category (optional, default false) — if true and zoho_category_id omitted, infer
      category from Zoho product detail for the anchor, then list peers (needs zoho_product_id on anchor)
    - response_source=zoho (alias: source=zoho) — return Zoho-shaped product list (like /zoho/multi/products/),
      not catalog Product rows. Requires zoho_category_id or same_zoho_category=true.
    - account_id (optional) — use this ZohoCommerceAccount; organization_id must match that account.
    - category_id — alias for zoho_category_id (Zoho category id string).
    - Standalone Zoho related (no catalog Product row): response_source=zoho + account_id +
      organization_id + category_id (or zoho_category_id) + exclude_product_id (or exclude_zoho_product_id);
      omit product_id and zoho_product_id. limit max 200 for this mode.
    - Zoho-only anchor (no local Product): response_source=zoho + organization_id or store_id +
      zoho_product_id (omit product_id). Category from Zoho product detail unless category_id /
      zoho_category_id is passed. Peers from Zoho; anchor excluded by Zoho id. limit max 20.
    """

    serializer_class = ProductListSerializer

    def _resolve_store(self) -> Store:
        store_id = self.kwargs.get('store_id') or self.request.query_params.get('store_id')
        org_id = (self.request.query_params.get('organization_id') or '').strip()

        if store_id not in (None, '') and org_id:
            try:
                sid = int(store_id)
            except (TypeError, ValueError):
                raise ValidationError({'store_id': 'Must be an integer.'})
            store = Store.objects.filter(pk=sid, is_active=True).first()
            if store is None:
                raise ValidationError({'store_id': 'Store not found.'})
            if (store.zoho_org_id or '').strip() != org_id:
                raise ValidationError(
                    {
                        'organization_id': (
                            'organization_id does not match this store\'s zoho_org_id.'
                        ),
                    },
                )
            return store

        if org_id:
            store = Store.objects.filter(zoho_org_id=org_id, is_active=True).first()
            if store is None:
                raise ValidationError(
                    {
                        'organization_id': (
                            'No active store with this organization_id (matches Store.zoho_org_id).'
                        ),
                    },
                )
            return store

        if store_id in (None, ''):
            raise ValidationError(
                {
                    'store_id': 'Provide store_id or organization_id.',
                    'organization_id': 'Provide store_id or organization_id.',
                },
            )

        try:
            sid = int(store_id)
        except (TypeError, ValueError):
            raise ValidationError({'store_id': 'Must be an integer.'})

        return get_object_or_404(Store, pk=sid, is_active=True)

    def _resolve_store_and_product(self):
        store = self._resolve_store()
        product_id_raw = self.kwargs.get('pk')
        if product_id_raw is None:
            product_id_raw = self.request.query_params.get('product_id')
        zoho_product_id = (self.request.query_params.get('zoho_product_id') or '').strip()

        if zoho_product_id:
            if product_id_raw not in (None, ''):
                raise ValidationError(
                    {'product_id': 'Use either product_id or zoho_product_id, not both.'},
                )
            product = (
                Product.objects.filter(
                    store=store,
                    zoho_product_id=zoho_product_id,
                    is_active=True,
                )
                .select_related('store')
                .first()
            )
            if product is None:
                raise ValidationError(
                    {'zoho_product_id': 'No active product with this Zoho id for this store.'},
                )
            return store, product

        if product_id_raw in (None, ''):
            raise ValidationError(
                {
                    'product_id': (
                        'This query parameter is required unless zoho_product_id is provided.'
                    ),
                },
            )

        try:
            product_pk = int(product_id_raw)
        except (TypeError, ValueError):
            raise ValidationError({'product_id': 'Must be an integer.'})

        product = get_object_or_404(
            Product.objects.select_related('store'),
            pk=product_pk,
            store=store,
            is_active=True,
        )
        return store, product

    def _zoho_org_account_service(
        self,
        store: Optional[Store] = None,
        *,
        organization_id: Optional[str] = None,
    ) -> tuple[str, ZohoCommerceAccount, ZohoCommerceService]:
        account_id_raw = (self.request.query_params.get('account_id') or '').strip()
        org_from_store = (getattr(store, 'zoho_org_id', None) or '').strip() if store is not None else ''
        if organization_id is not None:
            org = organization_id.strip()
        elif org_from_store:
            org = org_from_store
        else:
            raise ValidationError(
                {
                    'organization_id': (
                        'Provide organization_id or a store with zoho_org_id for Zoho category requests.'
                    ),
                },
            )
        if not org:
            raise ValidationError(
                {'organization_id': 'organization_id cannot be empty.'},
            )
        if store is not None and org_from_store and org_from_store != org:
            raise ValidationError(
                {'organization_id': 'organization_id does not match this store\'s zoho_org_id.'},
            )

        if account_id_raw:
            try:
                aid = int(account_id_raw)
            except (TypeError, ValueError):
                raise ValidationError({'account_id': 'Must be an integer.'})
            account = ZohoCommerceAccount.objects.filter(id=aid, is_active=True).first()
            if account is None:
                raise ValidationError(
                    {'account_id': 'Zoho account not found or inactive.'},
                )
            acc_org = (account.organization_id or '').strip()
            if acc_org != org:
                raise ValidationError(
                    {
                        'organization_id': (
                            'Must match the selected account\'s Zoho organization_id.'
                        ),
                    },
                )
        else:
            account = ZohoCommerceAccount.objects.filter(is_active=True, organization_id=org).first()
            if account is None:
                raise ValidationError(
                    {
                        'zoho_category_id': (
                            'No active ZohoCommerceAccount for this organization_id. '
                            'Pass account_id or sync accounts.'
                        ),
                    },
                )
        return org, account, ZohoCommerceService(account)

    def _zoho_category_id_list(
        self,
        service: ZohoCommerceService,
        org: str,
        zoho_category_id: str,
        include_descendants: bool,
    ) -> list[str]:
        if include_descendants:
            cat_data = service.list_categories(organization_id=org)
            raw_cats = cat_data.get('categories', []) or cat_data.get('category', []) or []
            cat_rows = [c for c in raw_cats if isinstance(c, dict)]
            visible = [c for c in cat_rows if c.get('visibility') is not False]
            category_ids = _related_collect_category_descendant_ids(visible, zoho_category_id)
            if not category_ids:
                category_ids = [str(zoho_category_id).strip()]
        else:
            category_ids = [str(zoho_category_id).strip()]
        return category_ids

    def _zoho_peer_raw_rows(
        self,
        service: ZohoCommerceService,
        org: str,
        category_ids: list[str],
        anchor_zoho: str,
    ) -> list[dict]:
        rows_out: list[dict] = []
        seen: set[str] = set()
        anchor_zoho = (anchor_zoho or '').strip()
        try:
            for cid in category_ids:
                pdata = service.list_products(organization_id=org, category_id=cid, page=1, per_page=200)
                prows = pdata.get('products', []) or pdata.get('items', []) or []
                for row in prows:
                    if not isinstance(row, dict):
                        continue
                    pid = str(row.get('product_id') or row.get('item_id') or row.get('id') or '').strip()
                    if not pid or pid == anchor_zoho or pid in seen:
                        continue
                    seen.add(pid)
                    rows_out.append(row)
        except Exception as e:
            raise ValidationError({'zoho_category_id': f'Zoho request failed: {e}'}) from e
        return rows_out

    def _enrich_zoho_peer_rows(self, service: ZohoCommerceService, org: str, rows: list[dict]) -> None:
        for product in rows:
            if _extract_price(product) not in ('0', '0.00'):
                continue
            pid = str(product.get('product_id') or product.get('item_id') or product.get('id') or '').strip()
            if not pid:
                continue
            try:
                detail_data = service.get_product_detail(organization_id=org, product_id=pid)
            except Exception:
                continue
            detail_product = (
                detail_data.get('product')
                or detail_data.get('item')
                or detail_data.get('data')
                or {}
            )
            if not isinstance(detail_product, dict):
                continue
            detail_price = _extract_price(detail_product)
            if detail_price not in ('0', '0.00'):
                product['rate'] = detail_price
            if not (product.get('sku') or product.get('product_sku')):
                detail_sku = detail_product.get('sku') or detail_product.get('product_sku')
                if detail_sku:
                    product['sku'] = detail_sku
            if not (product.get('image_url') or product.get('image_name')):
                detail_image = _extract_image_url(detail_product)
                if detail_image:
                    product['image_url'] = detail_image

    def _queryset_by_zoho_category(self, store: Store, anchor: Product, zoho_category_id: str, limit: int):
        """Match Zoho category listing; return local Product rows only (excludes anchor)."""
        include_descendants = _related_as_bool(
            self.request.query_params.get('include_descendants'),
            default=True,
        )
        try:
            org, _account, service = self._zoho_org_account_service(store)
            category_ids = self._zoho_category_id_list(
                service, org, zoho_category_id, include_descendants,
            )
            rows = self._zoho_peer_raw_rows(service, org, category_ids, (anchor.zoho_product_id or '').strip())
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError({'zoho_category_id': f'Zoho request failed: {e}'}) from e

        zoho_ids = {
            str(r.get('product_id') or r.get('item_id') or r.get('id') or '').strip()
            for r in rows
        }
        zoho_ids.discard('')
        if not zoho_ids:
            return []

        qs = (
            Product.objects.filter(
                store=store,
                is_active=True,
                zoho_product_id__in=zoho_ids,
            )
            .select_related('store')
            .exclude(pk=anchor.pk)
            .order_by('name')[:limit]
        )
        return list(qs)

    def _infer_zoho_category_for_anchor(self, store: Store, product: Product) -> str:
        """Resolve primary Zoho category id for the anchor product (detail API)."""
        zoho_pid = (product.zoho_product_id or '').strip()
        if not zoho_pid:
            return ''
        try:
            org, _account, service = self._zoho_org_account_service(store)
        except ValidationError:
            return ''
        try:
            detail = service.get_product_detail(
                organization_id=org,
                product_id=zoho_pid,
            )
        except Exception:
            return ''
        return _related_extract_zoho_category_id_from_detail(detail)

    def _resolve_zoho_category_id_for_request(self, store: Store, product: Product) -> str:
        zoho_category_id = (
            self.request.query_params.get('zoho_category_id')
            or self.request.query_params.get('category_id')
            or ''
        ).strip()
        if not zoho_category_id and _related_as_bool(
            self.request.query_params.get('same_zoho_category'),
            default=False,
        ):
            zoho_category_id = self._infer_zoho_category_for_anchor(store, product)
        return zoho_category_id

    def _related_wants_zoho_direct_response(self) -> bool:
        src = (
            self.request.query_params.get('response_source')
            or self.request.query_params.get('source')
            or ''
        ).strip().lower()
        return src in ('zoho', 'zoho_api', 'direct')

    def _finalize_zoho_related_products(
        self,
        request,
        *,
        org: str,
        account: ZohoCommerceAccount,
        service: ZohoCommerceService,
        rows: list,
        zoho_category_id: str,
        include_descendants: bool,
        store: Optional[Store],
        limit: int,
        extra: Optional[dict] = None,
    ) -> Response:
        rows = rows[:limit]
        self._enrich_zoho_peer_rows(service, org, rows)

        store_domain = (getattr(store, 'zoho_store_domain', '') or '').strip() if store else ''
        summaries = [_product_summary(p, store_domain=store_domain) for p in rows]
        for row in summaries:
            current_image = (row.get('image_url') or '').strip()
            normalized_image = build_image_url(store_domain, current_image)
            if normalized_image:
                row['image_url'] = normalized_image
                continue
            if current_image and (
                current_image.startswith('http://')
                or current_image.startswith('https://')
                or current_image.startswith('/')
            ):
                continue
            pid = (row.get('product_id') or '').strip()
            if not pid:
                continue
            if store is not None:
                row['image_url'] = request.build_absolute_uri(
                    f'/api/shop/zoho-products/{pid}/image/?store_id={store.pk}',
                )

        body = {
            'status': 'success',
            'response_source': 'zoho',
            'account_id': account.pk,
            'account_name': account.name,
            'account_email': account.email,
            'organization_id': org,
            'zoho_category_id': zoho_category_id,
            'include_descendants': include_descendants,
            'count': len(summaries),
            'products': summaries,
        }
        if extra:
            body.update(extra)
        return Response(body)

    def _list_zoho_related_standalone(self, request, *, zoho_category_id: str) -> Response:
        org_param = (request.query_params.get('organization_id') or '').strip()
        if not org_param:
            raise ValidationError(
                {'organization_id': 'Required for account-based related without product_id.'},
            )
        raw_limit = request.query_params.get('limit', 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 200))

        exclude = (
            (request.query_params.get('exclude_product_id') or request.query_params.get('exclude_zoho_product_id') or '')
            .strip()
        )
        if not exclude:
            raise ValidationError(
                {
                    'exclude_product_id': (
                        'Required for standalone related: Zoho product id to omit from results.'
                    ),
                },
            )

        include_descendants = _related_as_bool(
            request.query_params.get('include_descendants'),
            default=True,
        )
        try:
            org, account, service = self._zoho_org_account_service(
                None,
                organization_id=org_param,
            )
            category_ids = self._zoho_category_id_list(
                service, org, zoho_category_id, include_descendants,
            )
            rows = self._zoho_peer_raw_rows(service, org, category_ids, exclude)
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError({'zoho_category_id': f'Zoho request failed: {e}'}) from e

        store = Store.objects.filter(zoho_org_id=org, is_active=True).first()
        return self._finalize_zoho_related_products(
            request,
            org=org,
            account=account,
            service=service,
            rows=rows,
            zoho_category_id=zoho_category_id,
            include_descendants=include_descendants,
            store=store,
            limit=limit,
            extra={'exclude_product_id': exclude},
        )

    def _list_zoho_related_anchor_without_local_catalog_row(
        self,
        request,
        *,
        store: Store,
        zoho_product_id: str,
    ) -> Response:
        """
        response_source=zoho: anchor identified only by Zoho id (no catalog Product row).
        Resolve category from Zoho detail or from category_id / zoho_category_id query params.
        """
        anchor = (zoho_product_id or '').strip()
        if not anchor:
            raise ValidationError({'zoho_product_id': 'Required.'})

        raw_limit = request.query_params.get('limit', 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 20))

        zoho_category_id = (
            (request.query_params.get('zoho_category_id') or '').strip()
            or (request.query_params.get('category_id') or '').strip()
        )
        org, account, service = self._zoho_org_account_service(store)

        if not zoho_category_id:
            try:
                detail_data = service.get_product_detail(
                    organization_id=org,
                    product_id=anchor,
                )
            except Exception as e:
                raise ValidationError(
                    {'zoho_product_id': f'Zoho product detail failed: {e}'},
                ) from e
            detail_product = (
                detail_data.get('product')
                or detail_data.get('item')
                or detail_data.get('data')
                or {}
            )
            if not isinstance(detail_product, dict):
                raise ValidationError(
                    {'zoho_product_id': 'Invalid Zoho product detail payload for this id.'},
                )
            zoho_category_id = _related_extract_zoho_category_id_from_detail(detail_product)

        if not zoho_category_id:
            raise ValidationError(
                {
                    'zoho_category_id': (
                        'Could not infer Zoho category from product detail; pass category_id '
                        '(or zoho_category_id).'
                    ),
                },
            )

        include_descendants = _related_as_bool(
            request.query_params.get('include_descendants'),
            default=True,
        )
        try:
            category_ids = self._zoho_category_id_list(
                service, org, zoho_category_id, include_descendants,
            )
            rows = self._zoho_peer_raw_rows(service, org, category_ids, anchor)
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError({'zoho_category_id': f'Zoho request failed: {e}'}) from e

        return self._finalize_zoho_related_products(
            request,
            org=org,
            account=account,
            service=service,
            rows=rows,
            zoho_category_id=zoho_category_id,
            include_descendants=include_descendants,
            store=store,
            limit=limit,
            extra={'anchor_source': 'zoho_id_only'},
        )

    def list(self, request, *args, **kwargs):
        if not self._related_wants_zoho_direct_response():
            return super().list(request, *args, **kwargs)

        qp = request.query_params
        has_anchor = bool((qp.get('product_id') or '').strip() or (qp.get('zoho_product_id') or '').strip())
        if (qp.get('account_id') or '').strip() and (qp.get('organization_id') or '').strip():
            cat = (qp.get('category_id') or qp.get('zoho_category_id') or '').strip()
            if cat and not has_anchor:
                return self._list_zoho_related_standalone(request, zoho_category_id=cat)

        zoho_pid_only = (qp.get('zoho_product_id') or '').strip()
        product_id_only = (qp.get('product_id') or '').strip()
        if zoho_pid_only and not product_id_only:
            store_for_anchor = self._resolve_store()
            has_local = Product.objects.filter(
                store=store_for_anchor,
                zoho_product_id=zoho_pid_only,
                is_active=True,
            ).exists()
            if not has_local:
                return self._list_zoho_related_anchor_without_local_catalog_row(
                    request,
                    store=store_for_anchor,
                    zoho_product_id=zoho_pid_only,
                )

        store, product = self._resolve_store_and_product()
        raw_limit = request.query_params.get('limit', 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 20))

        zoho_category_id = self._resolve_zoho_category_id_for_request(store, product)
        if not zoho_category_id:
            raise ValidationError(
                {
                    'response_source': (
                        'When response_source=zoho, provide zoho_category_id or category_id, or '
                        'same_zoho_category=true with a zoho_product_id on the anchor so category can be inferred; '
                        'or use account_id + organization_id + category_id + exclude_product_id without product_id.'
                    ),
                    'zoho_category_id': (
                        'Required for Zoho-direct related unless same_zoho_category can infer it, '
                        'or use standalone account mode (see docs on RelatedProductSuggestionListAPIView).'
                    ),
                },
            )

        include_descendants = _related_as_bool(
            request.query_params.get('include_descendants'),
            default=True,
        )
        try:
            org, account, service = self._zoho_org_account_service(store)
            category_ids = self._zoho_category_id_list(
                service, org, zoho_category_id, include_descendants,
            )
            rows = self._zoho_peer_raw_rows(
                service,
                org,
                category_ids,
                (product.zoho_product_id or '').strip(),
            )
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError({'zoho_category_id': f'Zoho request failed: {e}'}) from e

        return self._finalize_zoho_related_products(
            request,
            org=org,
            account=account,
            service=service,
            rows=rows,
            zoho_category_id=zoho_category_id,
            include_descendants=include_descendants,
            store=store,
            limit=limit,
            extra=None,
        )

    def get_queryset(self):
        store, product = self._resolve_store_and_product()

        raw_limit = self.request.query_params.get('limit', 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 20))

        zoho_category_id = self._resolve_zoho_category_id_for_request(store, product)

        if zoho_category_id:
            return self._queryset_by_zoho_category(store, product, zoho_category_id, limit)

        base_qs = Product.objects.filter(
            store=product.store,
            is_active=True,
        ).select_related('store').exclude(pk=product.pk)

        if product.category:
            related_qs = list(base_qs.filter(category__iexact=product.category).order_by('name')[:limit])
            if len(related_qs) >= limit:
                return related_qs
            needed = limit - len(related_qs)
            fallback_qs = list(
                base_qs.exclude(pk__in=[p.pk for p in related_qs]).order_by('name')[:needed]
            )
            return related_qs + fallback_qs

        return base_qs.order_by('name')[:limit]


class AdminStoreListCreateAPIView(generics.ListCreateAPIView):
    """
    Staff only (JWT + is_staff). GET all stores; POST create a store.
    """
    permission_classes = [IsAdminUser]
    queryset = Store.objects.all().order_by('sort_order', 'name')
    serializer_class = StoreAdminSerializer


class AdminStoreDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE one store by id."""
    permission_classes = [IsAdminUser]
    queryset = Store.objects.all()
    serializer_class = StoreAdminSerializer


class AdminStoreProductListCreateAPIView(generics.ListCreateAPIView):
    """
    Staff only. GET all products for a store (including inactive); POST add product mapped to this store.
    """
    permission_classes = [IsAdminUser]
    serializer_class = ProductAdminSerializer

    def get_queryset(self):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'])
        return Product.objects.filter(store=store).select_related('store').order_by('name')

    def perform_create(self, serializer):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'])
        serializer.save(store=store)


class AdminStoreProductDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE product; must belong to store_id in URL."""
    permission_classes = [IsAdminUser]
    serializer_class = ProductAdminSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return Product.objects.filter(store_id=self.kwargs['store_id']).select_related('store')

