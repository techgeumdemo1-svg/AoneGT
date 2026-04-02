from django.conf import settings
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import User, PasswordResetOTP
from .serializers import (
    RegisterSerializer,
    EmailCheckSerializer,
    LoginSerializer,
    ForgotPasswordRequestSerializer,
    ResetPasswordSerializer,
    UserProfileSerializer,
)


class RegisterAPIView(APIView):
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                'message': 'Account created successfully.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CheckEmailAPIView(APIView):
    def post(self, request):
        serializer = EmailCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        exists = User.objects.filter(email__iexact=email).exists()
        return Response(
            {
                'email': email,
                'exists': exists,
                'message': 'Email found. Continue to password screen.' if exists else 'Email not registered.'
            },
            status=status.HTTP_200_OK,
        )


class LoginAPIView(APIView):
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ForgotPasswordAPIView(APIView):
    def post(self, request):
        serializer = ForgotPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response(
                {'detail': 'No account found with this email.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        otp = PasswordResetOTP.objects.create(user=user)

        subject = 'AoneGt Password Reset OTP'
        message = (
            f'Hello {user.first_name},\n\n'
            f'Your OTP for password reset is: {otp.otp_code}\n'
            f'This OTP will expire in 10 minutes.\n\n'
            f'Reset URL: {settings.FRONTEND_RESET_URL}\n\n'
            f'If you did not request this, please ignore this email.'
        )
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)

        return Response(
            {
                'message': 'Password reset OTP sent to email.',
                'email': user.email,
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordAPIView(APIView):
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].lower()
        otp_code = serializer.validated_data['otp']
        new_password = serializer.validated_data['new_password']

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response({'detail': 'Invalid email.'}, status=status.HTTP_404_NOT_FOUND)

        otp = PasswordResetOTP.objects.filter(user=user, otp_code=otp_code, is_used=False).first()
        if not otp:
            return Response({'detail': 'Invalid OTP.'}, status=status.HTTP_400_BAD_REQUEST)
        if otp.is_expired:
            return Response({'detail': 'OTP expired.'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save(update_fields=['password'])
        otp.is_used = True
        otp.save(update_fields=['is_used'])

        return Response({'message': 'Password reset successful.'}, status=status.HTTP_200_OK)


class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)
