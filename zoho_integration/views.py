from django.http import JsonResponse, HttpResponseRedirect
from django.conf import settings
import re
import requests
from typing import Optional
from urllib.parse import quote, urlencode
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import ZohoCommerceAccount
from .services import ZohoCommerceService
from catalog.models import Store


def _category_image_proxy_relative_path(account_id: int, organization_id: str, category_id: str) -> str:
    cid = (category_id or "").strip()
    if not cid:
        return ""
    qs = urlencode(
        {
            "account_id": account_id,
            "organization_id": str(organization_id),
            "category_id": cid,
        },
    )
    return f"/zoho/multi/categories/image/?{qs}"


def _is_top_level_category(category: dict) -> bool:
    # Prefer explicit hierarchy depth markers when available.
    level_value = category.get("level")
    depth_value = category.get("depth")
    for marker in (level_value, depth_value):
        if marker is None or marker == "":
            continue
        try:
            return int(marker) <= 0
        except (TypeError, ValueError):
            pass

    parent_candidates = (
        category.get("parent_id"),
        category.get("parent_category_id"),
        category.get("parent"),
        category.get("parent_category"),
        category.get("parentCategoryId"),
        category.get("parentCategory"),
    )
    for value in parent_candidates:
        if isinstance(value, (list, tuple, set)) and not value:
            continue
        if isinstance(value, dict):
            # Some payloads send parent as object; empty/missing-id means top-level.
            parent_obj_id = (
                value.get("id")
                or value.get("category_id")
                or value.get("parent_id")
                or value.get("parent_category_id")
            )
            if parent_obj_id in (None, "", "0", 0):
                continue
            return False
        if str(value).strip().lower() in ("none", "null"):
            continue
        if value in (None, "", "0", 0):
            continue
        return False
    return True


def _category_name(category: dict) -> str:
    return (category.get("name") or category.get("category_name") or "").strip()


def build_image_url(store_domain: str, image_url: str) -> str:
    raw = (image_url or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    domain = (store_domain or "").strip().replace("https://", "").replace("http://", "")
    if not domain:
        return ""
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return f"https://{domain}{raw}"


def build_zoho_cdn_document_url(store_domain: str, payload: dict) -> str:
    """Best-effort Zoho CDN URL from document ids (preferred) or file name."""
    domain = (store_domain or "").strip().replace("https://", "").replace("http://", "")
    if not domain:
        return ""
    top_document_id = str(payload.get("document_id") or "").strip()
    if top_document_id:
        return (
            f"https://cdn1.zohoecommerce.com/category-images/{quote(top_document_id)}/800x800"
            f"?storefront_domain={domain}"
        )
    rows = payload.get("documents") or payload.get("attachments") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_document_id = str(row.get("document_id") or "").strip()
            if row_document_id:
                return (
                    f"https://cdn1.zohoecommerce.com/category-images/{quote(row_document_id)}/800x800"
                    f"?storefront_domain={domain}"
                )
            file_name = str(row.get("file_name") or row.get("name") or "").strip()
            if file_name:
                return f"https://cdn1.zohoecommerce.com/{quote(file_name)}?storefront_domain={domain}"
    file_name = str(payload.get("file_name") or "").strip()
    if file_name:
        return f"https://cdn1.zohoecommerce.com/{quote(file_name)}?storefront_domain={domain}"
    return ""


def build_zoho_cdn_product_document_url(store_domain: str, payload: dict) -> str:
    """Best-effort Zoho product image URL from product document ids."""
    domain = (store_domain or "").strip().replace("https://", "").replace("http://", "")
    if not domain:
        return ""
    top_document_id = str(payload.get("document_id") or "").strip()
    if top_document_id:
        return (
            f"https://cdn1.zohoecommerce.com/product-images/{quote(top_document_id)}/800x800"
            f"?storefront_domain={domain}"
        )
    rows = payload.get("documents") or payload.get("attachments") or payload.get("images") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_document_id = str(row.get("document_id") or row.get("id") or "").strip()
            if row_document_id:
                return (
                    f"https://cdn1.zohoecommerce.com/product-images/{quote(row_document_id)}/800x800"
                    f"?storefront_domain={domain}"
                )
    return ""


def _category_summary(category: dict, fallback_image_url: str = "", store_domain: str = "") -> dict:
    extracted = _extract_image_url(category)
    image_url = build_image_url(store_domain, extracted) or extracted
    if not image_url:
        image_url = build_zoho_cdn_document_url(store_domain, category)
    if not image_url:
        image_url = fallback_image_url
    return {
        "category_id": str(category.get("category_id") or category.get("id") or "").strip(),
        "name": _category_name(category),
        "url": category.get("url") or "",
        "sibling_order": category.get("sibling_order", 0),
        "image_url": image_url,
    }


def _first_present_value(payload: dict, keys: list[str]):
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", "0", 0, "0.00"):
            return value
    return None


def _extract_price(payload: dict) -> str:
    direct_price = _first_present_value(
        payload,
        [
            "rate",
            "price",
            "selling_price",
            "sales_rate",
            "list_price",
            "actual_price",
            "mrp",
        ],
    )
    if direct_price is not None:
        return str(direct_price)

    variants = payload.get("variants") or payload.get("variant_list") or []
    if isinstance(variants, list):
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            v_price = _first_present_value(
                variant,
                [
                    "rate",
                    "price",
                    "selling_price",
                    "sales_rate",
                    "list_price",
                    "actual_price",
                    "mrp",
                ],
            )
            if v_price is not None:
                return str(v_price)
    return "0"


def _extract_image_url(payload: dict) -> str:
    def _looks_like_image_url(value: str) -> bool:
        v = (value or "").strip().lower()
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("/")):
            return False
        image_markers = (
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".avif", ".ico",
            "/image", "/images", "image_id=", "imagetype=", "product-images",
        )
        return any(marker in v for marker in image_markers)

    direct = _first_present_value(
        payload,
        [
            "image_url",
            "image_name",
            "image",
            "product_image",
            "image_path",
            "image_src",
            "thumbnail_url",
        ],
    )
    if direct is not None:
        candidate = str(direct).replace("&amp;", "&").strip()
        if _looks_like_image_url(candidate):
            return candidate

    for list_key in ("images", "product_images", "documents", "attachments"):
        rows = payload.get(list_key) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = _first_present_value(
                row,
                [
                    "image_url",
                    "url",
                    "download_url",
                    "secure_url",
                    "file_url",
                    "src",
                    "thumbnail_url",
                ],
            )
            if url is not None:
                candidate = str(url).replace("&amp;", "&").strip()
                if _looks_like_image_url(candidate):
                    return candidate

    # Some Zoho category payloads embed image tags in HTML fields.
    for html_key in ("description_html", "description", "category_content", "content"):
        raw_html = payload.get(html_key)
        if not raw_html:
            continue
        text = str(raw_html)
        match = re.search(r"""<img[^>]+src=["']([^"']+)["']""", text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).replace("&amp;", "&").strip()
            if _looks_like_image_url(candidate):
                return candidate

    # Last-resort recursive scan for nested payloads returned by different Zoho endpoints.
    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).lower()
                if isinstance(v, str):
                    val = v.strip()
                    if not val:
                        continue
                    if key in ("image_url", "thumbnail_url", "src", "file_url", "download_url"):
                        candidate = val.replace("&amp;", "&")
                        if _looks_like_image_url(candidate):
                            return candidate
                    img = re.search(r"""<img[^>]+src=["']([^"']+)["']""", val, flags=re.IGNORECASE)
                    if img:
                        candidate = img.group(1).replace("&amp;", "&").strip()
                        if _looks_like_image_url(candidate):
                            return candidate
                    if _looks_like_image_url(val):
                        return val.replace("&amp;", "&")
                elif isinstance(v, (dict, list)):
                    nested = _walk(v)
                    if nested:
                        return nested
        elif isinstance(node, list):
            for row in node:
                nested = _walk(row)
                if nested:
                    return nested
        return ""

    nested_url = _walk(payload)
    if nested_url:
        return nested_url
    return ""


def _product_summary(product: dict, store_domain: str = "") -> dict:
    raw_image = _extract_image_url(product)
    image_url = raw_image
    if not image_url:
        image_url = build_zoho_cdn_product_document_url(store_domain, product)
    return {
        "product_id": str(product.get("product_id") or product.get("item_id") or product.get("id") or "").strip(),
        "product_name": (
            product.get("name")
            or product.get("product_name")
            or product.get("item_name")
            or ""
        ),
        "sku": product.get("sku") or product.get("product_sku") or "",
        "price": _extract_price(product),
        "image_url": image_url,
    }


def _extract_description(payload: dict) -> str:
    return str(
        payload.get("description")
        or payload.get("product_description")
        or payload.get("short_description")
        or payload.get("long_description")
        or payload.get("description_html")
        or ""
    ).strip()


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _collect_category_and_descendants(categories: list[dict], root_category_id: str) -> list[str]:
    root_id = str(root_category_id or "").strip()
    if not root_id:
        return []

    children_map: dict[str, list[str]] = {}
    for c in categories:
        cid = str(c.get("category_id") or c.get("id") or "").strip()
        parent_id = str(c.get("parent_category_id") or c.get("parent_id") or "").strip()
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


def _menu_categories_for_response(categories: list[dict]) -> list[dict]:
    """
    Build user-facing menu categories from Zoho payload.
    - Exclude technical root nodes.
    - Prefer children under a "Categories" container if present.
    - Fallback to visible, non-root categories.
    """
    if not categories:
        return []

    # Pattern A: some stores (e.g. AoneSpices) place menu categories
    # as children under a container named "Categories".
    container_ids = {
        str(c.get("category_id") or c.get("id") or "").strip()
        for c in categories
        if _category_name(c).lower() == "categories"
    }
    if container_ids:
        container_children: list[dict] = []
        seen_child_ids: set[str] = set()
        for c in categories:
            if c.get("visibility") is False:
                continue
            parent_id = str(c.get("parent_category_id") or c.get("parent_id") or "").strip()
            if parent_id not in container_ids:
                continue
            cid = str(c.get("category_id") or c.get("id") or "").strip()
            if cid and cid in seen_child_ids:
                continue
            container_children.append(c)
            if cid:
                seen_child_ids.add(cid)
        if container_children:
            return sorted(
                container_children,
                key=lambda x: (x.get("sibling_order", 0), _category_name(x).lower()),
            )

    # Pattern B: stores like Doorde expose menu categories at depth 0.
    menu: list[dict] = []
    seen_ids: set[str] = set()
    for c in categories:
        name = _category_name(c).lower()
        if name in ("root", "categories"):
            continue
        if c.get("visibility") is False:
            continue

        category_id = str(c.get("category_id") or c.get("id") or "").strip()
        if category_id and category_id in seen_ids:
            continue

        # Prefer explicit depth for menu-level categories.
        depth = c.get("depth")
        if depth not in (None, ""):
            try:
                if int(depth) != 0:
                    continue
            except (TypeError, ValueError):
                pass
        else:
            # Fallback if depth is missing.
            if not _is_top_level_category(c):
                continue

        menu.append(c)
        if category_id:
            seen_ids.add(category_id)

    return sorted(menu, key=lambda x: (x.get("sibling_order", 0), _category_name(x).lower()))


def _mask_token(value: Optional[str]) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if len(token) <= 12:
        return f"{token[:3]}***"
    return f"{token[:6]}...{token[-6:]}"


def zoho_callback(request):
    code = request.GET.get("code")
    location = request.GET.get("location")
    accounts_server = request.GET.get("accounts-server")
    account_id = (request.GET.get("account_id") or "").strip()

    if not code:
        return JsonResponse({
            "status": "error",
            "message": "No authorization code received",
            "query_params": dict(request.GET),
        }, status=400)

    account = None
    if account_id:
        try:
            account = ZohoCommerceAccount.objects.get(id=int(account_id), is_active=True)
        except (TypeError, ValueError, ZohoCommerceAccount.DoesNotExist):
            return JsonResponse({
                "status": "error",
                "message": "Invalid account_id or account not found",
            }, status=400)

    accounts_base = (
        account.accounts_url
        if account is not None
        else getattr(settings, "ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
    ).rstrip("/")
    client_id = (
        account.client_id
        if account is not None
        else getattr(settings, "ZOHO_CLIENT_ID", "")
    )
    client_secret = (
        account.client_secret
        if account is not None
        else getattr(settings, "ZOHO_CLIENT_SECRET", "")
    )
    redirect_uri = getattr(settings, "ZOHO_REDIRECT_URI", "").strip()
    token_url = f"{accounts_base}/oauth/v2/token"

    if not client_id or not client_secret:
        return JsonResponse({
            "status": "error",
            "message": "Missing Zoho client credentials. Configure account credentials or .env values.",
        }, status=400)

    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }

    try:
        response = requests.post(token_url, data=payload, timeout=30)
        raw_text = response.text

        try:
            token_data = response.json()
        except ValueError:
            token_data = {"non_json_response": raw_text}

        if not response.ok or "error" in token_data:
            return JsonResponse({
                "status": "error",
                "message": "Zoho token exchange failed",
                "http_status": response.status_code,
                "token_url": token_url,
                "request_payload_preview": {
                    "grant_type": payload["grant_type"],
                    "client_id": f"{client_id[:8]}..." if client_id else "",
                    "redirect_uri": payload["redirect_uri"],
                    "code_preview": code[:10] + "...",
                },
                "response_data": token_data,
                "account_id": account.id if account else None,
                "location": location,
                "accounts_server": accounts_server,
            }, status=400)

        if account is not None and token_data.get("refresh_token"):
            account.refresh_token = token_data.get("refresh_token")
            account.save(update_fields=["refresh_token"])

        return JsonResponse({
            "status": "success",
            "message": "Zoho token generated successfully",
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "api_domain": token_data.get("api_domain"),
            "token_type": token_data.get("token_type"),
            "account_id": account.id if account else None,
            "location": location,
            "accounts_server": accounts_server,
        })

    except requests.RequestException as e:
        return JsonResponse({
            "status": "error",
            "message": "Request to Zoho failed",
            "details": str(e),
        }, status=500)

        
def get_zoho_access_token():
    url = f"{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    payload = {
        "refresh_token": settings.ZOHO_REFRESH_TOKEN,
        "client_id": settings.ZOHO_CLIENT_ID,
        "client_secret": settings.ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }

    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    return response.json()["access_token"]
def get_all_zoho_stores():
    access_token = get_zoho_access_token()

    url = f"{settings.ZOHO_COMMERCE_BASE_URL}/zs-site/api/v1/index/sites"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def zoho_debug_sites(request):
    """
    Temporary diagnostics endpoint for Zoho refresh + sites listing.
    Returns sanitized/masked values only.
    """
    token_url = f"{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    payload = {
        "refresh_token": settings.ZOHO_REFRESH_TOKEN,
        "client_id": settings.ZOHO_CLIENT_ID,
        "client_secret": settings.ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }

    override_base = (request.GET.get("base_url") or "").strip().rstrip("/")
    base_url = override_base or getattr(settings, "ZOHO_COMMERCE_BASE_URL", "")

    debug = {
        "status": "error",
        "config": {
            "accounts_url": getattr(settings, "ZOHO_ACCOUNTS_URL", ""),
            "commerce_base_url": base_url,
            "commerce_base_url_from_query": bool(override_base),
            "client_id_masked": _mask_token(getattr(settings, "ZOHO_CLIENT_ID", "")),
            "refresh_token_masked": _mask_token(getattr(settings, "ZOHO_REFRESH_TOKEN", "")),
        },
    }

    try:
        token_resp = requests.post(token_url, data=payload, timeout=30)
    except requests.RequestException as e:
        debug["message"] = "Failed to call Zoho token endpoint"
        debug["token_refresh"] = {"error": str(e)}
        return JsonResponse(debug, status=502)

    token_body_text = (token_resp.text or "").strip()
    token_body_preview = token_body_text[:800] if token_body_text else ""
    token_data = {}
    try:
        token_data = token_resp.json()
    except ValueError:
        token_data = {}

    access_token = (token_data.get("access_token") or "").strip()
    debug["token_refresh"] = {
        "http_status": token_resp.status_code,
        "ok": token_resp.ok,
        "scope": token_data.get("scope"),
        "expires_in": token_data.get("expires_in"),
        "token_type": token_data.get("token_type"),
        "access_token_masked": _mask_token(access_token),
        "body_preview": token_body_preview,
    }

    if not token_resp.ok or not access_token:
        debug["message"] = "Refresh token exchange failed"
        return JsonResponse(debug, status=400)

    if not (str(base_url).startswith("http://") or str(base_url).startswith("https://")):
        debug["message"] = "Invalid base_url. Must start with http:// or https://"
        return JsonResponse(debug, status=400)

    sites_url = f"{base_url}/zs-site/api/v1/index/sites"
    sites_headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }
    try:
        sites_resp = requests.get(sites_url, headers=sites_headers, timeout=30)
    except requests.RequestException as e:
        debug["message"] = "Token refreshed but sites endpoint request failed"
        debug["sites_call"] = {"error": str(e)}
        return JsonResponse(debug, status=502)

    sites_body_text = (sites_resp.text or "").strip()
    sites_body_preview = sites_body_text[:800] if sites_body_text else ""
    sites_data = {}
    try:
        sites_data = sites_resp.json()
    except ValueError:
        sites_data = {}

    debug["sites_call"] = {
        "http_status": sites_resp.status_code,
        "ok": sites_resp.ok,
        "url": sites_url,
        "body_preview": sites_body_preview,
    }

    if sites_resp.ok:
        my_sites = (
            (sites_data.get("get_sites") or {}).get("my_sites")
            if isinstance(sites_data, dict)
            else None
        )
        debug["status"] = "success"
        debug["message"] = "Zoho token refresh and sites call succeeded"
        debug["result"] = {
            "site_count": len(my_sites) if isinstance(my_sites, list) else 0,
            "domains": [
                s.get("primary_domain", "")
                for s in my_sites
                if isinstance(s, dict)
            ] if isinstance(my_sites, list) else [],
        }
        return JsonResponse(debug, status=200)

    debug["message"] = "Zoho sites call failed after successful token refresh"
    return JsonResponse(debug, status=400)


class MultiAccountZohoStoreListAPIView(APIView):
    def get(self, request):
        accounts = ZohoCommerceAccount.objects.filter(is_active=True)

        result = []
        errors = []

        for account in accounts:
            service = ZohoCommerceService(account)
            try:
                data = service.list_stores()

                # Zoho returns sites under get_sites.my_sites (see zs-site index API).
                stores = []
                if isinstance(data, dict):
                    gs = data.get("get_sites") or {}
                    if isinstance(gs, dict):
                        my_sites = gs.get("my_sites")
                        if isinstance(my_sites, list):
                            stores = [s for s in my_sites if isinstance(s, dict)]
                    if not stores:
                        raw = data.get("sites") or data.get("stores") or []
                        stores = [s for s in raw if isinstance(s, dict)]
                for store in stores:
                    result.append({
                        "account_id": account.id,
                        "account_name": account.name,
                        "account_email": account.email,
                        "store_id": store.get("zsite_id") or store.get("store_id"),
                        "site_name": store.get("site_title") or store.get("site_name"),
                        "primary_domain": store.get("primary_domain") or store.get("domain"),
                        "organization_id": store.get("zohofinance_orgid") or store.get("organization_id"),
                    })
            except Exception as e:
                errors.append({
                    "account_name": account.name,
                    "account_email": account.email,
                    "error": str(e),
                })

        return Response({
            "status": "success",
            "count": len(result),
            "stores": result,
            "errors": errors,
        }, status=status.HTTP_200_OK)


def _multi_account_product_list_response(request, account, organization_id: str):
    service = ZohoCommerceService(account)
    category_id = (request.GET.get("category_id") or "").strip() or None
    include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)

    if category_id and include_descendants:
        category_data = service.list_categories(organization_id=organization_id)
        category_rows = category_data.get("categories", []) or category_data.get("category", [])
        category_rows = [c for c in category_rows if isinstance(c, dict)]
        category_ids = _collect_category_and_descendants(category_rows, category_id)

        products = []
        seen_product_ids: set[str] = set()
        for current_category_id in category_ids:
            data = service.list_products(
                organization_id=organization_id,
                category_id=current_category_id,
            )
            rows = data.get("products", []) or data.get("items", [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pid = str(
                    row.get("product_id")
                    or row.get("item_id")
                    or row.get("id")
                    or ""
                ).strip()
                if pid and pid in seen_product_ids:
                    continue
                if pid:
                    seen_product_ids.add(pid)
                products.append(row)
    else:
        data = service.list_products(
            organization_id=organization_id,
            category_id=category_id,
        )
        products = data.get("products", []) or data.get("items", [])
    products = [p for p in products if isinstance(p, dict)]

    # Enrich missing prices from product detail endpoint.
    for product in products:
        if _extract_price(product) not in ("0", "0.00"):
            continue
        pid = str(product.get("product_id") or product.get("item_id") or product.get("id") or "").strip()
        if not pid:
            continue
        try:
            detail_data = service.get_product_detail(
                organization_id=organization_id,
                product_id=pid,
            )
        except Exception:
            continue

        detail_product = (
            detail_data.get("product")
            or detail_data.get("item")
            or detail_data.get("data")
            or {}
        )
        if isinstance(detail_product, dict):
            detail_price = _extract_price(detail_product)
            if detail_price not in ("0", "0.00"):
                product["rate"] = detail_price
            if not (product.get("sku") or product.get("product_sku")):
                detail_sku = detail_product.get("sku") or detail_product.get("product_sku")
                if detail_sku:
                    product["sku"] = detail_sku
            if not (product.get("image_url") or product.get("image_name")):
                detail_image = _extract_image_url(detail_product)
                if detail_image:
                    product["image_url"] = detail_image

    store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
    store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
    product_summaries = [_product_summary(p, store_domain=store_domain) for p in products]
    if store:
        for row in product_summaries:
            current_image = (row.get("image_url") or "").strip()
            normalized_image = build_image_url(store_domain, current_image)
            if normalized_image:
                row["image_url"] = normalized_image
                continue
            # Zoho sometimes returns only an image filename (not a usable URL).
            # In that case, replace it with our proxy URL.
            if current_image and (
                current_image.startswith("http://")
                or current_image.startswith("https://")
                or current_image.startswith("/")
            ):
                continue
            pid = (row.get("product_id") or "").strip()
            if not pid:
                continue
            row["image_url"] = request.build_absolute_uri(
                f"/api/shop/zoho-products/{pid}/image/?store_id={store.pk}"
            )

    return Response({
        "status": "success",
        "account_name": account.name,
        "account_email": account.email,
        "organization_id": organization_id,
        "category_id": category_id,
        "include_descendants": include_descendants,
        "count": len(product_summaries),
        "products": product_summaries,
    })


class MultiAccountZohoProductListAPIView(APIView):
    def get(self, request, account_id, organization_id):
        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Zoho account not found"
            }, status=404)

        try:
            return _multi_account_product_list_response(
                request=request,
                account=account,
                organization_id=organization_id,
            )
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


class MultiAccountZohoProductListQueryAPIView(APIView):
    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        if not account_id_raw:
            return Response(
                {"status": "error", "message": "account_id query parameter is required"},
                status=400,
            )
        if not organization_id:
            return Response(
                {"status": "error", "message": "organization_id query parameter is required"},
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
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Zoho account not found"
            }, status=404)

        try:
            return _multi_account_product_list_response(
                request=request,
                account=account,
                organization_id=organization_id,
            )
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


class MultiAccountZohoProductSearchAPIView(APIView):
    """
    Query params:
      - account_id (required)
      - organization_id (required)
      - q (required): case-insensitive search text
      - limit (optional, default=20, max=100)
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        query = (request.GET.get("q") or "").strip()
        limit_raw = (request.GET.get("limit") or "").strip()

        if not account_id_raw:
            return Response(
                {"status": "error", "message": "account_id query parameter is required"},
                status=400,
            )
        if not organization_id:
            return Response(
                {"status": "error", "message": "organization_id query parameter is required"},
                status=400,
            )
        if not query:
            return Response(
                {"status": "error", "message": "q query parameter is required"},
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
            limit = int(limit_raw) if limit_raw else 20
        except ValueError:
            return Response(
                {"status": "error", "message": "limit must be an integer"},
                status=400,
            )
        if limit < 1:
            limit = 1
        if limit > 100:
            limit = 100

        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response(
                {"status": "error", "message": "Zoho account not found"},
                status=404,
            )

        service = ZohoCommerceService(account)
        try:
            data = service.list_products(organization_id=organization_id, page=1, per_page=200)
            products = data.get("products", []) or data.get("items", [])
            products = [p for p in products if isinstance(p, dict)]
            needle = query.lower()

            matched_rows = []
            for row in products:
                product_name = str(
                    row.get("name")
                    or row.get("product_name")
                    or row.get("item_name")
                    or ""
                ).strip()
                sku = str(row.get("sku") or row.get("product_sku") or "").strip()
                product_id = str(row.get("product_id") or row.get("item_id") or row.get("id") or "").strip()

                haystack = " ".join([product_name, sku, product_id]).lower()
                if needle in haystack:
                    matched_rows.append(row)

            store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
            store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
            product_summaries = [_product_summary(p, store_domain=store_domain) for p in matched_rows]
            if store:
                for row in product_summaries:
                    current_image = (row.get("image_url") or "").strip()
                    normalized_image = build_image_url(store_domain, current_image)
                    if normalized_image:
                        row["image_url"] = normalized_image
                        continue
                    if current_image and (
                        current_image.startswith("http://")
                        or current_image.startswith("https://")
                        or current_image.startswith("/")
                    ):
                        continue
                    pid = (row.get("product_id") or "").strip()
                    if not pid:
                        continue
                    row["image_url"] = request.build_absolute_uri(
                        f"/api/shop/zoho-products/{pid}/image/?store_id={store.pk}"
                    )

            product_summaries = product_summaries[:limit]
            return Response(
                {
                    "status": "success",
                    "account_id": account.id,
                    "account_name": account.name,
                    "account_email": account.email,
                    "organization_id": organization_id,
                    "q": query,
                    "count": len(product_summaries),
                    "products": product_summaries,
                },
                status=200,
            )
        except Exception as e:
            return Response(
                {"status": "error", "message": str(e)},
                status=400,
            )


class MultiAccountZohoProductDetailQueryAPIView(APIView):
    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        product_id = (request.GET.get("product_id") or "").strip()

        if not account_id_raw:
            return Response(
                {"status": "error", "message": "account_id query parameter is required"},
                status=400,
            )
        if not organization_id:
            return Response(
                {"status": "error", "message": "organization_id query parameter is required"},
                status=400,
            )
        if not product_id:
            return Response(
                {"status": "error", "message": "product_id query parameter is required"},
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
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response(
                {"status": "error", "message": "Zoho account not found"},
                status=404,
            )

        try:
            service = ZohoCommerceService(account)
            detail_data = service.get_product_detail(
                organization_id=organization_id,
                product_id=product_id,
            )
            product = (
                detail_data.get("product")
                or detail_data.get("item")
                or detail_data.get("data")
                or detail_data
            )
            if not isinstance(product, dict):
                return Response(
                    {"status": "error", "message": "Invalid Zoho product detail payload"},
                    status=502,
                )

            store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
            store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
            summary = _product_summary(product, store_domain=store_domain)
            image_url = (summary.get("image_url") or "").strip()
            # If Zoho returned only a filename (e.g. "WhatsApp Image ... .jpeg"),
            # it isn't a usable URL for the frontend—force proxy fallback.
            if image_url and not (
                image_url.startswith("http://")
                or image_url.startswith("https://")
                or image_url.startswith("/")
            ):
                image_url = ""
            if not image_url and store and product_id:
                image_url = request.build_absolute_uri(
                    f"/api/shop/zoho-products/{product_id}/image/?store_id={store.pk}"
                )

            currency = str(
                product.get("currency_code")
                or product.get("currency")
                or "AED"
            ).strip() or "AED"

            primary_domain = ""
            if store and (store.zoho_store_domain or "").strip():
                primary_domain = store.zoho_store_domain.strip()
            elif "://" in str(product.get("product_url") or ""):
                primary_domain = str(product.get("product_url")).split("/")[2]

            return Response({
                "status": "success",
                "account_id": account.id,
                "account_name": account.name,
                "account_email": account.email,
                "organization_id": str(organization_id),
                "product": {
                    "product_id": summary.get("product_id") or product_id,
                    "product_name": summary.get("product_name"),
                    "sku": summary.get("sku"),
                    "price": summary.get("price"),
                    "currency": currency,
                    "description": _extract_description(product),
                    "image_url": image_url,
                },
                "add_to_cart": {
                    "endpoint": request.build_absolute_uri("/api/shop/cart/items/"),
                    "method": "POST",
                    "payload": {
                        "zoho_account_id": account.id,
                        "organization_id": str(organization_id),
                        "zoho_product_id": summary.get("product_id") or product_id,
                        "quantity": 1,
                        "primary_domain": primary_domain,
                    },
                },
            }, status=200)
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


def _multi_account_category_list_response(request, account, organization_id: str):
    service = ZohoCommerceService(account)
    data = service.list_categories(organization_id=organization_id)
    categories = data.get("categories", []) or data.get("category", [])
    categories = [c for c in categories if isinstance(c, dict)]
    placeholder_url = str(getattr(settings, "ZOHO_IMAGE_PLACEHOLDER_URL", "") or "").strip()

    # Optional query params:
    # - category_id: return categories under this parent/root category
    # - include_descendants: whether to include all descendants (default true)
    query_category_id = (request.GET.get("category_id") or "").strip() or None
    include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)

    if query_category_id:
        root_id = str(query_category_id).strip()
        visible_categories = [c for c in categories if c.get("visibility") is not False]
        if include_descendants:
            category_ids = set(_collect_category_and_descendants(visible_categories, root_id))
        else:
            category_ids = set()
            for c in visible_categories:
                cid = str(c.get("category_id") or c.get("id") or "").strip()
                if not cid:
                    continue
                parent_id = str(c.get("parent_category_id") or c.get("parent_id") or "").strip()
                if parent_id == root_id:
                    category_ids.add(cid)

        filtered_categories = [
            c for c in visible_categories
            if str(c.get("category_id") or c.get("id") or "").strip() in category_ids
        ]
        main_categories = sorted(
            filtered_categories,
            key=lambda x: (x.get("sibling_order", 0), _category_name(x).lower()),
        )
    else:
        # Default: return top-level menu categories (current behavior).
        main_categories = _menu_categories_for_response(categories)

    store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
    store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
    by_parent: dict[str, list[dict]] = {}
    for row in categories:
        parent_id = str(
            row.get("parent_category_id")
            or row.get("parent_id")
            or row.get("parent")
            or ""
        ).strip()
        by_parent.setdefault(parent_id, []).append(row)

    def _first_descendant_with_image(root_category_id: str):
        queue = list(by_parent.get(root_category_id, []))
        seen: set[str] = set()
        while queue:
            row = queue.pop(0)
            cid = str(row.get("category_id") or row.get("id") or "").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            candidate = (
                _extract_image_url(row)
                or build_zoho_cdn_document_url(store_domain, row)
            )
            if candidate:
                return row
            queue.extend(by_parent.get(cid, []))
        return None

    def _descendant_category_ids(root_category_id: str) -> list[str]:
        queue = [root_category_id]
        seen: set[str] = set()
        result: list[str] = []
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            result.append(current)
            for row in by_parent.get(current, []):
                child_id = str(row.get("category_id") or row.get("id") or "").strip()
                if child_id and child_id not in seen:
                    queue.append(child_id)
        return result

    enriched_categories: list[dict] = []
    for c in main_categories:
        if not _category_name(c):
            continue
        cid = str(c.get("category_id") or c.get("id") or "").strip()
        if not cid:
            continue
        row = dict(c)
        direct_image = (
            row.get("image_url")
            or row.get("image")
            or row.get("image_name")
            or row.get("image_path")
            or ""
        )
        if not direct_image and not row.get("document_id"):
            try:
                detail = service.get_category_detail(
                    organization_id=str(organization_id),
                    category_id=cid,
                )
                detail_row = (
                    detail.get("category")
                    or detail.get("data")
                    or {}
                )
                if isinstance(detail_row, dict):
                    for key in ("image_url", "image", "image_name", "image_path", "document_id", "documents"):
                        if key in detail_row and detail_row.get(key) not in (None, "", []):
                            row[key] = detail_row.get(key)
            except Exception:
                pass
        if not _extract_image_url(row) and not build_zoho_cdn_document_url(store_domain, row):
            descendant = _first_descendant_with_image(cid)
            if isinstance(descendant, dict):
                row = dict(row)
                for key in ("image_url", "image", "image_name", "image_path", "document_id", "documents"):
                    if descendant.get(key) not in (None, "", []):
                        row[key] = descendant.get(key)
        if not _extract_image_url(row) and not build_zoho_cdn_document_url(store_domain, row):
            # Final fallback: use first product image inside this category,
            # so image_url remains a real Zoho CDN URL.
            try:
                search_category_ids = _descendant_category_ids(cid)
                for search_cid in search_category_ids:
                    product_data = service.list_products(
                        organization_id=str(organization_id),
                        category_id=search_cid,
                        page=1,
                        per_page=50,
                    )
                    product_rows = product_data.get("products", []) or product_data.get("items", [])
                    if not isinstance(product_rows, list):
                        continue
                    found_product_image = ""
                    for product_row in product_rows:
                        if not isinstance(product_row, dict):
                            continue
                        p_image = _extract_image_url(product_row)
                        p_image = build_image_url(store_domain, p_image) or p_image
                        if not p_image:
                            p_image = build_zoho_cdn_product_document_url(store_domain, product_row)
                        if p_image:
                            found_product_image = p_image
                            break
                    if found_product_image:
                        row = dict(row)
                        row["image_url"] = found_product_image
                        break
            except Exception:
                pass
        enriched_categories.append(row)

    main_categories = [
        _category_summary(
            c,
            fallback_image_url=placeholder_url,
            store_domain=store_domain,
        )
        for c in enriched_categories
    ]

    return Response({
        "status": "success",
        "account_id": account.id,
        "account_name": account.name,
        "account_email": account.email,
        "organization_id": organization_id,
        "category_id": query_category_id,
        "include_descendants": include_descendants,
        "count": len(main_categories),
        "categories": main_categories,
    })


class MultiAccountZohoCategoryListAPIView(APIView):
    def get(self, request, account_id, organization_id):
        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Zoho account not found"
            }, status=404)
        try:
            return _multi_account_category_list_response(request, account, str(organization_id))
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


class MultiAccountZohoCategoryListQueryAPIView(APIView):
    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        if not account_id_raw:
            return Response(
                {"status": "error", "message": "account_id query parameter is required"},
                status=400,
            )
        if not organization_id:
            return Response(
                {"status": "error", "message": "organization_id query parameter is required"},
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
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response(
                {"status": "error", "message": "Zoho account not found"},
                status=404,
            )
        try:
            return _multi_account_category_list_response(request, account, organization_id)
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


class MultiAccountZohoCategorySearchAPIView(APIView):
    """
    Query params:
      - account_id (required)
      - organization_id (required)
      - q (required): case-insensitive search text
      - limit (optional, default=20, max=100)
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        query = (request.GET.get("q") or "").strip()
        limit_raw = (request.GET.get("limit") or "").strip()

        if not account_id_raw:
            return Response(
                {"status": "error", "message": "account_id query parameter is required"},
                status=400,
            )
        if not organization_id:
            return Response(
                {"status": "error", "message": "organization_id query parameter is required"},
                status=400,
            )
        if not query:
            return Response(
                {"status": "error", "message": "q query parameter is required"},
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
            limit = int(limit_raw) if limit_raw else 20
        except ValueError:
            return Response(
                {"status": "error", "message": "limit must be an integer"},
                status=400,
            )
        if limit < 1:
            limit = 1
        if limit > 100:
            limit = 100

        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response(
                {"status": "error", "message": "Zoho account not found"},
                status=404,
            )

        service = ZohoCommerceService(account)
        try:
            data = service.list_categories(organization_id=organization_id)
            categories = data.get("categories", []) or data.get("category", [])
            categories = [c for c in categories if isinstance(c, dict)]
            needle = query.lower()

            matched = []
            store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
            store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
            for c in categories:
                name = _category_name(c)
                if not name:
                    continue
                if needle in name.lower():
                    category_id = str(c.get("category_id") or c.get("id") or "").strip()
                    fallback_image_url = (
                        request.build_absolute_uri(
                            _category_image_proxy_relative_path(
                                account.id, str(organization_id), category_id,
                            ),
                        ) if category_id else ""
                    )
                    summary = _category_summary(
                        c,
                        fallback_image_url=fallback_image_url,
                        store_domain=store_domain,
                    )
                    if fallback_image_url:
                        # Keep category search image behavior aligned with category list:
                        # always return our proxy endpoint URL.
                        summary["image_url"] = fallback_image_url
                    matched.append(
                        summary
                    )

            matched = sorted(
                matched,
                key=lambda x: (x.get("sibling_order", 0), str(x.get("name") or "").lower()),
            )[:limit]

            return Response(
                {
                    "status": "success",
                    "account_id": account.id,
                    "account_name": account.name,
                    "account_email": account.email,
                    "organization_id": organization_id,
                    "q": query,
                    "count": len(matched),
                    "categories": matched,
                },
                status=200,
            )
        except Exception as e:
            return Response(
                {"status": "error", "message": str(e)},
                status=400,
            )


def _category_image_proxy_redirect(request, account_id, organization_id, category_id):
    try:
        account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
    except ZohoCommerceAccount.DoesNotExist:
        return Response({"detail": "Zoho account not found"}, status=status.HTTP_404_NOT_FOUND)

    service = ZohoCommerceService(account)

    try:
        data = service.list_categories(organization_id=organization_id)
        categories = data.get("categories", []) or data.get("category", [])
        categories = [c for c in categories if isinstance(c, dict)]
        match = next(
            (
                c for c in categories
                if str(c.get("category_id") or c.get("id") or "").strip() == str(category_id).strip()
            ),
            None,
        )
        if not match:
            # Backward-compatible fallback: some clients pass product_id in this slot.
            return _product_image_proxy_redirect(
                request,
                account_id=account_id,
                organization_id=organization_id,
                product_id=category_id,
            )

        image_url = _extract_image_url(match)
        detail_row = {}
        fallback_row = None
        if not image_url:
            try:
                detail = service.get_category_detail(
                    organization_id=organization_id,
                    category_id=category_id,
                )
            except Exception:
                detail = {}
            detail_row = (
                detail.get("category")
                or detail.get("data")
                or {}
            )
            if isinstance(detail_row, dict):
                image_url = _extract_image_url(detail_row)

        # Fallback: if this category has no own image in Zoho payload,
        # try descendants (children/grandchildren) and use the first image found.
        if not image_url:
            wanted_parent = str(category_id).strip()
            by_parent = {}
            for row in categories:
                parent_id = str(
                    row.get("parent_category_id")
                    or row.get("parent_id")
                    or row.get("parent")
                    or ""
                ).strip()
                by_parent.setdefault(parent_id, []).append(row)

            queue = list(by_parent.get(wanted_parent, []))
            seen = set()
            while queue and not image_url:
                row = queue.pop(0)
                row_id = str(row.get("category_id") or row.get("id") or "").strip()
                if not row_id or row_id in seen:
                    continue
                seen.add(row_id)
                if fallback_row is None:
                    fallback_row = row
                image_url = _extract_image_url(row)
                if image_url:
                    break
                queue.extend(by_parent.get(row_id, []))

        store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
        store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
        image_url = build_image_url(store_domain, image_url) or image_url
        if not image_url:
            image_url = build_zoho_cdn_document_url(store_domain, match)
        if not image_url and isinstance(detail_row, dict):
            image_url = build_zoho_cdn_document_url(store_domain, detail_row)
        if not image_url and isinstance(fallback_row, dict):
            image_url = build_zoho_cdn_document_url(store_domain, fallback_row)

        if not image_url:
            placeholder_url = str(getattr(settings, "ZOHO_IMAGE_PLACEHOLDER_URL", "") or "").strip()
            if placeholder_url.startswith("http://") or placeholder_url.startswith("https://"):
                return HttpResponseRedirect(placeholder_url)
            return Response(
                {"detail": "No image URL available for this category."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return HttpResponseRedirect(image_url)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class MultiAccountZohoCategoryImageProxyAPIView(APIView):
    def get(self, request, account_id, organization_id, category_id):
        return _category_image_proxy_redirect(request, account_id, organization_id, category_id)


class MultiAccountZohoCategoryImageQueryAPIView(APIView):
    """
    Same as path-based category image proxy, but IDs are query params:
    ?account_id=&organization_id=&category_id=
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        category_id = (request.GET.get("category_id") or "").strip()
        if not account_id_raw:
            return Response(
                {"detail": "account_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not organization_id:
            return Response(
                {"detail": "organization_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not category_id:
            return Response(
                {"detail": "category_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            account_id = int(account_id_raw)
        except (TypeError, ValueError):
            return Response(
                {"detail": "account_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _category_image_proxy_redirect(request, account_id, organization_id, category_id)


def _product_image_proxy_redirect(request, account_id, organization_id, product_id):
    try:
        account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
    except ZohoCommerceAccount.DoesNotExist:
        return Response({"detail": "Zoho account not found"}, status=status.HTTP_404_NOT_FOUND)

    service = ZohoCommerceService(account)
    try:
        detail_data = service.get_product_detail(
            organization_id=organization_id,
            product_id=product_id,
        )
        product = (
            detail_data.get("product")
            or detail_data.get("item")
            or detail_data.get("data")
            or detail_data
        )
        if not isinstance(product, dict):
            return Response(
                {"detail": "Invalid Zoho product payload."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        image_url = _extract_image_url(product)
        store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
        store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
        image_url = build_image_url(store_domain, image_url) or image_url
        if not image_url:
            image_url = build_zoho_cdn_product_document_url(store_domain, product)
        if not image_url:
            return Response(
                {"detail": "No image URL available for this product."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return HttpResponseRedirect(image_url)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class MultiAccountZohoProductImageProxyAPIView(APIView):
    """
    Redirects product image by account/org/product identifiers.
    """

    def get(self, request, account_id, organization_id, product_id):
        return _product_image_proxy_redirect(
            request,
            account_id=account_id,
            organization_id=organization_id,
            product_id=product_id,
        )