from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    SuperuserLoginSerializer,
    OrganizationSerializer,
    CouponCreateSerializer,
    CouponUpdateSerializer, 
    CouponGetSerializer,
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
        
class GetCouponView(APIView):
    """
    GET /api/offers/organizations/<org_id>/coupons/<coupon_id>/
    Fetches full details of a single coupon from Zoho Commerce.
    Call this before showing the edit form so the client can pre-fill all fields.
    """
    permission_classes = [IsAdminUser]

    # def get(self, request, org_id, coupon_id):
    #     service = ZohoWebhookService()
    #     try:  
    #         result = service.get_coupon(org_id, coupon_id)
    #     except ValueError as e:
    #         return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    #     zoho_response = result.get('zoho_response', {})
    #     # if zoho_response.get('code') != 0:
    #     #     return Response(
    #     #         {
    #     #             'error': 'Zoho Commerce could not fetch the coupon.',
    #     #             'zoho_message': zoho_response.get('message'),
    #     #             'zoho_code': zoho_response.get('code'),
    #     #         },
    #     #         status=status.HTTP_400_BAD_REQUEST
    #     #     )

    #     # return Response(
    #     #     {'message': 'Coupon fetched successfully.', 'data': result},
    #     #     status=status.HTTP_200_OK
    #     # )
    #     if result.get('code') != 0:
    #         return Response(
    #             {
    #                 'error': 'Zoho webhook error.',
    #                 'zoho_message': result.get('message'),
    #             },
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     zoho_response = result.get('zoho_response', {})
    #     if zoho_response.get('code') != 0:
    #         return Response(
    #             {
    #                 'error': 'Zoho Commerce could not fetch the coupon.',
    #                 'zoho_message': zoho_response.get('message'),
    #                 'zoho_code': zoho_response.get('code'),
    #             },
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     return Response(
    #         {'message': 'Coupon fetched successfully.', 'data': result},
    #         status=status.HTTP_200_OK
    #     )
    def get(self, request, org_id, coupon_id):
        service = ZohoWebhookService()
        try:
            result = service.get_coupon(org_id, coupon_id)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Accept both webhook envelopes:
        # 1) {"code": 0, "zoho_response": {...}}
        # 2) {"response": {"code": 0, "zoho_response": {...}}}
        envelope = result.get('response', result)

        if envelope.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho webhook error.',
                    'zoho_message': envelope.get('message'),
                    'zoho_code': envelope.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        zoho_response = envelope.get('zoho_response', envelope)

        if not isinstance(zoho_response, dict):
            return Response(
                {
                    'error': 'Invalid Zoho response format.',
                    'zoho_message': 'Expected JSON object for zoho_response.',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if zoho_response.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho Commerce could not fetch the coupon.',
                    'zoho_message': zoho_response.get('message'),
                    'zoho_code': zoho_response.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        coupon = zoho_response.get('coupon', {})

        clean_coupon = {
            "coupon_id": coupon.get("coupon_id"),
            "coupon_code": coupon.get("coupon_code"),
            "coupon_name": coupon.get("coupon_name") or coupon.get("name"),
            "description": coupon.get("description"),
            "discount_type": coupon.get("discount_type"),
            "discount_value": coupon.get("discount_value"),
            "max_discount_amount": coupon.get("max_discount_amount"),
            "status": coupon.get("status"),
            "is_active": coupon.get("is_active"),
            "activation_time": coupon.get("activation_time"),
            "expiry_at": coupon.get("expiry_at"),
            "minimum_order_value": coupon.get("minimum_order_value"),
            "max_redemption_count": coupon.get("max_redemption_count"),
            "max_redemption_count_per_user": coupon.get("max_redemption_count_per_user"),
            "eligible_products": coupon.get("eligible_products", {}),
}

        return Response(
            {'message': 'Coupon fetched successfully.', 'data': clean_coupon},
            status=status.HTTP_200_OK
        )


class UpdateCouponView(APIView):
    """
    PUT /api/offers/organizations/<org_id>/coupons/<coupon_id>/update/
    Validates update fields (all optional), then calls the update_coupon webhook.
    Only fields included in the request body are sent to Zoho.

    Typical edit flow:
      1. GET  /coupons/<coupon_id>/        → pre-fill the form
      2. PUT  /coupons/<coupon_id>/update/ → submit changed fields only

    Body example: {"coupon_name": "New Name", "discount_value": 75}
    """
    permission_classes = [IsAdminUser]

    def put(self, request, org_id, coupon_id):
        serializer = CouponUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = ZohoWebhookService()
        try:
            result = service.update_coupon(
                org_id,
                coupon_id,
                serializer.validated_data
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Accept both webhook envelopes:
        # 1) {"code": 0, "zoho_response": {...}}
        # 2) {"response": {"code": 0, "zoho_response": {...}}}
        envelope = result.get('response', result)

        if envelope.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho webhook error.',
                    'zoho_message': envelope.get('message'),
                    'zoho_code': envelope.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        zoho_response = envelope.get('zoho_response', envelope)

        if not isinstance(zoho_response, dict):
            return Response(
                {
                    'error': 'Invalid Zoho response format.',
                    'zoho_message': 'Expected JSON object for zoho_response.',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if zoho_response.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho Commerce rejected the update.',
                    'zoho_message': zoho_response.get('message'),
                    'zoho_code': zoho_response.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        coupon = zoho_response.get('coupon', {})

        clean_coupon = {
            "coupon_id": coupon.get("coupon_id"),
            "coupon_code": coupon.get("coupon_code"),
            "coupon_name": coupon.get("coupon_name") or coupon.get("name"),
            "description": coupon.get("description"),
            "discount_type": coupon.get("discount_type"),
            "discount_value": coupon.get("discount_value"),
            "status": coupon.get("status"),
            "is_active": coupon.get("is_active"),
            "minimum_order_value": coupon.get("minimum_order_value"),
            "activation_time": coupon.get("activation_time"),
            "expiry_at": coupon.get("expiry_at"),
            "updated_time": coupon.get("updated_time"),
        }
      
        return Response(
            {'message': 'Coupon updated successfully.', 'data': clean_coupon},
            status=status.HTTP_200_OK
        )