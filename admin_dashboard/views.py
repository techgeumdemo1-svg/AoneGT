import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import PasswordResetOTP
from shop.models import Order, OrderReturn
from .models import AdminLoginOTP
from .serializers import (
    AdminForgotPasswordSerializer,
    AdminLoginSerializer,
    AdminLoginVerifyOTPSerializer,
    AdminLogoutSerializer,
    AdminMeSerializer,
    AdminResetPasswordSerializer,
    build_admin_login_response,
)
from .throttles import AdminLoginOTPThrottle

logger = logging.getLogger(__name__)
User = get_user_model()


class IsStaffUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.is_superuser)
        )


class AdminLoginAPIView(APIView):
    """Step 1: email + password → send login OTP to email."""

    throttle_classes = [AdminLoginOTPThrottle]

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        to_email = (user.email or "").strip().lower()
        if not to_email or "@" not in to_email:
            return Response(
                {"detail": "Account email is invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        AdminLoginOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = AdminLoginOTP.objects.create(user=user)
        subject = "AoneGt Admin Login Verification"
        message = (
            f"Your admin login verification code is: {otp.otp_code}\n"
            "This code expires in 10 minutes.\n\n"
            "If you did not attempt to log in, ignore this email."
        )
        try:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
        except Exception as exc:
            otp.delete()
            logger.exception("admin-login: SMTP failed (%s)", exc)
            return Response(
                {"detail": "Could not send verification email. Try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "message": "A login verification code was sent to your email.",
                "email": to_email,
                "requires_otp": True,
            },
            status=status.HTTP_200_OK,
        )


class AdminLoginVerifyOTPAPIView(APIView):
    """Step 2: email + OTP → JWT tokens."""

    throttle_classes = [AdminLoginOTPThrottle]

    def post(self, request):
        serializer = AdminLoginVerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp = serializer.validated_data["otp_row"]
        user = serializer.validated_data["user"]
        otp.is_used = True
        otp.save(update_fields=["is_used"])
        AdminLoginOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        return Response(build_admin_login_response(user), status=status.HTTP_200_OK)


class AdminLogoutAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        serializer = AdminLogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"message": "Logged out successfully."}, status=status.HTTP_200_OK)


class AdminForgotPasswordAPIView(APIView):
    def post(self, request):
        serializer = AdminForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        generic = {
            "message": "If an admin account exists for this email, a reset OTP was sent.",
            "email": email,
        }
        user = User.objects.filter(email__iexact=email, is_staff=True).first()
        if not user:
            return Response(generic, status=status.HTTP_200_OK)

        to_email = (user.email or "").strip().lower()
        if not to_email or "@" not in to_email:
            logger.error("admin-forgot-password: invalid email for user=%s", user.pk)
            return Response(generic, status=status.HTTP_200_OK)

        otp = PasswordResetOTP.objects.create(user=user)
        subject = "AoneGt Admin Password Reset OTP"
        message = (
            f"Your admin password reset OTP is: {otp.otp_code}\n"
            "This OTP expires in 10 minutes.\n\n"
            "If you did not request this, ignore this email."
        )
        try:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
        except Exception as exc:
            otp.delete()
            logger.exception("admin-forgot-password: SMTP failed (%s)", exc)
            return Response(
                {"detail": "Could not send reset email. Try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(generic, status=status.HTTP_200_OK)


class AdminResetPasswordAPIView(APIView):
    def post(self, request):
        serializer = AdminResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        otp_code = serializer.validated_data["otp"]
        new_password = serializer.validated_data["new_password"]

        user = User.objects.filter(email__iexact=email, is_staff=True).first()
        otp = None
        if user:
            otp = PasswordResetOTP.objects.filter(
                user=user,
                otp_code=otp_code,
                is_used=False,
            ).first()
        if not user or not otp or otp.is_expired:
            return Response(
                {"detail": "Invalid or expired reset request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])
        otp.is_used = True
        otp.save(update_fields=["is_used"])

        return Response({"message": "Password reset successful."}, status=status.HTTP_200_OK)


class AdminMeAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        return Response(AdminMeSerializer(request.user).data, status=status.HTTP_200_OK)


class AdminDashboardSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        start_current = today - timedelta(days=6)
        start_previous = start_current - timedelta(days=7)

        def growth_pct(current_value, previous_value):
            if previous_value == 0:
                return 100.0 if current_value > 0 else 0.0
            return round(((current_value - previous_value) / previous_value) * 100, 1)

        total_orders_today = Order.objects.filter(
            created_at__date=today,
        ).count()
        pending_orders = Order.objects.filter(
            status=Order.Status.PENDING_ZOHO_SYNC,
        ).count()
        active_customers = User.objects.filter(
            is_staff=False,
            is_superuser=False,
            orders__created_at__date__gte=start_current,
        ).distinct().count()
        return_requests = OrderReturn.objects.filter(
            status__in=(
                OrderReturn.Status.PENDING_ZOHO,
                OrderReturn.Status.SYNCED,
            )
        ).count()

        orders_delivered = Order.objects.filter(
            customer_tracking_stage=Order.CustomerTrackingStage.DELIVERED,
        ).count()
        orders_cancelled = Order.objects.filter(
            status=Order.Status.CANCELLED,
        ).count()

        current_orders = Order.objects.filter(
            created_at__date__gte=start_current,
            created_at__date__lt=tomorrow,
        ).count()
        previous_orders = Order.objects.filter(
            created_at__date__gte=start_previous,
            created_at__date__lt=start_current,
        ).count()

        current_pending = Order.objects.filter(
            status=Order.Status.PENDING_ZOHO_SYNC,
            created_at__date__gte=start_current,
            created_at__date__lt=tomorrow,
        ).count()
        previous_pending = Order.objects.filter(
            status=Order.Status.PENDING_ZOHO_SYNC,
            created_at__date__gte=start_previous,
            created_at__date__lt=start_current,
        ).count()

        current_active_customers = User.objects.filter(
            is_staff=False,
            is_superuser=False,
            orders__created_at__date__gte=start_current,
            orders__created_at__date__lt=tomorrow,
        ).distinct().count()
        previous_active_customers = User.objects.filter(
            is_staff=False,
            is_superuser=False,
            orders__created_at__date__gte=start_previous,
            orders__created_at__date__lt=start_current,
        ).distinct().count()

        payload = {
            "total_orders_today": total_orders_today,
            "pending_orders": pending_orders,
            "active_customers": active_customers,
            "return_requests": return_requests,
            "orders_delivered": orders_delivered,
            "orders_cancelled": orders_cancelled,
            "order_growth": growth_pct(current_orders, previous_orders),
            "pending_growth": growth_pct(current_pending, previous_pending),
            "customer_growth": growth_pct(
                current_active_customers,
                previous_active_customers,
            ),
        }
        return Response(payload, status=status.HTTP_200_OK)


def _parse_dashboard_period(raw: str) -> int:
    """Return number of days from query values like '7days', '30days'. Default 7."""
    value = (raw or "7days").strip().lower()
    if value.endswith("days"):
        try:
            days = int(value[:-4])
            if 1 <= days <= 365:
                return days
        except ValueError:
            pass
    return 7


class AdminDashboardChartsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        days = _parse_dashboard_period(request.query_params.get("period"))
        today = timezone.localdate()
        start_date = today - timedelta(days=days - 1)
        tomorrow = today + timedelta(days=1)

        period_orders = Order.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lt=tomorrow,
        )

        order_rows = (
            period_orders.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                orders=Count("id"),
                revenue=Sum("total"),
            )
            .order_by("day")
        )
        orders_by_day = {row["day"]: row for row in order_rows}

        sales_trend = []
        for i in range(days):
            day = start_date + timedelta(days=i)
            row = orders_by_day.get(day, {})
            revenue = row.get("revenue") or Decimal("0")
            sales_trend.append(
                {
                    "date": day.isoformat(),
                    "orders": row.get("orders", 0),
                    "revenue": str(revenue.quantize(Decimal("0.01"))),
                }
            )

        status_distribution = list(
            period_orders.values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )

        tracking_distribution = list(
            period_orders.exclude(customer_tracking_stage="")
            .values("customer_tracking_stage")
            .annotate(count=Count("id"))
            .order_by("customer_tracking_stage")
        )

        payload = {
            "period": f"{days}days",
            "start_date": start_date.isoformat(),
            "end_date": today.isoformat(),
            "sales_trend": sales_trend,
            "order_status_distribution": status_distribution,
            "order_tracking_distribution": tracking_distribution,
        }
        return Response(payload, status=status.HTTP_200_OK)