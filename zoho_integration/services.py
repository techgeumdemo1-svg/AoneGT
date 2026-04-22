import requests


class ZohoIntegrationError(Exception):
    pass


def get_zoho_access_token(account):
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
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise ZohoIntegrationError(f"Zoho token request failed: {e}") from e
    except ValueError as e:
        raise ZohoIntegrationError("Invalid JSON from Zoho token endpoint.") from e

    token = data.get("access_token")
    if not token:
        raise ZohoIntegrationError(f"Failed to get access token: {data}")
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

    def list_products(self, organization_id, page=1, per_page=200):
        # Products/variants docs indicate store APIs use items READ scope.
        # Some endpoints need organization_id in query params.
        url = f"{self.commerce_base_url}/store/api/v1/products"
        params = {
            "organization_id": organization_id,
            "page": page,
            "per_page": per_page,
        }
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho products request failed: {e}") from e
        except ValueError as e:
            raise ZohoIntegrationError("Invalid JSON from Zoho products endpoint.") from e
