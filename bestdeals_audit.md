# Best Deals Audit

## Scope
This report covers only the best-deals-related code paths in the current workspace.

## 1. `MultiAccountZohoBestDealsAPIView`

File: `zoho_integration/views.py`
Line: 1467

```python
class MultiAccountZohoBestDealsAPIView(APIView):
    """
    Best deals for the mobile app.

    Default (source=admin): Django admin marks catalog.Product.is_best_deal; API loads
    Zoho product detail per zoho_product_id and merges live Zoho fields with local order/ids.

    Alternatives:
      - source=category — Zoho category (ZOHO_BEST_DEALS_CATEGORY_ID / category_name)
      - source=collection — Zoho storefront collection (collection_id, collection_name, or env)
      - GET /zoho/multi/collections/ — list collection ids for an organization (admin API)

    Query params:
      - account_id + organization_id, or store_id (resolves org / account when possible)
      - source (optional): admin | category | collection (default from ZOHO_BEST_DEALS_SOURCE)
      - collection_id (optional): when set, forces source=collection (else ZOHO_BEST_DEALS_COLLECTION_ID)
      - limit (optional, default 50, max 200)
    """

    @staticmethod
    def _resolve_best_deals_source(request) -> str:
        if (request.GET.get("collection_id") or "").strip():
            return "collection"
        source_param = (request.GET.get("source") or "").strip().lower()
        if source_param:
            return source_param
        return (
            getattr(settings, "ZOHO_BEST_DEALS_SOURCE", "admin") or "admin"
        ).strip().lower()

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        store_id_raw = (request.GET.get("store_id") or "").strip()
        source = self._resolve_best_deals_source(request)
        limit_raw = (request.GET.get("limit") or "").strip()

        if store_id_raw:
            try:
                store_pk = int(store_id_raw)
            except ValueError:
                return Response(
                    {"status": "error", "message": "store_id must be an integer"},
                    status=400,
                )
            store_from_id = Store.objects.filter(pk=store_pk).first()
            if not store_from_id:
                return Response(
                    {"status": "error", "message": "Store not found"},
                    status=404,
                )
            resolved_org = str(store_from_id.zoho_org_id or "").strip()
            if not resolved_org:
                return Response(
                    {
                        "status": "error",
                        "message": "Store has no zoho_org_id configured",
                    },
                    status=400,
                )
            if organization_id and organization_id != resolved_org:
                return Response(
                    {
                        "status": "error",
                        "message": "store_id does not match organization_id",
                    },
                    status=400,
                )
            organization_id = resolved_org

        if not account_id_raw and organization_id:
            linked = ZohoCommerceAccount.objects.filter(
                organization_id=organization_id,
                is_active=True,
            ).first()
            if linked:
                account_id_raw = str(linked.id)

        if not account_id_raw:
            return Response(
                {
                    "status": "error",
                    "message": "account_id query parameter is required (or use store_id with a linked Zoho account)",
                },
                status=400,
            )
        if not organization_id:
            return Response(
                {
                    "status": "error",
                    "message": "organization_id or store_id query parameter is required",
                },
                status=400,
            )

        try:
            account_id = int(account_id_raw)
        except ValueError:
            return Response(
                {"status": "error", "message": "account_id must be an integer"},
                status=400,
            )

        try:
            limit = int(limit_raw) if limit_raw else 50
        except ValueError:
            return Response(
                {"status": "error", "message": "limit must be an integer"},
                status=400,
            )
        if limit < 1:
            limit = 1
        if limit > 200:
            limit = 200

        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response(
                {"status": "error", "message": "Zoho account not found"},
                status=404,
            )

        store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
        if not store:
            return Response(
                {
                    "status": "error",
                    "message": "No catalog store found for this organization_id",
                },
                status=404,
            )
        store_domain = (getattr(store, "zoho_store_domain", "") or "").strip()

        if source == "collection":
            return self._best_deals_from_collection(
                request,
                account=account,
                organization_id=organization_id,
                store=store,
                store_domain=store_domain,
                limit=limit,
            )
        if source == "category":
            return self._best_deals_from_category(
                request,
                account=account,
                organization_id=organization_id,
                store=store,
                store_domain=store_domain,
                limit=limit,
            )
        if source != "admin":
            return Response(
                {"status": "error", "message": "source must be admin, category, or collection"},
                status=400,
            )
        return self._best_deals_from_admin(
            request,
            account=account,
            organization_id=organization_id,
            store=store,
            store_domain=store_domain,
            limit=limit,
        )

    def _best_deals_from_admin(
        self,
        request,
        *,
        account: ZohoCommerceAccount,
        organization_id: str,
        store: Store,
        store_domain: str,
        limit: int,
    ):
        local_rows = list(
            Product.objects.filter(
                store=store,
                is_best_deal=True,
                is_active=True,
            ).order_by("best_deal_sort_order", "name")[:limit]
        )

        service = ZohoCommerceService(account)
        product_summaries: list[dict] = []
        skipped: list[dict] = []

        for local in local_rows:
            zpid = (local.zoho_product_id or "").strip()
            if not zpid:
                skipped.append(
                    {
                        "catalog_product_id": local.pk,
                        "product_name": local.name,
                        "reason": "missing zoho_product_id",
                    }
                )
                continue
            try:
                detail_data = service.get_product_detail(
                    organization_id=organization_id,
                    product_id=zpid,
                )
            except Exception as e:
                skipped.append(
                    {
                        "catalog_product_id": local.pk,
                        "product_id": zpid,
                        "product_name": local.name,
                        "reason": str(e),
                    }
                )
                continue

            zoho_row = _zoho_detail_product_dict(detail_data)
            if not zoho_row:
                skipped.append(
                    {
                        "catalog_product_id": local.pk,
                        "product_id": zpid,
                        "product_name": local.name,
                        "reason": "empty Zoho product detail",
                    }
                )
                continue

            product_summaries.append(
                _best_deal_summary_from_local_zoho(local, zoho_row, store_domain)
            )

        _normalize_best_deals_product_images(request, store, store_domain, product_summaries)

        payload = {
            "status": "success",
            "source": "admin",
            "account_id": account.id,
            "account_name": account.name,
            "account_email": account.email,
            "organization_id": organization_id,
            "store_id": store.pk,
            "count": len(product_summaries),
            "products": product_summaries,
        }
        if skipped:
            payload["skipped"] = skipped
        if not product_summaries and not local_rows:
            payload["message"] = (
                "No products marked as best deal. In Django admin, open Products and enable "
                "'Is best deal' (requires zoho_product_id)."
            )
        return Response(payload, status=200)

    def _best_deals_from_category(
        self,
        request,
        *,
        account: ZohoCommerceAccount,
        organization_id: str,
        store: Store,
        store_domain: str,
        limit: int,
    ):
        category_id = (request.GET.get("category_id") or "").strip()
        if not category_id:
            category_id = str(getattr(settings, "ZOHO_BEST_DEALS_CATEGORY_ID", "") or "").strip()
        category_name = (request.GET.get("category_name") or "").strip()
        if not category_name:
            category_name = str(getattr(settings, "ZOHO_BEST_DEALS_CATEGORY_NAME", "") or "").strip()
        include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)

        service = ZohoCommerceService(account)
        try:
            category_id, category_name = _resolve_best_deals_category_id(
                service,
                organization_id,
                category_id=category_id,
                category_name=category_name,
            )
        except Exception as e:
            return Response({"status": "error", "message": str(e)}, status=400)

        if not category_id:
            return Response(
                {
                    "status": "error",
                    "message": (
                        "No best-deals category found. Pass category_id or create a Zoho category "
                        "(e.g. Best Deals)."
                    ),
                },
                status=404,
            )

        raw_products = _zoho_product_rows_for_org(
            service,
            organization_id,
            category_id=category_id,
            include_descendants=include_descendants,
        )[:limit]
        _enrich_zoho_list_product_rows_from_detail(service, organization_id, raw_products)

        product_summaries = [_product_summary(p, store_domain=store_domain) for p in raw_products]
        for row in product_summaries:
            row["category_id"] = category_id
        _normalize_best_deals_product_images(request, store, store_domain, product_summaries)

        return Response(
            {
                "status": "success",
                "source": "category",
                "account_id": account.id,
                "account_name": account.name,
                "account_email": account.email,
                "organization_id": organization_id,
                "category_id": category_id,
                "category_name": category_name,
                "include_descendants": include_descendants,
                "count": len(product_summaries),
                "products": product_summaries,
            },
            status=200,
        )

    def _best_deals_from_collection(
        self,
        request,
        *,
        account: ZohoCommerceAccount,
        organization_id: str,
        store: Store,
        store_domain: str,
        limit: int,
    ):
        collection_id = (request.GET.get("collection_id") or "").strip()
        collection_name_query = (request.GET.get("collection_name") or "").strip()
        if not collection_id and collection_name_query:
            try:
                admin_rows = list_zoho_commerce_collections(
                    organization_id,
                    store=store,
                )
                collection_id, _resolved_name = resolve_collection_id_by_name(
                    admin_rows,
                    collection_name_query,
                )
            except ZohoCommerceError as exc:
                return Response(
                    {"status": "error", "message": str(exc)},
                    status=400,
                )
            if not collection_id:
                return Response(
                    {
                        "status": "error",
                        "message": (
                            f'No collection named "{collection_name_query}" found for '
                            f'organization_id={organization_id}. '
                            'Use GET /zoho/multi/collections/ to list available collections.'
                        ),
                    },
                    status=404,
                )

        if not collection_id:
            collection_id = str(getattr(settings, "ZOHO_BEST_DEALS_COLLECTION_ID", "") or "").strip()

        if not collection_name_query:
            collection_name_query = str(
                getattr(settings, "ZOHO_BEST_DEALS_COLLECTION_NAME", "") or ""
            ).strip()

        if not collection_id and collection_name_query:
            try:
                admin_rows = list_zoho_commerce_collections(
                    organization_id,
                    store=store,
                )
                collection_id, _resolved_name = resolve_collection_id_by_name(
                    admin_rows,
                    collection_name_query,
                )
            except ZohoCommerceError as exc:
                return Response(
                    {"status": "error", "message": str(exc)},
                    status=400,
                )
            if not collection_id:
                return Response(
                    {
                        "status": "error",
                        "message": (
                            f'No collection named "{collection_name_query}" found for '
                            f'organization_id={organization_id}. '
                            'Use GET /zoho/multi/collections/ to list available collections.'
                        ),
                    },
                    status=404,
                )

        if not collection_id:
            return Response(
                {
                    "status": "error",
                    "message": (
                        "collection_id is required for source=collection "
                        "(or set ZOHO_BEST_DEALS_COLLECTION_ID, or pass collection_name=Best Deals)"
                    ),
                },
                status=400,
            )

        host = store_domain.replace("https://", "").replace("http://", "").split("/")[0].lower()
        if not host:
            return Response(
                {
                    "status": "error",
                    "message": "Store zoho_store_domain must be set for storefront collection fetch",
                },
                status=400,
            )

        commerce_url = (getattr(account, "commerce_base_url", "") or "").strip() or "https://commerce.zoho.com"
        collection_url_slug = (request.GET.get("collection_url") or "").strip().strip("/")
        if not collection_url_slug:
            try:
                for row in list_zoho_commerce_collections(organization_id, store=store):
                    rid = str(row.get("collection_id") or row.get("id") or "").strip()
                    if rid == collection_id:
                        collection_url_slug = str(
                            row.get("url") or row.get("collection_url") or ""
                        ).strip().strip("/")
                        break
            except ZohoCommerceError:
                pass

        payload = fetch_storefront_collection_json(
            commerce_url,
            host,
            collection_id,
            collection_url=collection_url_slug,
        )
        if not payload or not extract_storefront_collection_products(payload):
            slug_hint = f"collections/{collection_url_slug}/{collection_id}" if collection_url_slug else f"collections/{collection_id}"
            return Response(
                {
                    "status": "error",
                    "message": (
                        "Storefront collection has no products (or could not be loaded). "
                        "Confirm products are in this collection in Zoho and the collection is published."
                    ),
                    "collection_id": collection_id,
                    "collection_url": collection_url_slug or None,
                    "store_domain": host,
                    "hint": (
                        "Test in Postman: GET "
                        f"https://commerce.zoho.com/storefront/api/v1/{slug_hint}"
                        f"?format=json with header domain-name: {host}"
                    ),
                },
                status=502,
            )

        raw_products = extract_storefront_collection_products(payload)[:limit]
        collection_name = extract_storefront_collection_name(payload)

        product_summaries = [_product_summary(p, store_domain=store_domain) for p in raw_products]
        for row in product_summaries:
            row["collection_id"] = collection_id
        _normalize_best_deals_product_images(request, store, store_domain, product_summaries)

        return Response(
            {
                "status": "success",
                "source": "collection",
                "account_id": account.id,
                "account_name": account.name,
                "account_email": account.email,
                "organization_id": organization_id,
                "store_id": store.pk,
                "collection_id": collection_id,
                "collection_name": collection_name,
                "count": len(product_summaries),
                "products": product_summaries,
            },
            status=200,
        )
```

## 2. Collection Branch Deep Dive

### Current resolution order in `source=collection`

File: `zoho_integration/views.py`
Line: 1801-1939

1. `collection_id` is first read from `request.GET.get("collection_id")`.
2. `collection_name_query` is first read from `request.GET.get("collection_name")`.
3. If `collection_id` is empty and `collection_name_query` is present, the code lists Zoho Commerce collections and resolves `collection_id` by matching that query name.
4. If `collection_id` is still empty, the code falls back to `settings.ZOHO_BEST_DEALS_COLLECTION_ID`.
5. If `collection_name_query` is empty, the code falls back to `settings.ZOHO_BEST_DEALS_COLLECTION_NAME`.
6. If `collection_id` is still empty and `collection_name_query` is now populated, the code lists Zoho Commerce collections again and resolves by the fallback name.
7. If `collection_id` is still empty after those steps, the request returns HTTP 400.
8. After that, the code derives `collection_url_slug`, fetches the storefront collection JSON, and extracts `collection_name` from the payload.

### Exact assignment lines

File: `zoho_integration/views.py`
Line: 1801

```python
        collection_id = (request.GET.get("collection_id") or "").strip()
```

File: `zoho_integration/views.py`
Line: 1802

```python
        collection_name_query = (request.GET.get("collection_name") or "").strip()
```

File: `zoho_integration/views.py`
Line: 1832

```python
            collection_id = str(getattr(settings, "ZOHO_BEST_DEALS_COLLECTION_ID", "") or "").strip()
```

File: `zoho_integration/views.py`
Line: 1835-1837

```python
            collection_name_query = str(
                getattr(settings, "ZOHO_BEST_DEALS_COLLECTION_NAME", "") or ""
            ).strip()
```

File: `zoho_integration/views.py`
Line: 1931

```python
        collection_name = extract_storefront_collection_name(payload)
```

### Hardcoded `"Best Deals"` check

Inside `MultiAccountZohoBestDealsAPIView._best_deals_from_collection`, `collection_name` is not hardcoded as `"Best Deals"`.
It is resolved from the query parameter first, then from `settings.ZOHO_BEST_DEALS_COLLECTION_NAME`, and finally from the storefront payload via `extract_storefront_collection_name(payload)`.

## 3. Helper Functions Currently Used

### `zoho_integration/commerce_collections.py`

File: `zoho_integration/commerce_collections.py`
Line: 35-92

```python
def collection_summary(row: dict) -> dict:
    return {
        'collection_id': str(row.get('collection_id') or row.get('id') or '').strip(),
        'name': str(row.get('name') or row.get('collection_name') or '').strip(),
        'url': str(row.get('url') or row.get('collection_url') or '').strip(),
        'status': str(row.get('status') or '').strip(),
    }


def list_zoho_commerce_collections(
    organization_id: str,
    *,
    store: Store | None = None,
    timeout: int = 30,
) -> list[dict]:
    """
    GET {ZOHO_API_BASE_HOST}/commerce/v1/collections?organization_id=...
    Requires OAuth (ZohoCommerceService.admin_headers).
    """
    org = str(organization_id or '').strip()
    if not org:
        raise ZohoCommerceError('organization_id is required to list collections.')

    url = f'{zoho_api_base_host()}/commerce/v1/collections'
    headers = ZohoCommerceService.admin_headers(store)
    try:
        response = requests.get(
            url,
            headers=headers,
            params={'organization_id': org},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ZohoCommerceError(f'Could not reach Zoho Commerce collections API: {exc}') from exc

    if response.status_code >= 400:
        body = (response.text or '')[:500]
        raise ZohoCommerceError(
            f'Zoho collections API returned HTTP {response.status_code}: {body}',
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ZohoCommerceError('Invalid JSON from Zoho collections API.') from exc

    return _collection_rows_from_payload(data)


def resolve_collection_id_by_name(
    rows: list[dict],
    collection_name: str,
) -> tuple[str, str]:
    """Return (collection_id, resolved_name) or ('', '') if not found."""
    want = (collection_name or '').strip().lower()
    if not want:
        return '', ''
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get('name') or row.get('collection_name') or '').strip()
        if name.lower() != want:
            continue
        cid = str(row.get('collection_id') or row.get('id') or '').strip()
        if cid:
            return cid, name
    return '', ''
```

### `zoho_integration/storefront_collections.py`

File: `zoho_integration/storefront_collections.py`
Line: 89-171

```python
def fetch_storefront_collection_json(
    commerce_base_url: str,
    domain_name: str,
    collection_id: str,
    *,
    collection_url: str = "",
    timeout: int = 25,
) -> dict:
    """
    GET /storefront/api/v1/collections/{id} with domain-name header (no OAuth).

    Some stores return payload.redirect to /collections/{slug}/{id}; we follow that.
    Optional collection_url (e.g. best-deals from admin API) is used as a fallback path.
    """
    cid = (collection_id or "").strip()
    host = _normalize_storefront_host(domain_name)
    if not cid or not host:
        return {}
    origin = _storefront_origin(commerce_base_url)
    slug = (collection_url or "").strip().strip("/")

    candidates: list[str] = [f"collections/{quote(cid, safe='')}"]
    if slug:
        candidates.append(f"collections/{quote(slug, safe='')}/{quote(cid, safe='')}")

    seen_paths: set[str] = set()
    last_data: dict = {}

    def _try_path(resource_path: str) -> dict:
        if not resource_path or resource_path in seen_paths:
            return {}
        seen_paths.add(resource_path)
        return _storefront_get_json(origin, host, resource_path, timeout=timeout)

    for resource_path in candidates:
        data = _try_path(resource_path)
        if not data:
            continue
        last_data = data
        if _collection_payload_has_products(data):
            return data
        follow_path = _storefront_redirect_path(data).lstrip("/")
        if follow_path:
            followed = _try_path(follow_path)
            if followed:
                last_data = followed
                if _collection_payload_has_products(followed):
                    return followed

    return last_data


def _storefront_collection_dict(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    root = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    if not isinstance(root, dict):
        return {}
    coll = root.get("collection")
    return coll if isinstance(coll, dict) else {}


def extract_storefront_collection_products(payload: dict) -> list[dict]:
    coll = _storefront_collection_dict(payload)
    prods = coll.get("products")
    if not isinstance(prods, list):
        return []
    return [p for p in prods if isinstance(p, dict)]


def extract_storefront_collection_name(payload: dict) -> str:
    coll = _storefront_collection_dict(payload)
    return str(coll.get("name") or coll.get("collection_name") or "").strip()
```

## 4. URL Pattern

File: `zoho_integration/urls.py`
Line: 24

```python
    path("multi/best-deals/", MultiAccountZohoBestDealsAPIView.as_view()),
```

## 5. Current Response for `{{Production}}zoho/multi/best-deals/?store_id=5&source=collection`

This request does not have a single deterministic response from code alone because the endpoint depends on existing database rows and settings.

Current code path for this exact query:

- `source=collection` selects the collection branch.
- `store_id=5` must resolve to an existing `Store` row.
- That store must have `zoho_org_id` set.
- An active `ZohoCommerceAccount` must be linked to that organization unless `account_id` is supplied separately.
- Because no `collection_id` or `collection_name` query parameter is supplied, the branch falls back to `ZOHO_BEST_DEALS_COLLECTION_ID` and then `ZOHO_BEST_DEALS_COLLECTION_NAME` from settings.
- If both are empty, the branch returns HTTP 400 with `collection_id is required for source=collection (or set ZOHO_BEST_DEALS_COLLECTION_ID, or pass collection_name=Best Deals)`.
- If a collection id is resolved but the storefront collection cannot be loaded or has no products, the branch returns HTTP 502 with `Storefront collection has no products (or could not be loaded).`

So, for the exact URL you provided, the code currently expects either a settings fallback or query params beyond `store_id` and `source`; otherwise it errors out at the collection-resolution step.

## 6. Notes on Current Behavior

The collection branch still reads `collection_name` from request query params. It is not hardcoded to `"Best Deals"` in this class.

A separate `"Best Deals"` string appears elsewhere in `zoho_integration/views.py` at line 2343, but that is outside `MultiAccountZohoBestDealsAPIView` and is not the collection branch audited here.
