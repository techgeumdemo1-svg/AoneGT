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


def fetch_storefront_collection_json(
    commerce_base_url: str,
    domain_name: str,
    collection_id: str,
    *,
    timeout: int = 25,
) -> dict:
    """
    GET /storefront/api/v1/collections/{id} with domain-name header (no OAuth).
    """
    cid = (collection_id or "").strip()
    host = (domain_name or "").strip().replace("https://", "").replace("http://", "").split("/")[0].lower()
    if not cid or not host:
        return {}
    origin = _storefront_origin(commerce_base_url)
    url = f"{origin}/storefront/api/v1/collections/{quote(cid, safe='')}"
    try:
        response = requests.get(
            url,
            headers={
                "domain-name": host,
                "Accept": "application/json",
            },
            params={"format": "json"},
            timeout=timeout,
        )
        if not response.ok:
            return {}
        if not (response.content or b"").strip():
            return {}
        return response.json()
    except (requests.RequestException, ValueError) as e:
        logger.debug("storefront collection fetch failed %s: %s", cid, e)
        return {}


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
