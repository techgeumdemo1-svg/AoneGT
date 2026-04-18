import requests
from django.conf import settings


class ZohoCommerceService:
    def __init__(self, account):
        self.account = account
        self.accounts_url = account.accounts_url.rstrip("/")
        self.commerce_base_url = account.commerce_base_url.rstrip("/")

    def get_access_token(self):
        url = f"{self.accounts_url}/oauth/v2/token"
        data = {
            "refresh_token": self.account.refresh_token,
            "client_id": self.account.client_id,
            "client_secret": self.account.client_secret,
            "grant_type": "refresh_token",
        }
        response = requests.post(url, data=data, timeout=30)
        response.raise_for_status()
        payload = response.json()

        access_token = payload.get("access_token")
        if not access_token:
            raise Exception(f"Failed to get access token: {payload}")

        return access_token

    def _headers(self):
        token = self.get_access_token()
        return {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        }

    def list_stores(self):
        # Official docs show:
        # GET https://commerce.zoho.com/zs-site/api/v1/index/sites
        url = f"{self.commerce_base_url}/zs-site/api/v1/index/sites"
        response = requests.get(url, headers=self._headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def list_products(self, organization_id, page=1, per_page=200):
        # Products/variants docs indicate store APIs use items READ scope.
        # Some endpoints need organization_id in query params.
        url = f"{self.commerce_base_url}/store/api/v1/products"
        params = {
            "organization_id": organization_id,
            "page": page,
            "per_page": per_page,
        }
        response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()
