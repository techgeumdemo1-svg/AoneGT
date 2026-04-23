from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.db import IntegrityError

User = get_user_model()

@api_view(['POST'])
def create_superuser(request):
    try:
        # 🔐 Secret check
        secret = request.headers.get("X-ADMIN-SECRET")

        if not secret or secret != settings.SUPERUSER_API_SECRET:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        # 📥 Get data
        email = request.data.get("email")
        password = request.data.get("password")

        # ⚠️ Validate
        if not email or not password:
            return Response({"error": "Email and password required"}, status=400)

        if User.objects.filter(email=email).exists():
            return Response({"error": "User already exists"}, status=400)

        # 🚀 Create superuser
        user = User.objects.create_superuser(
            email=email,
            password=password
        )

        return Response({
            "message": "Superuser created successfully"
        }, status=201)

    except IntegrityError as e:
        return Response({
            "error": "IntegrityError",
            "details": str(e)
        }, status=400)

    except Exception as e:
        return Response({
            "error": "Internal Server Error",
            "details": str(e)
        }, status=500)