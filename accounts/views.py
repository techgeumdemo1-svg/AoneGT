from django.conf import settings
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import User, PasswordResetOTP, RegistrationOTP
from .serializers import (
    RegisterSerializer,
    EmailCheckSerializer,
    RequestRegistrationOTPSerializer,
    LoginSerializer,
    ForgotPasswordRequestSerializer,
    ResetPasswordSerializer,
    UserProfileSerializer,
)
from .services.zoho_registration_gate import (
    ZohoContactCheckError,
    registration_email_check_configured,
    registration_email_exists_in_zoho,
    resolved_register_zoho_email_source,
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
                'message': 'Use this result to continue sign-in or registration.',
            },
            status=status.HTTP_200_OK,
        )


class CheckZohoContactAPIView(APIView):
    """
    Optional UX step before register: whether the email is allowed under the active Zoho source
    (Inventory contacts vs Commerce sales orders). When REGISTER_REQUIRE_ZOHO_CONTACT is False,
    returns exists_in_zoho: null.
    """

    def post(self, request):
        serializer = EmailCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        if not getattr(settings, 'REGISTER_REQUIRE_ZOHO_CONTACT', False):
            return Response(
                {
                    'email': email,
                    'zoho_check_required': False,
                    'exists_in_zoho': None,
                    'message': 'Zoho contact check is disabled (REGISTER_REQUIRE_ZOHO_CONTACT).',
                },
                status=status.HTTP_200_OK,
            )

        if not registration_email_check_configured():
            return Response(
                {
                    'detail': 'Zoho is not configured for registration checks. Set '
                    'ZOHO_ACCESS_TOKEN and the organization id for your '
                    'REGISTER_ZOHO_EMAIL_SOURCE.',
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            exists = registration_email_exists_in_zoho(email)
        except ZohoContactCheckError as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        src = resolved_register_zoho_email_source()
        return Response(
            {
                'email': email,
                'zoho_check_required': True,
                'source': src,
                'exists_in_zoho': exists,
                'message': (
                    'Email matches Zoho records. You can register.'
                    if exists
                    else (
                        'No matching Commerce sales orders for this email.'
                        if src == 'commerce_salesorders'
                        else 'Email not found in Zoho Inventory contacts.'
                    )
                ),
            },
            status=status.HTTP_200_OK,
        )


class RequestRegistrationOTPAPIView(APIView):
    """
    Sends a 6-digit code to the email for signup when REGISTER_REQUIRE_EMAIL_OTP is True.
    Uses the same generic response whether the email is ineligible, to limit enumeration.
    """

    def post(self, request):
        serializer = RequestRegistrationOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        generic = {
            'message': (
                'If this email is eligible for registration, a verification code has been sent.'
            ),
            'email': email,
        }

        if User.objects.filter(email__iexact=email).exists():
            return Response(generic, status=status.HTTP_200_OK)

        if getattr(settings, 'REGISTER_REQUIRE_ZOHO_CONTACT', False):
            if not registration_email_check_configured():
                return Response(
                    {
                        'detail': (
                            'Zoho is not configured for registration checks. Set '
                            'ZOHO_ACCESS_TOKEN and the organization id for your '
                            'REGISTER_ZOHO_EMAIL_SOURCE.'
                        ),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            try:
                if not registration_email_exists_in_zoho(email):
                    return Response(generic, status=status.HTTP_200_OK)
            except ZohoContactCheckError as e:
                return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        otp = RegistrationOTP.objects.create(email=email)
        subject = 'AoneGt registration verification code'
        message = (
            f'Your registration verification code is: {otp.otp_code}\n'
            f'This code expires in 10 minutes.\n\n'
            f'If you did not request this, you can ignore this email.'
        )
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)

        return Response(generic, status=status.HTTP_200_OK)


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

        user = User.objects.filter(email__iexact=email).first()
        if user:
            otp = PasswordResetOTP.objects.create(user=user)
            subject = 'AoneGt Password Reset OTP'
            message = (
                f'Hello {user.first_name},\n\n'
                f'Your OTP for password reset is: {otp.otp_code}\n'
                f'This OTP will expire in 10 minutes.\n\n'
                f'Reset URL: {settings.FRONTEND_RESET_URL}\n\n'
                f'If you did not request this, please ignore this email.'
            )
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
            )

        return Response(
            {
                'message': (
                    'If an account exists for this email, a password reset code has been sent.'
                ),
                'email': email,
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

        user = User.objects.filter(email__iexact=email).first()
        otp = None
        if user:
            otp = PasswordResetOTP.objects.filter(
                user=user, otp_code=otp_code, is_used=False,
            ).first()
        if not user or not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired reset request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=['password'])
        otp.is_used = True
        otp.save(update_fields=['is_used'])

        return Response({'message': 'Password reset successful.'}, status=status.HTTP_200_OK)


class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)
