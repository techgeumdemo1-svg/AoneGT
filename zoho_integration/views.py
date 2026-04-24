from django.http import JsonResponse
from django.conf import settings
import requests
from typing import Optional
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import ZohoCommerceAccount
from .services import ZohoCommerceService


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


def _category_summary(category: dict) -> dict:
    return {
        "category_id": str(category.get("category_id") or category.get("id") or "").strip(),
        "name": _category_name(category),
        "url": category.get("url") or "",
        "sibling_order": category.get("sibling_order", 0),
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


def _product_summary(product: dict) -> dict:
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
        "image_url": (
            product.get("image_url")
            or product.get("image_name")
            or ""
        ),
    }


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


class MultiAccountZohoProductListAPIView(APIView):
    def get(self, request, account_id, organization_id):
        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Zoho account not found"
            }, status=404)

        service = ZohoCommerceService(account)
        category_id = (request.GET.get("category_id") or "").strip() or None
        include_descendants = _as_bool(request.GET.get("include_descendants"), default=True)

        try:
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
                        detail_image = detail_product.get("image_url") or detail_product.get("image_name")
                        if detail_image:
                            product["image_url"] = detail_image

            product_summaries = [_product_summary(p) for p in products]

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
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)


class MultiAccountZohoCategoryListAPIView(APIView):
    def get(self, request, account_id, organization_id):
        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Zoho account not found"
            }, status=404)

        service = ZohoCommerceService(account)

        try:
            data = service.list_categories(organization_id=organization_id)
            categories = data.get("categories", []) or data.get("category", [])
            categories = [c for c in categories if isinstance(c, dict)]
            main_categories = _menu_categories_for_response(categories)
            main_categories = [_category_summary(c) for c in main_categories if _category_name(c)]

            return Response({
                "status": "success",
                "account_name": account.name,
                "account_email": account.email,
                "organization_id": organization_id,
                "count": len(main_categories),
                "categories": main_categories,
            })
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)