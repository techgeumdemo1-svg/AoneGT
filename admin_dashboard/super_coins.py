from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.loyalty import (
    aed_per_point_earned,
    coupon_credit_aed,
    coupon_expiry_days,
    coupon_points_block,
    min_points_to_redeem,
    point_value_aed,
)
from shop.models import LoyaltyIssuedCoupon, PurchasePointsLedger
from shop.serializers import order_code_for_order

from .orders import _apply_order_date_filter
from .views import IsStaffUser

User = get_user_model()


def _customers_queryset():
    return User.objects.filter(is_staff=False, is_superuser=False)


def _customer_display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or user.email


def _quantize_decimal(value) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def _loyalty_settings_payload() -> dict:
    earn_step = aed_per_point_earned()
    point_value = point_value_aed()
    min_redeem = min_points_to_redeem()
    expiry_days = coupon_expiry_days()
    block = coupon_points_block()
    block_credit = coupon_credit_aed()
    return {
        "aed_per_point_earned": earn_step,
        "point_value_aed": _quantize_decimal(point_value),
        "min_points_to_redeem": min_redeem,
        "coupon_points_block": block,
        "coupon_credit_aed_per_block": _quantize_decimal(block_credit),
        "coupon_expiry_days": expiry_days,
        "earn_rule": f"Earn 1 Super Coin per {earn_step} AED spent (AED orders only).",
        "redeem_rule": f"1 Super Coin = {_quantize_decimal(point_value)} AED discount at checkout.",
        "min_redeem_rule": f"Minimum {min_redeem} Super Coins required to redeem.",
        "coupon_rule": (
            f"Exchange Super Coins in blocks of {block} for a coupon worth "
            f"{_quantize_decimal(block_credit)} AED store credit each."
        ),
        "coupon_expiry_rule": f"Issued coupons expire after {expiry_days} days.",
    }


def build_customer_super_coins_payload(user, *, history_limit: int = 50) -> dict:
    point_value = point_value_aed()
    balance = int(user.points_balance or 0)

    earned = []
    for row in (
        PurchasePointsLedger.objects.filter(user=user)
        .select_related("order")
        .order_by("-created_at")[:history_limit]
    ):
        earned.append(
            {
                "type": "earned",
                "points": row.points_awarded,
                "order_id": row.order_id,
                "order_code": order_code_for_order(row.order) if row.order_id else None,
                "note": row.note or "",
                "at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    spent = []
    for row in LoyaltyIssuedCoupon.objects.filter(user=user).order_by("-created_at")[:history_limit]:
        spent.append(
            {
                "type": "spent",
                "points": row.points_spent,
                "amount_aed": _quantize_decimal(row.amount_aed),
                "coupon_code": row.code,
                "used": bool(row.used_at),
                "used_at": row.used_at.isoformat() if row.used_at else None,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "order_id": row.order_id,
                "at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    history = sorted(
        earned + spent,
        key=lambda item: item.get("at") or "",
        reverse=True,
    )

    return {
        "customer_id": user.pk,
        "customer_email": user.email,
        "customer_name": _customer_display_name(user),
        "super_coins_balance": balance,
        "point_value_aed": _quantize_decimal(point_value),
        "balance_value_aed": _quantize_decimal(Decimal(balance) * point_value),
        "credit_balance_aed": _quantize_decimal(user.credit_balance_aed),
        "history": history,
    }


def _super_coins_summary_payload(request) -> tuple[dict, Optional[str]]:
    customers = _customers_queryset()
    point_value = point_value_aed()
    now = timezone.now()

    outstanding = int(customers.aggregate(total=Sum("points_balance"))["total"] or 0)
    customers_with_balance = customers.filter(points_balance__gt=0).count()
    total_customers = customers.count()

    earned_qs = PurchasePointsLedger.objects.all()
    spent_qs = LoyaltyIssuedCoupon.objects.all()
    earned_qs, date_err, date_filter = _apply_order_date_filter(earned_qs, request)
    if date_err:
        return {}, date_err
    spent_qs, date_err, _ = _apply_order_date_filter(spent_qs, request)
    if date_err:
        return {}, date_err

    total_earned_all_time = int(
        PurchasePointsLedger.objects.aggregate(total=Sum("points_awarded"))["total"] or 0
    )
    total_spent_all_time = int(
        LoyaltyIssuedCoupon.objects.aggregate(total=Sum("points_spent"))["total"] or 0
    )
    period_points_earned = int(earned_qs.aggregate(total=Sum("points_awarded"))["total"] or 0)
    period_points_spent = int(spent_qs.aggregate(total=Sum("points_spent"))["total"] or 0)

    active_coupons = LoyaltyIssuedCoupon.objects.filter(
        used_at__isnull=True,
        expires_at__gt=now,
    ).count()
    used_coupons = LoyaltyIssuedCoupon.objects.filter(used_at__isnull=False).count()

    top_holders = []
    for user in customers.filter(points_balance__gt=0).order_by("-points_balance")[:5]:
        balance = int(user.points_balance or 0)
        top_holders.append(
            {
                "customer_id": user.pk,
                "customer_email": user.email,
                "customer_name": _customer_display_name(user),
                "super_coins_balance": balance,
                "balance_value_aed": _quantize_decimal(Decimal(balance) * point_value),
            }
        )

    payload = {
        "total_super_coins_outstanding": outstanding,
        "total_outstanding_value_aed": _quantize_decimal(Decimal(outstanding) * point_value),
        "customers_with_balance": customers_with_balance,
        "total_customers": total_customers,
        "total_points_earned_all_time": total_earned_all_time,
        "total_points_spent_all_time": total_spent_all_time,
        "period_points_earned": period_points_earned,
        "period_points_spent": period_points_spent,
        "active_coupons": active_coupons,
        "used_coupons": used_coupons,
        "top_holders": top_holders,
        "settings": _loyalty_settings_payload(),
    }
    if date_filter:
        payload["date_filter"] = date_filter
    return payload, None


class AdminSuperCoinsSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        payload, date_err = _super_coins_summary_payload(request)
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=status.HTTP_200_OK)


class AdminSuperCoinsCustomerAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, customer_id):
        user = get_object_or_404(_customers_queryset(), pk=customer_id)
        return Response(
            {
                **build_customer_super_coins_payload(user),
                "settings": _loyalty_settings_payload(),
            },
            status=status.HTTP_200_OK,
        )


class AdminSuperCoinsSettingsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        return Response(_loyalty_settings_payload(), status=status.HTTP_200_OK)
