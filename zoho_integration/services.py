import requests
import time


class ZohoIntegrationError(Exception):
    pass


_TOKEN_CACHE_TTL_FALLBACK_SECONDS = 50 * 60
_TOKEN_CACHE_SAFETY_SECONDS = 30
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _token_cache_key(account) -> str:
    account_id = getattr(account, "id", None)
    if account_id is not None:
        return f"id:{account_id}"
    email = (getattr(account, "email", "") or "").strip().lower()
    return f"email:{email}" if email else "anonymous"


def get_zoho_access_token(account):
    cache_key = _token_cache_key(account)
    cached = _TOKEN_CACHE.get(cache_key)
    now = time.time()
    if cached:
        cached_token, cached_expires_at = cached
        if cached_token and cached_expires_at > now:
            return cached_token

    accounts_url = (getattr(account, "accounts_url", "") or "https://accounts.zoho.com").rstrip("/")
    url = f"{accounts_url}/oauth/v2/token"
    payload = {
        "refresh_token": getattr(account, "refresh_token", ""),
        "client_id": getattr(account, "client_id", ""),
        "client_secret": getattr(account, "client_secret", ""),
        "grant_type": "refresh_token",
    }

    try:
        response = requests.post(url, data=payload, timeout=30)
    except requests.RequestException as e:
        raise ZohoIntegrationError(f"Zoho token request failed: {e}") from e

    raw_text = (response.text or "").strip()
    try:
        data = response.json()
    except ValueError as e:
        if not response.ok:
            raise ZohoIntegrationError(
                f"Zoho token request failed: HTTP {response.status_code}, body: {raw_text[:300]}"
            ) from e
        raise ZohoIntegrationError("Invalid JSON from Zoho token endpoint.") from e

    if not response.ok:
        raise ZohoIntegrationError(
            f"Zoho token request failed: HTTP {response.status_code}, response: {data}"
        )

    token = data.get("access_token")
    if not token:
        raise ZohoIntegrationError(f"Failed to get access token: {data}")

    expires_in = data.get("expires_in")
    try:
        expires_in_seconds = int(expires_in)
    except (TypeError, ValueError):
        expires_in_seconds = _TOKEN_CACHE_TTL_FALLBACK_SECONDS

    cache_ttl = max(60, expires_in_seconds - _TOKEN_CACHE_SAFETY_SECONDS)
    _TOKEN_CACHE[cache_key] = (token, now + cache_ttl)
    return token


def get_all_zoho_stores(account):
    access_token = get_zoho_access_token(account)

    base_url = (getattr(account, "commerce_base_url", "") or "https://commerce.zoho.com").rstrip("/")
    url = f"{base_url}/zs-site/api/v1/index/sites"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise ZohoIntegrationError(f"Zoho stores request failed: {e}") from e
    except ValueError as e:
        raise ZohoIntegrationError("Invalid JSON from Zoho stores endpoint.") from e


def _get_json_or_raise_error(response, *, label: str):
    """Parse JSON; on HTTP error include Zoho response body in the exception message."""
    if not response.ok:
        try:
            err_data = response.json()
        except ValueError:
            body_preview = (response.text or "")[:500]
            raise ZohoIntegrationError(
                f"Zoho {label} failed: HTTP {response.status_code}, body: {body_preview}"
            ) from None
        raise ZohoIntegrationError(
            f"Zoho {label} failed: HTTP {response.status_code}, response: {err_data}"
        ) from None
    try:
        return response.json()
    except ValueError as e:
        raise ZohoIntegrationError(f"Invalid JSON from Zoho {label} endpoint.") from e


class ZohoCommerceService:
    def __init__(self, account):
        self.account = account
        self.accounts_url = account.accounts_url.rstrip("/")
        self.commerce_base_url = account.commerce_base_url.rstrip("/")

    def get_access_token(self):
        return get_zoho_access_token(self.account)

    def _headers(self):
        token = self.get_access_token()
        return {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        }

    def list_stores(self):
        return get_all_zoho_stores(self.account)

    def list_products(self, organization_id, page=1, per_page=200, category_id=None):
        # Products/variants docs indicate store APIs use items READ scope.
        # Some endpoints need organization_id in query params.
        url = f"{self.commerce_base_url}/store/api/v1/products"
        params = {
            "organization_id": organization_id,
            "page": page,
            "per_page": per_page,
        }
        if category_id:
            params["category_id"] = category_id
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho products request failed: {e}") from e
        return _get_json_or_raise_error(response, label="products request")

    def get_product_detail(self, organization_id, product_id):
        url = f"{self.commerce_base_url}/store/api/v1/products/{product_id}"
        params = {
            "organization_id": organization_id,
        }
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho product detail request failed: {e}") from e
        return _get_json_or_raise_error(response, label="product detail request")

    def list_categories(self, organization_id, page=1, per_page=100):
        # per_page=200 may return 400 on some orgs; 100 is a safe default.
        url = f"{self.commerce_base_url}/store/api/v1/categories"
        params = {
            "organization_id": organization_id,
            "page": page,
            "per_page": per_page,
        }
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho categories request failed: {e}") from e
        return _get_json_or_raise_error(response, label="categories request")

    def get_category_detail(self, organization_id, category_id):
        url = f"{self.commerce_base_url}/store/api/v1/categories/{category_id}"
        params = {
            "organization_id": organization_id,
        }
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho category detail request failed: {e}") from e
        return _get_json_or_raise_error(response, label="category detail request")
