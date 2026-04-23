# from django.shortcuts import render

# from django.contrib.auth import get_user_model
# from django.conf import settings
# from rest_framework.decorators import api_view
# from rest_framework.response import Response
# from rest_framework import status

# User = get_user_model()

# @api_view(['POST'])
# def create_superuser(request):
#     secret = request.headers.get("X-ADMIN-SECRET")

#     if not secret or secret != settings.SUPERUSER_API_SECRET:
#         return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

#     username = request.data.get("username")
#     email = request.data.get("email")
#     password = request.data.get("password")

#     if not username or not password:
#         return Response({"error": "Username and password required"}, status=400)

#     if User.objects.filter(username=username).exists():
#         return Response({"error": "User already exists"}, status=400)

#     user = User.objects.create_superuser(
#         username=username,
#         email=email,
#         password=password
#     )

#     return Response({
#         "message": "Superuser created successfully"
#     }, status=201)

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
        # 🔐 Check secret
        secret = request.headers.get("X-ADMIN-SECRET")
        
        if settings.SUPERUSER_API_SECRET is None:
            return Response({"error": "Server misconfiguration"}, status=500)

        if not secret or secret != settings.SUPERUSER_API_SECRET:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        # 📥 Get data
        username = request.data.get("username")
        email = request.data.get("email")
        password = request.data.get("password")

        # ⚠️ Validate input
        if not username or not password:
            return Response({"error": "Username and password required"}, status=400)

        if User.objects.filter(username=username).exists():
            return Response({"error": "User already exists"}, status=400)
        

        # 🚀 Create user
        user = User.objects.create_superuser(
            username=username,
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
