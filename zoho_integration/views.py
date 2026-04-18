from django.http import JsonResponse
from django.conf import settings
import requests
from typing import Optional
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import ZohoCommerceAccount
from .services import ZohoCommerceService


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

        try:
            data = service.list_products(organization_id=organization_id)
            products = data.get("products", []) or data.get("items", [])

            return Response({
                "status": "success",
                "account_name": account.name,
                "account_email": account.email,
                "organization_id": organization_id,
                "count": len(products),
                "products": products,
            })
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)