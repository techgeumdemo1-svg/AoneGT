import logging
import uuid

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from shop.models import Cart, Order, UserAddress, WishlistItem
from .throttles import (
    ChangePasswordOTPThrottle,
    CheckEmailRateThrottle,
    DeactivateAccountOTPThrottle,
    DeleteAccountOTPThrottle,
    LoginRateThrottle,
    ReactivateAccountOTPThrottle,
    RegisterRateThrottle,
)

from .models import (
    User,
    PasswordResetOTP,
    RegistrationOTP,
    AccountDeactivateOTP,
    AccountDeleteOTP,
    AccountReactivateOTP,
    ChangePasswordOTP,
)
from .serializers import (
    RegisterSerializer,
    EmailCheckSerializer,
    RequestRegistrationOTPSerializer,
    LoginSerializer,
    LogoutSerializer,
    ForgotPasswordRequestSerializer,
    VerifyResetOTPSerializer,
    ResetPasswordSerializer,
    UserProfileSerializer,
    UserProfileUpdateSerializer,
    DeactivateAccountConfirmSerializer,
    DeactivateAccountOTPSerializer,
    DeleteAccountConfirmSerializer,
    DeleteAccountOTPSerializer,
    ReactivateAccountRequestSerializer,
    ReactivateAccountOTPSerializer,
    ChangePasswordConfirmSerializer,
    ChangePasswordSerializer,
)
from .services.zoho_registration_gate import (
    ZohoContactCheckError,
    registration_email_check_configured,
    registration_email_exists_in_zoho,
    resolved_register_zoho_email_source,
)

logger = logging.getLogger(__name__)


class RegisterAPIView(APIView):
    throttle_classes = [RegisterRateThrottle]

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


_EXISTS_MESSAGE_CHECK_EMAIL = 'An account exists for this email.'
_EXISTS_MESSAGE_FORGOT_PASSWORD = (
    'An account exists for this email. An OTP has been sent to this email.'
)
_NOT_EXISTS_MESSAGE = 'account not exists'


class CheckEmailAPIView(APIView):
    throttle_classes = [CheckEmailRateThrottle]

    def post(self, request):
        serializer = EmailCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        exists = User.objects.filter(email__iexact=email).exists()
        return Response(
            {
                'email': email,
                'exists': exists,
                'message': _EXISTS_MESSAGE_CHECK_EMAIL if exists else _NOT_EXISTS_MESSAGE,
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
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LogoutAPIView(APIView):
    """
    Invalidate the refresh token (server-side blacklist).
    Client should also discard access and refresh tokens locally.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data['refresh'])
            token.blacklist()
        except TokenError:
            return Response(
                {'detail': 'Invalid or expired refresh token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'message': 'Logged out successfully.'},
            status=status.HTTP_200_OK,
        )


class ForgotPasswordAPIView(APIView):
    def post(self, request):
        serializer = ForgotPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(
                {
                    'email': email,
                    'exists': False,
                    'message': _NOT_EXISTS_MESSAGE,
                },
                status=status.HTTP_200_OK,
            )

        to_email = (user.email or '').strip().lower()
        if to_email and user.email != to_email:
            User.objects.filter(pk=user.pk).update(email=to_email)
            user.email = to_email
        otp = PasswordResetOTP.objects.create(user=user)
        if not to_email or '@' not in to_email:
            otp.delete()
            logger.error(
                'forgot-password: user pk=%s has invalid stored email repr=%r',
                user.pk,
                user.email,
            )
            return Response(
                {
                    'email': email,
                    'exists': True,
                    'message': (
                        'An account exists for this email but the address is invalid. '
                        'Contact support.'
                    ),
                },
                status=status.HTTP_200_OK,
            )
        greeting = (user.first_name or '').strip() or 'there'
        subject = 'AoneGt Password Reset OTP'
        message = (
            f'Hello {greeting},\n\n'
            f'Your OTP for password reset is: {otp.otp_code}\n'
            f'This OTP will expire in 10 minutes.\n\n'
            f'Reset URL: {settings.FRONTEND_RESET_URL}\n\n'
            f'If you did not request this, please ignore this email.'
        )
        try:
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [to_email],
                fail_silently=False,
            )
        except Exception as exc:
            otp.delete()
            logger.exception(
                'forgot-password: SMTP failed to=%s (%s: %s)',
                to_email,
                type(exc).__name__,
                exc,
            )
            return Response(
                {
                    'email': email,
                    'exists': True,
                    'message': (
                        'An account exists for this email but we could not send the email. '
                        'Try again later.'
                    ),
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                'email': email,
                'exists': True,
                'message': _EXISTS_MESSAGE_FORGOT_PASSWORD,
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


class VerifyResetOTPAPIView(APIView):
    def post(self, request):
        serializer = VerifyResetOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        otp_code = serializer.validated_data['otp']

        user = User.objects.filter(email__iexact=email).first()
        otp = None
        if user:
            otp = PasswordResetOTP.objects.filter(
                user=user, otp_code=otp_code, is_used=False,
            ).first()
        if not user or not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired reset OTP.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'message': 'OTP verified successfully.'},
            status=status.HTTP_200_OK,
        )


class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)

    def patch(self, request):
        serializer = UserProfileUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        request.user.refresh_from_db()
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)


class RequestChangePasswordOTPAPIView(APIView):
    """Step 1: send OTP to the logged-in user's email (requires confirm: true)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ChangePasswordOTPThrottle]

    def post(self, request):
        serializer = ChangePasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.is_active:
            return Response(
                {'detail': 'Your account is inactive.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_email = (user.email or '').strip().lower()
        if not to_email or '@' not in to_email:
            return Response(
                {'detail': 'Your account has no valid email address for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ChangePasswordOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = ChangePasswordOTP.objects.create(user=user)

        greeting = (user.first_name or '').strip() or 'there'
        subject = 'AoneGt Change Password OTP'
        message = (
            f'Hello {greeting},\n\n'
            f'Your OTP to change your password is: {otp.otp_code}\n'
            f'This OTP will expire in 10 minutes.\n\n'
            f'If you did not request this, secure your account and ignore this email.'
        )
        try:
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [to_email],
                fail_silently=False,
            )
        except Exception as exc:
            otp.delete()
            logger.exception(
                'change-password: SMTP failed to=%s (%s: %s)',
                to_email,
                type(exc).__name__,
                exc,
            )
            return Response(
                {'detail': 'Could not send verification email. Try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'message': 'A verification code has been sent to your email.',
                'email': to_email,
            },
            status=status.HTTP_200_OK,
        )


class ChangePasswordAPIView(APIView):
    """Step 2: verify OTP and set the new password (requires Bearer token)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if not user.is_active:
            return Response(
                {'detail': 'Your account is inactive.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp_code = serializer.validated_data['otp']
        new_password = serializer.validated_data['new_password']

        otp = ChangePasswordOTP.objects.filter(
            user=user, otp_code=otp_code, is_used=False,
        ).first()
        if not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired verification code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        user.set_password(new_password)
        user.save(update_fields=['password'])
        ChangePasswordOTP.objects.filter(user=user, is_used=False).update(is_used=True)

        return Response(
            {'message': 'Password changed successfully.'},
            status=status.HTTP_200_OK,
        )


def _deactivate_user_account(user):
    user.is_active = False
    user.save(update_fields=['is_active'])
    AccountDeactivateOTP.objects.filter(user=user, is_used=False).update(is_used=True)


def _reactivate_user_account(user):
    user.is_active = True
    user.save(update_fields=['is_active'])
    AccountReactivateOTP.objects.filter(user=user, is_used=False).update(is_used=True)


class RequestDeactivateAccountOTPAPIView(APIView):
    """Step 1: send a 6-digit OTP (requires confirm: true from the client)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [DeactivateAccountOTPThrottle]

    def post(self, request):
        serializer = DeactivateAccountConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.is_active:
            return Response(
                {'detail': 'This account is already deactivated.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_email = (user.email or '').strip().lower()
        if not to_email or '@' not in to_email:
            return Response(
                {'detail': 'Your account has no valid email address for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        AccountDeactivateOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = AccountDeactivateOTP.objects.create(user=user)

        greeting = (user.first_name or '').strip() or 'there'
        subject = 'AoneGt Account Deactivation OTP'
        message = (
            f'Hello {greeting},\n\n'
            f'Your OTP to deactivate your account is: {otp.otp_code}\n'
            f'This OTP will expire in 10 minutes.\n\n'
            f'If you did not request this, secure your account and ignore this email.'
        )
        try:
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [to_email],
                fail_silently=False,
            )
        except Exception as exc:
            otp.delete()
            logger.exception(
                'deactivate-account: SMTP failed to=%s (%s: %s)',
                to_email,
                type(exc).__name__,
                exc,
            )
            return Response(
                {'detail': 'Could not send verification email. Try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'message': 'A verification code has been sent to your email.',
                'email': to_email,
            },
            status=status.HTTP_200_OK,
        )


class DeactivateAccountAPIView(APIView):
    """
    Step 2: confirm deactivation with confirm: true and OTP from email.
    Marks the user inactive. Existing JWTs stay valid until expiry; login will be rejected.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if not user.is_active:
            return Response(
                {'detail': 'This account is already deactivated.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = DeactivateAccountOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp_code = serializer.validated_data['otp']

        otp = AccountDeactivateOTP.objects.filter(
            user=user, otp_code=otp_code, is_used=False,
        ).first()
        if not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired verification code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        _deactivate_user_account(user)
        user.refresh_from_db()

        return Response(
            {
                'status': 'success',
                'message': 'Your account has been deactivated.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class RequestReactivateAccountOTPAPIView(APIView):
    """
    Step 1: send OTP to reactivate a deactivated account (no Bearer token; user cannot log in).
    """

    throttle_classes = [ReactivateAccountOTPThrottle]

    def post(self, request):
        serializer = ReactivateAccountRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(
                {'detail': 'No account found for this email.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if user.is_active:
            return Response(
                {'detail': 'This account is already active. You can log in.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_email = (user.email or '').strip().lower()
        if not to_email or '@' not in to_email:
            return Response(
                {'detail': 'This account has no valid email address for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        AccountReactivateOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = AccountReactivateOTP.objects.create(user=user)

        greeting = (user.first_name or '').strip() or 'there'
        subject = 'AoneGt Account Reactivation OTP'
        message = (
            f'Hello {greeting},\n\n'
            f'Your OTP to reactivate your account is: {otp.otp_code}\n'
            f'This OTP will expire in 10 minutes.\n\n'
            f'If you did not request this, secure your account and ignore this email.'
        )
        try:
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [to_email],
                fail_silently=False,
            )
        except Exception as exc:
            otp.delete()
            logger.exception(
                'reactivate-account: SMTP failed to=%s (%s: %s)',
                to_email,
                type(exc).__name__,
                exc,
            )
            return Response(
                {'detail': 'Could not send verification email. Try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'message': 'A verification code has been sent to your email.',
                'email': to_email,
            },
            status=status.HTTP_200_OK,
        )


class ReactivateAccountAPIView(APIView):
    """Step 2: verify OTP and set is_active=True."""

    def post(self, request):
        serializer = ReactivateAccountOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        otp_code = serializer.validated_data['otp']

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(
                {'detail': 'No account found for this email.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if user.is_active:
            return Response(
                {'detail': 'This account is already active. You can log in.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp = AccountReactivateOTP.objects.filter(
            user=user, otp_code=otp_code, is_used=False,
        ).first()
        if not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired verification code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        _reactivate_user_account(user)
        user.refresh_from_db()

        return Response(
            {
                'status': 'success',
                'message': 'Your account has been reactivated. You can log in now.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class RequestDeleteAccountOTPAPIView(APIView):
    """Step 1: send a 6-digit OTP (requires confirm: true from the client)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [DeleteAccountOTPThrottle]

    def post(self, request):
        serializer = DeleteAccountConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        to_email = (user.email or '').strip().lower()
        if not to_email or '@' not in to_email:
            return Response(
                {'detail': 'Your account has no valid email address for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        AccountDeleteOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = AccountDeleteOTP.objects.create(user=user)

        greeting = (user.first_name or '').strip() or 'there'
        subject = 'AoneGt Account Deletion OTP'
        message = (
            f'Hello {greeting},\n\n'
            f'Your OTP to permanently delete your account is: {otp.otp_code}\n'
            f'This OTP will expire in 10 minutes.\n\n'
            f'If you did not request this, secure your account and ignore this email.'
        )
        try:
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [to_email],
                fail_silently=False,
            )
        except Exception as exc:
            otp.delete()
            logger.exception(
                'delete-account: SMTP failed to=%s (%s: %s)',
                to_email,
                type(exc).__name__,
                exc,
            )
            return Response(
                {'detail': 'Could not send verification email. Try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'message': 'A verification code has been sent to your email.',
                'email': to_email,
            },
            status=status.HTTP_200_OK,
        )


class DeleteAccountAPIView(APIView):
    """
    Step 2: confirm deletion with confirm: true and OTP from email.
    Users with order history are anonymized; others are deleted from the DB.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DeleteAccountOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp_code = serializer.validated_data['otp']

        user = request.user
        otp = AccountDeleteOTP.objects.filter(
            user=user, otp_code=otp_code, is_used=False,
        ).first()
        if not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired verification code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp.is_used = True
        otp.save(update_fields=['is_used'])

        has_orders = Order.objects.filter(user=user).exists()

        with transaction.atomic():
            Cart.objects.filter(user=user).delete()
            WishlistItem.objects.filter(user=user).delete()
            UserAddress.objects.filter(user=user).delete()
            PasswordResetOTP.objects.filter(user=user).delete()
            ChangePasswordOTP.objects.filter(user=user).delete()
            AccountDeactivateOTP.objects.filter(user=user).delete()
            AccountDeleteOTP.objects.filter(user=user).delete()
            AccountReactivateOTP.objects.filter(user=user).delete()

            if has_orders:
                suffix = uuid.uuid4().hex[:12]
                user.email = f'deleted-{user.pk}-{suffix}@invalid.invalid'
                user.first_name = 'Deleted'
                user.last_name = ''
                user.phone = ''
                user.is_active = False
                user.set_unusable_password()
                user.save()
                mode = 'anonymized'
                body = {
                    'status': 'success',
                    'message': (
                        'Your personal data has been removed from your profile. '
                        'Order history is retained under an anonymized account.'
                    ),
                    'mode': mode,
                }
            else:
                user.delete()
                mode = 'deleted'
                body = {
                    'status': 'success',
                    'message': 'Your account and associated data have been deleted.',
                    'mode': mode,
                }

        return Response(body, status=status.HTTP_200_OK)
