from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import SuperuserLoginSerializer
from .services import authenticate_superuser


class SuperuserLoginView(APIView):
    """
    API View to handle Superuser login.
    Allows any user to attempt login, but the service layer strictly enforces superuser-only access.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SuperuserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Pass validated data to the service layer
        login_data = authenticate_superuser(serializer.validated_data)

        return Response(
            {
                'message': 'Superuser authenticated successfully.',
                'data': login_data
            },
            status=status.HTTP_200_OK
        )