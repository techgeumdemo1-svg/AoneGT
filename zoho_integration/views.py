from django.http import JsonResponse, HttpResponseRedirect
from django.conf import settings
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import quote, urlencode
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import ZohoCommerceAccount
from .services import ZohoCommerceService
from .commerce_collections import (
    collection_summary,
    list_zoho_commerce_collections,
    resolve_collection_id_by_name,
)
from .storefront_collections import (
    extract_storefront_collection_name,
    extract_storefront_collection_products,
    fetch_storefront_collection_json,
)
from shop.services.zoho_commerce import ZohoCommerceError
from catalog.models import Product, Store
from catalog.text_utils import html_to_plain_text


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


def _zoho_cdn_static_storefront_file_url(store_domain: str, file_name: str, meta: dict) -> str:
    """Zoho CDN root asset, e.g. ``…/Rice.png?v=…&storefront_domain=www.aonegt.com``."""
    domain = (store_domain or "").strip().replace("https://", "").replace("http://", "")
    fn = (file_name or "").strip()
    if not domain or not fn:
        return ""
    v_raw = (
        meta.get("version")
        or meta.get("image_version")
        or meta.get("revision")
        or meta.get("v")
        or meta.get("cache_key")
    )
    params = [("storefront_domain", domain)]
    if v_raw not in (None, "", 0):
        params.insert(0, ("v", str(v_raw).strip()))
    qs = urlencode(params)
    return f"https://cdn1.zohoecommerce.com/{quote(fn, safe='')}?{qs}"


def build_zoho_cdn_document_url(store_domain: str, payload: dict) -> str:
    """
    Zoho Commerce CDN category / document image.

    Prefer storefront static files (``cdn1.zohoecommerce.com/Name.png?v=…&storefront_domain=…``)
    when ``file_name`` (or similar) is present. Otherwise use ``category-images/{document_id}/…``.
    """
    domain = (store_domain or "").strip().replace("https://", "").replace("http://", "")
    if not domain or not isinstance(payload, dict):
        return ""

    _IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".ico", ".avif")

    def _looks_like_image_file(name: str) -> bool:
        n = (name or "").strip().lower()
        return any(n.endswith(ext) for ext in _IMAGE_EXT)

    def _file_from(p: dict) -> str:
        return str(
            p.get("file_name")
            or p.get("image_name")
            or p.get("filename")
            or p.get("image_file_name")
            or ""
        ).strip()

    rows = payload.get("documents") or payload.get("attachments") or []
    if not isinstance(rows, list):
        rows = []

    fn = _file_from(payload)
    if fn and _looks_like_image_file(fn):
        return _zoho_cdn_static_storefront_file_url(store_domain, fn, payload)

    for row in rows:
        if not isinstance(row, dict):
            continue
        fn = _file_from(row) or str(row.get("name") or "").strip()
        if fn and _looks_like_image_file(fn):
            return _zoho_cdn_static_storefront_file_url(store_domain, fn, row)

    top_document_id = str(payload.get("document_id") or "").strip()
    if top_document_id:
        return (
            f"https://cdn1.zohoecommerce.com/category-images/{quote(top_document_id)}/800x800"
            f"?storefront_domain={domain}"
        )
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_document_id = str(row.get("document_id") or "").strip()
        if row_document_id:
            return (
                f"https://cdn1.zohoecommerce.com/category-images/{quote(row_document_id)}/800x800"
                f"?storefront_domain={domain}"
            )
        fn = str(row.get("file_name") or row.get("name") or "").strip()
        if fn:
            return _zoho_cdn_static_storefront_file_url(store_domain, fn, row)
    file_name = str(payload.get("file_name") or "").strip()
    if file_name:
        return _zoho_cdn_static_storefront_file_url(store_domain, file_name, payload)
    return ""


def build_zoho_cdn_product_document_url(store_domain: str, payload: dict) -> str:
    """
    Zoho Commerce CDN product image URL.

    Prefer storefront shape (matches Zoho site / AoneGT):
    ``product-images/{file_name}/{document_id}/800x800?storefront_domain=...``
    Fall back to ``product-images/{document_id}/800x800?...`` when filename is missing.
    """
    domain = (store_domain or "").strip().replace("https://", "").replace("http://", "")
    if not domain or not isinstance(payload, dict):
        return ""

    def _file_and_doc(p: dict) -> tuple[str, str]:
        fn = str(
            p.get("file_name")
            or p.get("image_name")
            or p.get("original_file_name")
            or p.get("filename")
            or ""
        ).strip()
        did = str(
            p.get("document_id")
            or p.get("image_document_id")
            or p.get("image_id")
            or ""
        ).strip()
        return fn, did

    rows = payload.get("documents") or payload.get("attachments") or payload.get("images") or []
    if not isinstance(rows, list):
        rows = []

    blocks: list[dict] = [payload]
    blocks.extend(r for r in rows if isinstance(r, dict))

    for block in blocks:
        fn, did = _file_and_doc(block)
        if fn and did:
            return (
                f"https://cdn1.zohoecommerce.com/product-images/{quote(fn, safe='')}/{did}/800x800"
                f"?storefront_domain={domain}"
            )

    for block in blocks:
        did = str(
            block.get("document_id")
            or block.get("image_document_id")
            or block.get("image_id")
            or block.get("id")
            or ""
        ).strip()
        if did:
            return (
                f"https://cdn1.zohoecommerce.com/product-images/{quote(did, safe='')}/800x800"
                f"?storefront_domain={domain}"
            )
    return ""


def _explicit_product_image_url(store_domain: str, product_row: dict) -> str:
    """
    Image URL as returned by Zoho (absolute https). Avoids synthesizing
    ``product-images/{document_id}/…`` which can return HTTP 400 when ``document_id``
    is not a valid public storefront image document for that CDN path.
    """
    p_image = _extract_image_url(product_row)
    p_image = build_image_url(store_domain, p_image) or p_image
    if p_image.startswith(("http://", "https://")):
        return p_image
    for list_key in ("documents", "attachments", "images", "product_images"):
        rows = product_row.get(list_key) or []
        if not isinstance(rows, list):
            continue
        for doc in rows:
            if not isinstance(doc, dict):
                continue
            for key in ("image_url", "url", "secure_url", "download_url", "src", "thumbnail_url", "file_url"):
                val = doc.get(key)
                if isinstance(val, str):
                    candidate = val.strip().replace("&amp;", "&")
                    if candidate.startswith(("http://", "https://")):
                        return candidate
    return ""


def _best_product_fallback_image_url(
    service: ZohoCommerceService,
    organization_id: str,
    store_domain: str,
    product_rows: list,
) -> str:
    """
    For category tiles: prefer real URLs from product list/detail; synthetic CDN URL last.
    """
    rows = [r for r in product_rows if isinstance(r, dict)]
    for pr in rows:
        u = _explicit_product_image_url(store_domain, pr)
        if u:
            return u
    # List payloads often omit full URLs; one product detail keeps category list responsive.
    for pr in rows[:1]:
        pid = str(pr.get("product_id") or pr.get("item_id") or pr.get("id") or "").strip()
        if not pid:
            continue
        try:
            detail = service.get_product_detail(str(organization_id), pid)
        except Exception:
            continue
        prod = detail.get("product") if isinstance(detail.get("product"), dict) else None
        if prod is None and isinstance(detail.get("data"), dict):
            prod = detail.get("data")
        if prod is None and isinstance(detail, dict):
            prod = detail
        if isinstance(prod, dict):
            u = _explicit_product_image_url(store_domain, prod)
            if u:
                return u
    for pr in rows:
        u = build_zoho_cdn_product_document_url(store_domain, pr)
        if u:
            return u
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
    for html_key in (
        "description_html",
        "description",
        "category_content",
        "content",
        "long_description",
        "category_description",
        "category_description_html",
        "html_description",
        "summary",
        "information",
        "seo_description",
        "meta_description",
    ):
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
    for key in (
        "description",
        "product_description",
        "product_short_description",
        "short_description",
        "long_description",
        "description_html",
        "purchase_description",
        "seo_description",
    ):
        clean = html_to_plain_text(payload.get(key))
        if clean:
            return clean

    variants = payload.get("variants") or payload.get("variant_list") or []
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict):
                clean = _extract_description(variant)
                if clean:
                    return clean
    return ""


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


def _zoho_product_rows_for_org(
    service: ZohoCommerceService,
    organization_id: str,
    *,
    category_id: Optional[str],
    include_descendants: bool,
    max_pages: int = 100,
) -> list[dict]:
    """Load raw product dicts from Zoho (same rules as GET /zoho/multi/products/)."""
    cat = (category_id or "").strip() or None

    if cat and include_descendants:
        category_data = service.list_categories(organization_id=organization_id)
        category_rows = category_data.get("categories", []) or category_data.get("category", [])
        category_rows = [c for c in category_rows if isinstance(c, dict)]
        category_ids = _collect_category_and_descendants(category_rows, cat)

        products: list[dict] = []
        seen_product_ids: set[str] = set()
        for current_category_id in category_ids:
            rows = service.list_products_all_pages(
                organization_id,
                category_id=current_category_id,
                per_page=200,
                max_pages=max_pages,
            )
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
        return products

    return service.list_products_all_pages(
        organization_id,
        category_id=cat,
        per_page=200,
        max_pages=max_pages,
    )


def _enrich_zoho_list_product_rows_from_detail(
    service: ZohoCommerceService,
    organization_id: str,
    products: list[dict],
) -> None:
    """Fill missing rate/sku/image on list rows using product detail (in place)."""
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


def _multi_account_product_list_response(request, account, organization_id: str):
    service = ZohoCommerceService(account)
    category_id = (request.GET.get("category_id") or "").strip() or None
    include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)

    products = _zoho_product_rows_for_org(
        service,
        organization_id,
        category_id=category_id,
        include_descendants=include_descendants,
    )

    exclude_pid = (
        (request.GET.get("exclude_product_id") or request.GET.get("exclude_zoho_product_id") or "")
        .strip()
    )
    if exclude_pid:
        products = [
            p
            for p in products
            if str(p.get("product_id") or p.get("item_id") or p.get("id") or "").strip() != exclude_pid
        ]

    limit_raw = (request.GET.get("limit") or "").strip()
    limit_applied = None
    if limit_raw:
        try:
            lim = int(limit_raw)
        except ValueError:
            lim = 0
        if lim > 0:
            limit_applied = min(lim, 200)
            products = products[:limit_applied]

    _enrich_zoho_list_product_rows_from_detail(service, organization_id, products)

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

    payload = {
        "status": "success",
        "account_name": account.name,
        "account_email": account.email,
        "organization_id": organization_id,
        "category_id": category_id,
        "include_descendants": include_descendants,
        "count": len(product_summaries),
        "products": product_summaries,
    }
    if exclude_pid:
        payload["exclude_product_id"] = exclude_pid
    if limit_applied is not None:
        payload["limit"] = limit_applied
    return Response(payload)


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


def _product_row_search_haystack(row: dict) -> str:
    """
    Lowercased text blob for substring search against Zoho list-product rows.
    Includes variant-level names/SKUs (parent name is often generic when has_variant is true).
    """
    parts: list[str] = []

    def _add(value) -> None:
        s = str(value or "").strip()
        if s:
            parts.append(s)

    _add(row.get("name") or row.get("product_name") or row.get("item_name"))
    _add(row.get("sku") or row.get("product_sku"))
    _add(row.get("product_id") or row.get("item_id") or row.get("id"))
    _add(row.get("url"))
    _add(row.get("seo_keyword"))
    _add(row.get("brand"))
    _add(row.get("manufacturer"))
    _add(row.get("part_number"))
    _add(row.get("category_name"))
    cat = row.get("category")
    if isinstance(cat, dict):
        _add(cat.get("category_name") or cat.get("name"))
    elif isinstance(cat, str):
        _add(cat)

    for key in ("description", "product_description", "product_short_description"):
        raw = row.get(key)
        if raw:
            _add(str(raw)[:500])

    tags = row.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, dict):
                _add(t.get("tag_name") or t.get("name"))
            elif isinstance(t, str):
                _add(t)

    variants = row.get("variants")
    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, dict):
                continue
            _add(v.get("name") or v.get("product_name"))
            _add(v.get("sku") or v.get("product_sku"))
            _add(v.get("variant_id") or v.get("id"))
            _add(v.get("ean") or v.get("upc") or v.get("isbn"))

    return " ".join(parts).lower()


class MultiAccountZohoProductSearchAPIView(APIView):
    """
    Same style as GET /zoho/multi/categories/search/ — text match on the product pool.

    Query params:
      - account_id (required)
      - organization_id (required)
      - query (required, alias: q): case-insensitive substring on names, SKUs, ids, url, seo,
        descriptions, tags, and variant rows
      - limit (optional, default=20, max=100)
      - category_id (optional): limit search to this Zoho category (same as /zoho/multi/products/)
      - include_descendants (optional, default true): with category_id, include child categories
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        query = (request.GET.get("query") or request.GET.get("q") or "").strip()
        limit_raw = (request.GET.get("limit") or "").strip()
        category_id = (request.GET.get("category_id") or "").strip() or None
        include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)

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
                {"status": "error", "message": "query (or q) query parameter is required"},
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
            products = _zoho_product_rows_for_org(
                service,
                organization_id,
                category_id=category_id,
                include_descendants=include_descendants,
            )
            needle = query.lower()

            matched_rows: list[dict] = []
            for row in products:
                haystack = _product_row_search_haystack(row)
                if needle in haystack:
                    matched_rows.append(row)

            matched_rows = matched_rows[:limit]
            _enrich_zoho_list_product_rows_from_detail(service, organization_id, matched_rows)

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

            return Response(
                {
                    "status": "success",
                    "account_id": account.id,
                    "account_name": account.name,
                    "account_email": account.email,
                    "organization_id": organization_id,
                    "category_id": category_id,
                    "include_descendants": include_descendants,
                    "query": query,
                    "scanned": len(products),
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


def _zoho_detail_product_dict(detail_data: dict) -> dict:
    product = (
        detail_data.get("product")
        or detail_data.get("item")
        or detail_data.get("data")
        or detail_data
    )
    return product if isinstance(product, dict) else {}


def _normalize_best_deals_product_images(
    request,
    store: Store,
    store_domain: str,
    product_summaries: list[dict],
) -> None:
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


def _best_deal_summary_from_local_zoho(local: Product, zoho_row: dict, store_domain: str) -> dict:
    summary = _product_summary(zoho_row, store_domain=store_domain)
    zpid = (local.zoho_product_id or "").strip()
    if not summary.get("product_id"):
        summary["product_id"] = zpid
    if not (summary.get("product_name") or "").strip():
        summary["product_name"] = local.name
    price = str(summary.get("price") or "").strip()
    if price in ("", "0", "0.00"):
        summary["price"] = str(local.price)
    if not (summary.get("sku") or "").strip():
        summary["sku"] = local.sku or ""
    if not (summary.get("image_url") or "").strip() and (local.image_url or "").strip():
        summary["image_url"] = local.image_url
    summary["catalog_product_id"] = local.pk
    summary["is_best_deal"] = True
    summary["best_deal_sort_order"] = local.best_deal_sort_order
    summary["currency"] = (local.currency or "AED").strip() or "AED"
    return summary


def _resolve_best_deals_category_id(
    service: ZohoCommerceService,
    organization_id: str,
    *,
    category_id: str,
    category_name: str,
) -> tuple[str, str]:
    cid = (category_id or "").strip()
    if cid:
        resolved_name = ""
        try:
            category_data = service.list_categories(organization_id=organization_id)
            category_rows = category_data.get("categories", []) or category_data.get("category", [])
            for row in category_rows:
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("category_id") or row.get("id") or "").strip()
                if row_id == cid:
                    resolved_name = _category_name(row)
                    break
        except Exception:
            pass
        return cid, resolved_name

    want_name = (category_name or "").strip()
    if not want_name:
        return "", ""

    category_data = service.list_categories(organization_id=organization_id)
    category_rows = category_data.get("categories", []) or category_data.get("category", [])
    category_rows = [c for c in category_rows if isinstance(c, dict)]
    want_lower = want_name.lower()
    exact: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []
    for row in category_rows:
        row_id = str(row.get("category_id") or row.get("id") or "").strip()
        if not row_id:
            continue
        name = _category_name(row)
        name_lower = name.lower()
        if name_lower == want_lower:
            exact.append((row_id, name))
        elif want_lower in name_lower:
            partial.append((row_id, name))
    if exact:
        return exact[0]
    if partial:
        return partial[0]
    return "", ""


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
        collection_name_query = "Best Deals"
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
                        "status": "success",
                        "message": "No Best Deals collection found for this store.",
                        "count": 0,
                        "products": [],
                    },
                    status=200,
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
                        "status": "success",
                        "message": "No Best Deals collection found for this store.",
                        "count": 0,
                        "products": [],
                    },
                    status=200,
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


# Merged from Zoho category detail (and descendants) so _extract_image_url can read
# ``<img src="https://cdn1.zohoecommerce.com/...">`` from description HTML.
_CATEGORY_DETAIL_IMAGE_MERGE_KEYS = (
    "image_url",
    "image",
    "image_name",
    "image_path",
    "document_id",
    "documents",
    "description_html",
    "description",
    "category_content",
    "content",
    "long_description",
    "category_description",
    "category_description_html",
    "html_description",
    "summary",
    "information",
    "seo_description",
    "meta_description",
)


def _multi_account_category_list_response(
    request,
    account,
    organization_id: str,
    *,
    skip_product_image_fallback: bool = False,
):
    service = ZohoCommerceService(account)
    data = service.list_categories(organization_id=organization_id)
    categories = data.get("categories", []) or data.get("category", [])
    categories = [c for c in categories if isinstance(c, dict)]
    placeholder_url = str(getattr(settings, "ZOHO_IMAGE_PLACEHOLDER_URL", "") or "").strip()

    # Optional query params:
    # - category_id: return categories under this parent/root category
    # - include_descendants: whether to include all descendants (default true)
    # - strict_category_images: if true, do not use product thumbnails as category image fallback
    query_category_id = (request.GET.get("category_id") or "").strip() or None
    include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)
    strict_category_images = _as_bool(request.GET.get("strict_category_images"), default=False)
    skip_product_fb = bool(skip_product_image_fallback) or strict_category_images

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
    ordered_pairs: list[tuple[str, dict]] = []
    for c in main_categories:
        if not _category_name(c):
            continue
        cid = str(c.get("category_id") or c.get("id") or "").strip()
        if not cid:
            continue
        ordered_pairs.append((cid, dict(c)))

    # One Zoho detail call per category without a list-time image — sequential calls
    # exceed gateway timeouts (e.g. Render). Fetch details in parallel (bounded).
    pending_ids = [cid for cid, row in ordered_pairs if not _extract_image_url(row)]
    detail_cap = int(getattr(settings, "ZOHO_MAX_CATEGORY_DETAIL_FETCH", 24) or 24)
    raw_lim = (request.GET.get("category_detail_limit") or "").strip()
    if raw_lim:
        try:
            detail_cap = max(0, min(int(raw_lim), 80))
        except ValueError:
            pass
    if detail_cap <= 0:
        pending_ids = []
    elif len(pending_ids) > detail_cap:
        pending_ids = pending_ids[:detail_cap]
    scheduled_category_detail_fetches = len(pending_ids)
    detail_by_id: dict[str, dict] = {}
    if pending_ids:
        org_s = str(organization_id)

        def _detail_for(cat_id: str) -> tuple[str, dict]:
            try:
                detail = service.get_category_detail(
                    organization_id=org_s,
                    category_id=cat_id,
                )
                dr = detail.get("category") or detail.get("data") or {}
                return cat_id, (dr if isinstance(dr, dict) else {})
            except Exception:
                return cat_id, {}

        workers = min(8, max(1, len(pending_ids)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            detail_by_id = dict(pool.map(_detail_for, pending_ids))

    for cid, row in ordered_pairs:
        row = dict(row)
        dr = detail_by_id.get(cid)
        if dr:
            for key in _CATEGORY_DETAIL_IMAGE_MERGE_KEYS:
                if key in dr and dr.get(key) not in (None, "", []):
                    row[key] = dr[key]
        if not _extract_image_url(row) and not build_zoho_cdn_document_url(store_domain, row):
            descendant = _first_descendant_with_image(cid)
            if isinstance(descendant, dict):
                row = dict(row)
                for key in _CATEGORY_DETAIL_IMAGE_MERGE_KEYS:
                    if descendant.get(key) not in (None, "", []):
                        row[key] = descendant.get(key)
        if (
            not skip_product_fb
            and not _extract_image_url(row)
            and not build_zoho_cdn_document_url(store_domain, row)
        ):
            # Final fallback: use first product image inside this category,
            # so image_url remains a real Zoho CDN URL (cdn1.zohoecommerce.com/product-images/…).
            # Omit with ?strict_category_images=1 or the legacy strict-only grocery view flag.
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
                    found_product_image = _best_product_fallback_image_url(
                        service,
                        str(organization_id),
                        store_domain,
                        product_rows,
                    )
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
        "strict_category_images": strict_category_images,
        "category_detail_fetches": scheduled_category_detail_fetches,
        "category_detail_cap": detail_cap,
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


class MultiAccountZohoCollectionListQueryAPIView(APIView):
    """
    GET /zoho/multi/collections/?account_id=&organization_id=
    Lists Zoho Commerce collections via admin API (zohoapis.com/commerce/v1/collections).

    Optional: collection_name=Best Deals — returns only that match in collections[] (case-insensitive).
    Optional: all=true — return every collection plus matched when collection_name is set.
    Also accepts store_id (resolves organization_id / account_id when possible).
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        store_id_raw = (request.GET.get("store_id") or "").strip()
        collection_name = "Best Deals"
        list_all = _as_bool(request.GET.get("all"), default=False)

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
                {"status": "error", "message": "account_id query parameter is required"},
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
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response(
                {"status": "error", "message": "Zoho account not found"},
                status=404,
            )

        store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
        try:
            rows = list_zoho_commerce_collections(organization_id, store=store)
        except ZohoCommerceError as exc:
            return Response(
                {"status": "error", "message": str(exc)},
                status=400,
            )

        all_summaries = [collection_summary(r) for r in rows]
        matched_id = ""
        matched_name = ""
        matched_summary = None
        if collection_name:
            matched_id, matched_name = resolve_collection_id_by_name(rows, collection_name)
            if matched_id:
                matched_summary = {
                    "collection_id": matched_id,
                    "name": matched_name,
                    "url": "",
                    "status": "",
                }
                for s in all_summaries:
                    if s.get("collection_id") == matched_id:
                        matched_summary = s
                        break

        if collection_name and not list_all:
            summaries = [matched_summary] if matched_summary else []
        else:
            summaries = all_summaries

        payload = {
            "status": "success",
            "account_id": account.id,
            "account_name": account.name,
            "account_email": account.email,
            "organization_id": organization_id,
            "store_id": store.pk if store else None,
            "count": len(summaries),
            "collections": summaries,
        }
        if collection_name:
            payload["collection_name_query"] = collection_name
            payload["matched"] = (
                {"collection_id": matched_id, "name": matched_name}
                if matched_id
                else None
            )
            if list_all:
                payload["total_collections"] = len(all_summaries)
        return Response(payload, status=200)


class MultiAccountZohoCategoryListQueryAPIView(APIView):
    """GET …/zoho/multi/categories/?account_id=&organization_id= — Zoho category menu + image enrichment."""

    skip_product_image_fallback = False

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
            return _multi_account_category_list_response(
                request,
                account,
                organization_id,
                skip_product_image_fallback=self.skip_product_image_fallback,
            )
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


class MultiAccountZohoCategoryListAonegtGroceryQueryAPIView(MultiAccountZohoCategoryListQueryAPIView):
    """
    Same behavior as ``/zoho/multi/categories/`` (including Zoho CDN ``product-images`` fallback
    when Zoho does not assign category art). Kept as a stable path for AoneGT Grocery clients.

    Append ``&strict_category_images=1`` to skip product thumbnails and use only category
    metadata / descendant category images / placeholder.
    """


class MultiAccountZohoSubCategoryListQueryAPIView(APIView):
    """
    Query params:
      - account_id (required)
      - organization_id (required)
      - category_id (required): parent category id
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        parent_category_id = (request.GET.get("category_id") or "").strip()

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
        if not parent_category_id:
            return Response(
                {"status": "error", "message": "category_id query parameter is required"},
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
            data = service.list_categories(organization_id=organization_id)
            categories = data.get("categories", []) or data.get("category", [])
            categories = [c for c in categories if isinstance(c, dict)]

            parent_category = None
            children = []
            for category in categories:
                category_id = str(category.get("category_id") or category.get("id") or "").strip()
                if category_id == parent_category_id:
                    parent_category = category
                parent_id = str(
                    category.get("parent_category_id")
                    or category.get("parent_id")
                    or ""
                ).strip()
                if parent_id == parent_category_id and category.get("visibility") is not False:
                    children.append(category)

            store = Store.objects.filter(zoho_org_id=str(organization_id)).first()
            store_domain = (getattr(store, "zoho_store_domain", "") or "").strip() if store else ""
            subcategories = []
            for child in sorted(
                children,
                key=lambda x: (x.get("sibling_order", 0), _category_name(x).lower()),
            ):
                child_id = str(child.get("category_id") or child.get("id") or "").strip()
                fallback_image_url = (
                    request.build_absolute_uri(
                        _category_image_proxy_relative_path(
                            account.id, str(organization_id), child_id,
                        ),
                    ) if child_id else ""
                )
                summary = _category_summary(
                    child,
                    fallback_image_url=fallback_image_url,
                    store_domain=store_domain,
                )
                if not (summary.get("image_url") or "").strip() and fallback_image_url:
                    summary["image_url"] = fallback_image_url
                subcategories.append(summary)

            return Response(
                {
                    "status": "success",
                    "account_id": account.id,
                    "account_name": account.name,
                    "account_email": account.email,
                    "organization_id": organization_id,
                    "parent_category": (
                        _category_summary(parent_category, store_domain=store_domain)
                        if isinstance(parent_category, dict)
                        else None
                    ),
                    "category_id": parent_category_id,
                    "count": len(subcategories),
                    "subcategories": subcategories,
                },
                status=200,
            )
        except Exception as e:
            return Response(
                {"status": "error", "message": str(e)},
                status=400,
            )


class MultiAccountZohoCategorySearchAPIView(APIView):
    """
    Query params:
      - account_id (required)
      - organization_id (required)
      - query (required, alias: q): case-insensitive search text
      - limit (optional, default=20, max=100)
    """

    def get(self, request):
        account_id_raw = (request.GET.get("account_id") or "").strip()
        organization_id = (request.GET.get("organization_id") or "").strip()
        query = (request.GET.get("query") or request.GET.get("q") or "").strip()
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
                {"status": "error", "message": "query (or q) query parameter is required"},
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
                    if not (summary.get("image_url") or "").strip() and fallback_image_url:
                        # Use proxy only when no direct/derived image URL is available.
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
                    "query": query,
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