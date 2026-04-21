from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    SuperuserLoginSerializer,
    OrganizationSerializer,
    CouponCreateSerializer,
    CouponDeleteSerializer,
)
from .services import authenticate_superuser, ZohoWebhookService


# ── Existing (do not remove) ──────────────────────────────────────────────────

class SuperuserLoginView(APIView):
    """
    API View to handle Superuser login.
    Allows any user to attempt login, but the service layer strictly enforces superuser-only access.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SuperuserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        login_data = authenticate_superuser(serializer.validated_data)

        return Response(
            {
                'message': 'Superuser authenticated successfully.',
                'data': login_data
            },
            status=status.HTTP_200_OK
        )


# ── Organizations ─────────────────────────────────────────────────────────────

class OrganizationListView(APIView):
    """
    GET /api/offers/organizations/
    Returns all active organizations with name, image, org_id.
    Frontend uses this to show the org selection grid.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        service = ZohoWebhookService()
        orgs = service.get_organizations()
        serializer = OrganizationSerializer(
            orgs, many=True, context={'request': request}
        )
        return Response(
            {'message': 'Organizations fetched successfully.', 'data': serializer.data},
            status=status.HTTP_200_OK
        )


# ── Coupons ───────────────────────────────────────────────────────────────────

class ListCouponsView(APIView):
    """
    GET /api/offers/organizations/<org_id>/coupons/
    Calls the list_coupons webhook for the selected org.
    Returns the coupon list from Zoho Commerce.
    """
    permission_classes = [IsAdminUser]

    def get(self, request, org_id):
        service = ZohoWebhookService()
        try:
            result = service.list_coupons(org_id)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {'message': 'Coupons fetched successfully.', 'data': result},
            status=status.HTTP_200_OK
        )


class CreateCouponView(APIView):
    """
    POST /api/offers/organizations/<org_id>/coupons/create/
    Validates input, then calls the create_coupon webhook.
    Checks Zoho's inner response code before returning success.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, org_id):
        serializer = CouponCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = ZohoWebhookService()
        try:
            result = service.create_coupon(org_id, serializer.validated_data)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # The webhook wraps Zoho's response. Check the inner code.
        zoho_response = result.get('response', {}).get('zoho_response', {})
        if zoho_response.get('code') != 0:
            return Response(
                {
                    'error': 'Zoho Commerce rejected the coupon.',
                    'zoho_message': zoho_response.get('message'),
                    'zoho_code': zoho_response.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {'message': 'Coupon created successfully.', 'data': result},
            status=status.HTTP_201_CREATED
        )


class DeleteCouponView(APIView):
    """
    DELETE /api/offers/organizations/<org_id>/coupons/delete/
    Calls the delete_coupon webhook with the given coupon_id.
    Body: {"coupon_id": "3743983000000049109"}
    """
    permission_classes = [IsAdminUser]

    def delete(self, request, org_id):
        serializer = CouponDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = ZohoWebhookService()
        try:
            result = service.delete_coupon(
                org_id, serializer.validated_data['coupon_id']
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {'message': 'Coupon deleted successfully.', 'data': result},
            status=status.HTTP_200_OK
        )