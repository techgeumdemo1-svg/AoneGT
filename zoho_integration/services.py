import requests
import time


class ZohoIntegrationError(Exception):
    pass


_TOKEN_CACHE_TTL_FALLBACK_SECONDS = 50 * 60
_TOKEN_CACHE_SAFETY_SECONDS = 30
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _request_get_with_retries(url, *, headers, params, timeout=30, label="request"):
    """
    Retry transient network failures a few times before raising a clear error.
    """
    last_error = None
    for attempt in range(1, 4):
        try:
            return requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.ConnectionError as e:
            last_error = e
            # Immediate retries for first two attempts; short backoff before final.
            if attempt < 3:
                time.sleep(0.6 * attempt)
                continue
            msg = str(e)
            if "10065" in msg or "unreachable host" in msg.lower():
                raise ZohoIntegrationError(
                    f"Zoho {label} failed: network unreachable to host in URL {url}. "
                    "Check internet/DNS/firewall, or verify the account commerce_base_url."
                ) from e
            raise ZohoIntegrationError(f"Zoho {label} failed: network connection error: {e}") from e
        except requests.Timeout as e:
            last_error = e
            if attempt < 3:
                time.sleep(0.6 * attempt)
                continue
            raise ZohoIntegrationError(
                f"Zoho {label} failed: request timed out after retries."
            ) from e
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho {label} failed: {e}") from e
    if last_error:
        raise ZohoIntegrationError(f"Zoho {label} failed: {last_error}")
    raise ZohoIntegrationError(f"Zoho {label} failed: unknown request error")


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


def clear_zoho_access_token_cache(account):
    cache_key = _token_cache_key(account)
    _TOKEN_CACHE.pop(cache_key, None)


def get_all_zoho_stores(account):
    base_url = (getattr(account, "commerce_base_url", "") or "https://commerce.zoho.com").rstrip("/")
    url = f"{base_url}/zs-site/api/v1/index/sites"

    # Retry once on 401 by clearing token cache and refreshing.
    for attempt in (1, 2):
        access_token = get_zoho_access_token(account)
        headers = {
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
        }
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho stores request failed: {e}") from e

        if response.status_code == 401 and attempt == 1:
            clear_zoho_access_token_cache(account)
            continue

        try:
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
        # Zoho "List All Products" uses page_start_from (not "page").
        url = f"{self.commerce_base_url}/store/api/v1/products"
        params = {
            "organization_id": organization_id,
            # Zoho docs use page_start_from; some deployments also honor "page".
            "page_start_from": page,
            "page": page,
            "per_page": per_page,
        }
        if category_id:
            params["category_id"] = category_id
        response = _request_get_with_retries(
            url,
            headers=self._headers(),
            params=params,
            timeout=30,
            label="products request",
        )
        return _get_json_or_raise_error(response, label="products request")

    def list_products_all_pages(
        self,
        organization_id,
        category_id=None,
        *,
        per_page: int = 200,
        max_pages: int = 100,
    ) -> list[dict]:
        """
        Concatenate /store/api/v1/products across pages until has_more_page is false
        or max_pages is reached.
        """
        allowed = (10, 25, 50, 100, 200)
        if per_page not in allowed:
            per_page = 200
        combined: list[dict] = []
        for page in range(1, max_pages + 1):
            data = self.list_products(
                organization_id,
                page=page,
                per_page=per_page,
                category_id=category_id,
            )
            rows = data.get("products", []) or data.get("items", [])
            for row in rows:
                if isinstance(row, dict):
                    combined.append(row)
            page_ctx = data.get("page_context") if isinstance(data.get("page_context"), dict) else {}
            if not page_ctx.get("has_more_page"):
                break
        return combined

    def get_product_detail(self, organization_id, product_id):
        url = f"{self.commerce_base_url}/store/api/v1/products/{product_id}"
        params = {
            "organization_id": organization_id,
        }
        response = _request_get_with_retries(
            url,
            headers=self._headers(),
            params=params,
            timeout=30,
            label="product detail request",
        )
        return _get_json_or_raise_error(response, label="product detail request")

    def list_categories(self, organization_id, page=1, per_page=100):
        # per_page=200 may return 400 on some orgs; 100 is a safe default.
        url = f"{self.commerce_base_url}/store/api/v1/categories"
        params = {
            "organization_id": organization_id,
            "page": page,
            "per_page": per_page,
        }
        response = _request_get_with_retries(
            url,
            headers=self._headers(),
            params=params,
            timeout=30,
            label="categories request",
        )
        return _get_json_or_raise_error(response, label="categories request")

    def get_category_detail(self, organization_id, category_id):
        url = f"{self.commerce_base_url}/store/api/v1/categories/{category_id}"
        params = {
            "organization_id": organization_id,
        }
        response = _request_get_with_retries(
            url,
            headers=self._headers(),
            params=params,
            timeout=30,
            label="category detail request",
        )
        return _get_json_or_raise_error(response, label="category detail request")
