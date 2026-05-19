"""
Optional Zoho Commerce storefront collection helpers.

Store admin product JSON usually has no collection_id. The public Storefront
"Get Collection" API returns collection id + products[] — see:
https://www.zoho.com/commerce/api/get-collection.html

Set ZOHO_COLLECTION_PROBE_IDS (comma-separated collection ids) to probe those
collections when upserting a product; if product_id / variant_id matches, we
persist zoho_collection_id.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urlparse

import requests
from django.conf import settings

from zoho_integration.models import ZohoCommerceAccount

logger = logging.getLogger(__name__)


def _storefront_origin(commerce_base_url: str) -> str:
    base = (commerce_base_url or "").strip().rstrip("/") or "https://commerce.zoho.com"
    if "://" not in base:
        base = f"https://{base}"
    parsed = urlparse(base)
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_storefront_host(domain_name: str) -> str:
    return (domain_name or "").strip().replace("https://", "").replace("http://", "").split("/")[0].lower()


def _storefront_get_json(origin: str, host: str, resource_path: str, *, timeout: int) -> dict:
    """GET {origin}/storefront/api/v1/{resource_path} with domain-name header."""
    path = (resource_path or "").strip().lstrip("/")
    if not path:
        return {}
    url = f"{origin}/storefront/api/v1/{path}"
    try:
        response = requests.get(
            url,
            headers={
                "domain-name": host,
                "Accept": "application/json",
            },
            params={"format": "json"},
            timeout=timeout,
            allow_redirects=True,
        )
        if not response.ok:
            logger.warning(
                "storefront collection request failed: HTTP %s domain=%s url=%s body=%s",
                response.status_code,
                host,
                url,
                (response.text or "")[:300],
            )
            return {}
        if not (response.content or b"").strip():
            return {}
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("storefront collection request failed domain=%s url=%s: %s", host, url, exc)
        return {}


def _payload_root(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    root = payload.get("payload")
    return root if isinstance(root, dict) else {}


def _storefront_redirect_path(payload: dict) -> str:
    redirect = str(_payload_root(payload).get("redirect") or "").strip()
    if not redirect:
        return ""
    return redirect.split("?")[0].strip()


def _collection_payload_has_products(payload: dict) -> bool:
    return bool(extract_storefront_collection_products(payload))


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


def storefront_payload_contains_zoho_product_id(payload: dict, zoho_product_id: str) -> bool:
    want = str(zoho_product_id or "").strip()
    if not want or not isinstance(payload, dict):
        return False
    coll = _storefront_collection_dict(payload)
    prods = coll.get("products")
    if not isinstance(prods, list):
        return False
    for p in prods:
        if not isinstance(p, dict):
            continue
        if str(p.get("product_id") or "").strip() == want:
            return True
        for v in p.get("variants") or []:
            if isinstance(v, dict) and str(v.get("variant_id") or "").strip() == want:
                return True
    return False


def collection_probe_ids_from_settings() -> list[str]:
    raw = str(getattr(settings, "ZOHO_COLLECTION_PROBE_IDS", "") or "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def resolve_zoho_collection_id_via_storefront(store, zoho_product_id: str) -> str:
    """
    Return first matching collection id from ZOHO_COLLECTION_PROBE_IDS, or "".
    """
    pid = str(zoho_product_id or "").strip()
    if not pid:
        return ""
    if not collection_probe_ids_from_settings():
        return ""
    domain = (getattr(store, "zoho_store_domain", "") or "").strip()
    domain = domain.replace("https://", "").replace("http://", "").split("/")[0].lower()
    if not domain:
        return ""
    org = str(getattr(store, "zoho_org_id", "") or "").strip()
    if not org:
        return ""
    account = ZohoCommerceAccount.objects.filter(is_active=True, organization_id=org).first()
    if account is None:
        return ""
    commerce_url = (getattr(account, "commerce_base_url", "") or "").strip() or "https://commerce.zoho.com"

    for cid in collection_probe_ids_from_settings()[:40]:
        data = fetch_storefront_collection_json(commerce_url, domain, cid)
        if storefront_payload_contains_zoho_product_id(data, pid):
            return cid[:120]
    return ""


def backfill_product_collection_id_if_empty(store, product, zoho_product_id: str) -> None:
    """
    When zoho_collection_id is still blank, run storefront probes and persist
    (runs even if _upsert_local_product_from_zoho was skipped for this request).
    """
    if not product:
        return
    if (getattr(product, "zoho_collection_id", "") or "").strip():
        return
    col = resolve_zoho_collection_id_via_storefront(store, zoho_product_id)
    if not col:
        return
    product.zoho_collection_id = col[:120]
    product.save(update_fields=["zoho_collection_id"])
