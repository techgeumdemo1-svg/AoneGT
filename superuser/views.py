from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

User = get_user_model()


def _admin_secret_valid(request) -> bool:
    header_secret = (request.headers.get("X-ADMIN-SECRET") or "").strip()
    expected = (getattr(settings, "SUPERUSER_API_SECRET", "") or "").strip()
    return bool(expected and header_secret and header_secret == expected)


@api_view(["POST"])
def create_superuser(request):
    try:
        if not _admin_secret_valid(request):
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response({"error": "Email and password required"}, status=400)

        if User.objects.filter(email=email).exists():
            return Response({"error": "User already exists"}, status=400)

        User.objects.create_superuser(
            email=email,
            password=password,
        )
        return Response({"message": "Superuser created successfully"}, status=201)

    except IntegrityError as exc:
        return Response(
            {
                "error": "IntegrityError",
                "details": str(exc),
            },
            status=400,
        )
    except Exception as exc:
        return Response(
            {
                "error": "Internal Server Error",
                "details": str(exc),
            },
            status=500,
        )
