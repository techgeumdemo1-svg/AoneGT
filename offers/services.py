import requests
import json
from decimal import Decimal
from django.contrib.auth import authenticate
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Organization, WebhookConfig


# ── Existing (do not remove) ──────────────────────────────────────────────────

def authenticate_superuser(validated_data: dict) -> dict:
    """
    Business logic for authenticating a superuser.
    Checks credentials, verifies superuser status, and returns JWT tokens.
    """
    email = validated_data.get('email', '').lower()
    password = validated_data.get('password')

    user = authenticate(username=email, password=password)

    if not user:
        raise AuthenticationFailed('Invalid email or password.')

    if not user.is_active:
        raise AuthenticationFailed('This account is inactive.')

    if not user.is_superuser:
        raise AuthenticationFailed('Access denied: User is not a superuser.')

    refresh = RefreshToken.for_user(user)

    return {
        'user': {
            'id': user.id,
            'email': user.email,
            'is_superuser': user.is_superuser,
        },
        'tokens': {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
    }


# ── Zoho Webhook Service ──────────────────────────────────────────────────────
class _DecimalEncoder(json.JSONEncoder):
    """Converts Decimal values to float for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj) 
    
class ZohoWebhookService:
    """
    All Zoho Commerce operations go through Zoho Incoming Webhooks.

    Auth model:
    - Django stores the full ZAPI key URL (including encapiKey) in WebhookConfig.
    - Django POSTs JSON to that URL. That is the only thing Django does.
    - The encapiKey in the URL authenticates the request to Zoho.
    - Inside Zoho, the Deluge script uses zoho_commerce_connection (OAuth)
      to call the Commerce API. Django never sees or stores OAuth tokens.

    No client_id, client_secret, or access_token is stored anywhere in Django.
    """

    TIMEOUT_SECONDS = 30

    def _get_webhook_url(self, org_id: int, webhook_type: str) -> str:
        """
        Look up the ZAPI webhook URL for a given organization and action.
        org_id here is the Django DB primary key of the Organization record,
        NOT the Zoho org ID string.
        """
        try:
            org = Organization.objects.get(org_id=org_id, is_active=True)
        except Organization.DoesNotExist:
            raise ValueError(f"Organization id={org_id} not found or inactive.")

        try:
            webhook = org.webhooks.get(webhook_type=webhook_type, is_active=True)
        except WebhookConfig.DoesNotExist:
            raise ValueError(
                f"No active '{webhook_type}' webhook for '{org.name}'. "
                f"Add it in Django admin → WebhookConfig."
            )

        return webhook.webhook_url

    # def _post(self, url: str, payload: dict) -> dict:
    #     """
    #     POST a JSON payload to a Zoho ZAPI webhook URL.
    #     The encapiKey query param in the URL handles authentication.
    #     """
    #     try:
    #         response = requests.post(
    #             url,
    #             json=payload,
    #             headers={"Content-Type": "application/json"},
    #             timeout=self.TIMEOUT_SECONDS
    #         )
    #         response.raise_for_status()
    #         return response.json()
    #     except requests.exceptions.Timeout:
    #         raise ValueError("Zoho webhook timed out (30s).")
    #     except requests.exceptions.ConnectionError:
    #         raise ValueError("Cannot reach Zoho webhook. Check network.")
    #     except requests.exceptions.HTTPError as e:
    #         raise ValueError(f"Zoho returned HTTP error: {str(e)}")
    #     except requests.exceptions.RequestException as e:
    #         raise ValueError(f"Zoho webhook request failed: {str(e)}")
    
    def _post(self, url: str, payload: dict) -> dict:
        """
        POST a JSON payload to a Zoho ZAPI webhook URL.
        Uses a custom encoder to handle Decimal values from DRF serializers.
        """
        try:
            response = requests.post(
                url,
                data=json.dumps(payload, cls=_DecimalEncoder),
                headers={"Content-Type": "application/json"},
                timeout=self.TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise ValueError("Zoho webhook timed out (30s).")
        except requests.exceptions.ConnectionError:
            raise ValueError("Cannot reach Zoho webhook. Check network.")
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"Zoho returned HTTP error: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Zoho webhook request failed: {str(e)}")

    def get_organizations(self):
        """Return all active organizations (used by OrganizationListView)."""
        return Organization.objects.filter(is_active=True).prefetch_related('webhooks')

    def list_coupons(self, org_id: int) -> dict:
        """
        POST {} to the list_coupons webhook.
        The webhook runs a GET internally and returns all coupons.
        """
        url = self._get_webhook_url(org_id, 'list_coupons')
        return self._post(url, {})

    def create_coupon(self, org_id: int, coupon_data: dict) -> dict:
        """
        POST validated coupon_data to the create_coupon webhook.
        coupon_data comes from CouponCreateSerializer.validated_data.
        """
        url = self._get_webhook_url(org_id, 'create_coupon')
        return self._post(url, coupon_data)

    def delete_coupon(self, org_id: int, coupon_id: str) -> dict:
        """
        POST {"coupon_id": "..."} to the delete_coupon webhook.
        The webhook runs a DELETE internally against Zoho Commerce.
        """
        url = self._get_webhook_url(org_id, 'delete_coupon')
        return self._post(url, {"coupon_id": coupon_id})