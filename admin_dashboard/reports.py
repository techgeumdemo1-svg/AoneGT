import io
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import AccountCreditLedger, Cart, OrderReturn

from .orders import _paginate_queryset, _parse_order_list_date
from .returns import (
    AdminReturnListSerializer,
    _admin_returns_queryset,
    _apply_return_list_filters,
    _customer_display_name,
    _return_reason_label,
    _return_refund_amount,
)
from .views import IsStaffUser

_EXPORT_MAX_ROWS = 5000
_VALID_EXPORT_REPORTS = {"cart-abandonment", "refunds"}


def _parse_threshold_hours(request) -> int:
    raw = (request.query_params.get("threshold_hours") or "24").strip()
    try:
        hours = max(int(raw), 1)
        return min(hours, 24 * 90)
    except (TypeError, ValueError):
        return 24


def _apply_updated_at_date_filter(queryset, request) -> Tuple[object, Optional[str], Optional[dict]]:
    date_from_raw = (
        request.query_params.get("date_from")
        or request.query_params.get("start_date")
        or ""
    ).strip()
    date_to_raw = (
        request.query_params.get("date_to")
        or request.query_params.get("end_date")
        or ""
    ).strip()
    period = (request.query_params.get("period") or "").strip().lower()

    if date_from_raw or date_to_raw:
        date_from = _parse_order_list_date(date_from_raw)
        date_to = _parse_order_list_date(date_to_raw)
        if date_from_raw and date_from is None:
            return queryset, "Invalid date_from/start_date. Use YYYY-MM-DD.", None
        if date_to_raw and date_to is None:
            return queryset, "Invalid date_to/end_date. Use YYYY-MM-DD.", None
        if date_from and date_to and date_from > date_to:
            return queryset, "date_from must be on or before date_to.", None
        meta = {"type": "custom", "date_from": None, "date_to": None}
        if date_from:
            queryset = queryset.filter(updated_at__date__gte=date_from)
            meta["date_from"] = date_from.isoformat()
        if date_to:
            queryset = queryset.filter(updated_at__date__lte=date_to)
            meta["date_to"] = date_to.isoformat()
        return queryset, None, meta

    if not period or period in ("all", "all_time", "alltime"):
        return queryset, None, {"type": "all"}

    today = timezone.localdate()
    if period in ("7days", "last_7_days", "last7days"):
        start = today - timedelta(days=6)
        return (
            queryset.filter(updated_at__date__gte=start, updated_at__date__lte=today),
            None,
            {"type": "last_7_days", "date_from": start.isoformat(), "date_to": today.isoformat()},
        )
    if period in ("this_month", "month", "thismonth"):
        start = today.replace(day=1)
        return (
            queryset.filter(updated_at__date__gte=start, updated_at__date__lte=today),
            None,
            {"type": "this_month", "date_from": start.isoformat(), "date_to": today.isoformat()},
        )
    return (
        queryset,
        "Invalid period. Use: all, 7days, this_month, or custom date_from/date_to (YYYY-MM-DD).",
        None,
    )


def _cart_subtotal(cart: Cart) -> Decimal:
    total = Decimal("0")
    for item in cart.items.all():
        total += item.line_subtotal
    return total.quantize(Decimal("0.01"))


def _cart_stores(cart: Cart) -> List[str]:
    names = []
    seen = set()
    for item in cart.items.all():
        if item.store_id and item.store_id not in seen:
            seen.add(item.store_id)
            names.append(item.store.name)
    return names


def _cart_items_payload(cart: Cart) -> list:
    rows = []
    for item in cart.items.all():
        rows.append(
            {
                "product_id": item.product_id,
                "product_name": item.product.name if item.product_id else "",
                "store_id": item.store_id,
                "store_name": item.store.name if item.store_id else "",
                "quantity": item.quantity,
                "unit_price": str(Decimal(str(item.product.price)).quantize(Decimal("0.01"))),
                "line_subtotal": str(item.line_subtotal.quantize(Decimal("0.01"))),
            }
        )
    return rows


def _cart_abandonment_row(cart: Cart) -> dict:
    subtotal = _cart_subtotal(cart)
    stores = _cart_stores(cart)
    return {
        "cart_id": cart.pk,
        "customer_id": cart.user_id,
        "customer_email": cart.user.email,
        "customer_name": _customer_display_name(cart.user),
        "last_updated_at": cart.updated_at.isoformat() if cart.updated_at else None,
        "items_count": cart.items.count(),
        "subtotal_aed": str(subtotal),
        "stores": stores,
        "items": _cart_items_payload(cart),
    }


def _abandoned_carts_queryset(request):
    threshold_hours = _parse_threshold_hours(request)
    cutoff = timezone.now() - timedelta(hours=threshold_hours)
    qs = (
        Cart.objects.annotate(items_count=Count("items", distinct=True))
        .filter(items_count__gt=0, updated_at__lt=cutoff)
        .select_related("user")
        .prefetch_related("items__product", "items__store")
        .order_by("-updated_at")
    )
    return qs, threshold_hours


def _apply_cart_abandonment_filters(queryset, request):
    store_id = (request.query_params.get("store_id") or "").strip()
    if store_id.isdigit():
        queryset = queryset.filter(items__store_id=int(store_id)).distinct()

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = (
            Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__phone__icontains=search)
        )
        if search.isdigit():
            q |= Q(pk=int(search)) | Q(user_id=int(search))
        queryset = queryset.filter(q)

    return _apply_updated_at_date_filter(queryset, request)


def _cart_abandonment_summary(carts) -> dict:
    total_items = 0
    total_value = Decimal("0")
    for cart in carts:
        total_items += cart.items.count()
        total_value += _cart_subtotal(cart)
    return {
        "abandoned_carts": len(carts),
        "total_items": total_items,
        "total_value_aed": str(total_value.quantize(Decimal("0.01"))),
    }


def _refunded_at_for_return(ret: OrderReturn) -> Optional[str]:
    if ret.status != OrderReturn.Status.COMPLETED:
        return None
    entry = (
        AccountCreditLedger.objects.filter(
            reference=f"return:{ret.pk}",
            kind=AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
            amount__gt=0,
        )
        .order_by("-created_at")
        .first()
    )
    return entry.created_at.isoformat() if entry and entry.created_at else None


def _refund_report_row(ret: OrderReturn) -> dict:
    data = AdminReturnListSerializer(ret).data
    data["refunded_at"] = _refunded_at_for_return(ret)
    data["currency"] = ret.order.currency or "AED"
    return data


def _refunds_summary(returns) -> dict:
    by_status = {}
    by_reason = {}
    total_refund_amount = Decimal("0")
    completed_refund_amount = Decimal("0")
    completed_count = 0

    for ret in returns:
        amount = _return_refund_amount(ret)
        status_key = ret.status
        by_status[status_key] = by_status.get(status_key, 0) + 1

        reason_key = ret.return_reason or "unspecified"
        reason_row = by_reason.setdefault(
            reason_key,
            {"count": 0, "refund_amount_aed": Decimal("0"), "label": _return_reason_label(ret) or reason_key},
        )
        reason_row["count"] += 1
        reason_row["refund_amount_aed"] += amount

        total_refund_amount += amount
        if ret.status == OrderReturn.Status.COMPLETED:
            completed_count += 1
            completed_refund_amount += amount

    by_reason_payload = {
        key: {
            "count": row["count"],
            "refund_amount_aed": str(row["refund_amount_aed"].quantize(Decimal("0.01"))),
            "label": row["label"],
        }
        for key, row in by_reason.items()
    }

    return {
        "total_returns": len(returns),
        "total_refund_amount_aed": str(total_refund_amount.quantize(Decimal("0.01"))),
        "completed_count": completed_count,
        "completed_refund_amount_aed": str(completed_refund_amount.quantize(Decimal("0.01"))),
        "by_status": by_status,
        "by_reason": by_reason_payload,
    }


def _validate_export_report(request) -> Tuple[Optional[str], Optional[str]]:
    report = (request.query_params.get("report") or "").strip().lower()
    if not report:
        return None, "Query param report is required. Use: cart-abandonment or refunds."
    if report not in _VALID_EXPORT_REPORTS:
        return None, "Invalid report. Use: cart-abandonment or refunds."
    return report, None


def _export_filename(report: str, ext: str) -> str:
    today = timezone.localdate().isoformat()
    return f"{report}-report-{today}.{ext}"


def _build_cart_abandonment_export_rows(carts) -> Tuple[list, list]:
    headers = [
        "Cart ID",
        "Customer Email",
        "Customer Name",
        "Last Updated",
        "Items Count",
        "Subtotal (AED)",
        "Stores",
    ]
    rows = []
    for cart in carts:
        rows.append(
            [
                cart.pk,
                cart.user.email,
                _customer_display_name(cart.user),
                cart.updated_at.strftime("%Y-%m-%d %H:%M") if cart.updated_at else "",
                cart.items.count(),
                str(_cart_subtotal(cart)),
                ", ".join(_cart_stores(cart)),
            ]
        )
    return headers, rows


def _build_refunds_export_rows(returns) -> Tuple[list, list]:
    headers = [
        "Return ID",
        "Order ID",
        "Order Code",
        "Customer Email",
        "Customer Name",
        "Store",
        "Status",
        "Reason",
        "Refund Amount (AED)",
        "Refunded At",
        "Created At",
    ]
    rows = []
    for ret in returns:
        data = _refund_report_row(ret)
        rows.append(
            [
                data["return_id"],
                data["order_id"],
                data["order_code"],
                data["customer_email"],
                data["customer_name"],
                data["store_name"],
                data["status_label"],
                data["return_reason_label"],
                data["refund_amount"],
                data["refunded_at"] or "",
                data["created_at"],
            ]
        )
    return headers, rows


def _build_excel_response(report: str, headers: list, rows: list) -> HttpResponse:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = report.replace("-", " ").title()[:31]
    sheet.append(headers)
    for row in rows:
        sheet.append(row)

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{_export_filename(report, "xlsx")}"'
    return response


def _build_pdf_response(report: str, title: str, headers: list, rows: list) -> HttpResponse:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles["Title"]), Spacer(1, 12)]

    table_data = [headers] + rows
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{_export_filename(report, "pdf")}"'
    return response


class AdminCartAbandonmentReportAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs, threshold_hours = _abandoned_carts_queryset(request)
        qs, date_err, date_filter = _apply_cart_abandonment_filters(qs, request)
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)

        summary_carts = list(qs[:_EXPORT_MAX_ROWS])
        page_qs, pagination = _paginate_queryset(qs, request)
        payload = {
            **pagination,
            "threshold_hours": threshold_hours,
            "summary": {
                **_cart_abandonment_summary(summary_carts),
                "threshold_hours": threshold_hours,
            },
            "results": [_cart_abandonment_row(cart) for cart in page_qs],
        }
        if date_filter:
            payload["date_filter"] = date_filter
        return Response(payload, status=status.HTTP_200_OK)


class AdminRefundsReportAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs, date_err, date_filter = _apply_return_list_filters(
            _admin_returns_queryset().order_by("-created_at"),
            request,
        )
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)

        summary_returns = list(qs[:_EXPORT_MAX_ROWS])
        page_qs, pagination = _paginate_queryset(qs, request)
        payload = {
            **pagination,
            "summary": _refunds_summary(summary_returns),
            "results": [_refund_report_row(ret) for ret in page_qs],
        }
        if date_filter:
            payload["date_filter"] = date_filter
        return Response(payload, status=status.HTTP_200_OK)


class AdminReportExcelExportAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        report, err = _validate_export_report(request)
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        if report == "cart-abandonment":
            qs, _ = _abandoned_carts_queryset(request)
            qs, date_err, _ = _apply_cart_abandonment_filters(qs, request)
            if date_err:
                return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)
            carts = list(qs[:_EXPORT_MAX_ROWS])
            headers, rows = _build_cart_abandonment_export_rows(carts)
            title = "Cart Abandonment Report"
        else:
            qs, date_err, _ = _apply_return_list_filters(
                _admin_returns_queryset().order_by("-created_at"),
                request,
            )
            if date_err:
                return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)
            returns = list(qs[:_EXPORT_MAX_ROWS])
            headers, rows = _build_refunds_export_rows(returns)
            title = "Refunds Report"

        return _build_excel_response(report, headers, rows)


class AdminReportPdfExportAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        report, err = _validate_export_report(request)
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        if report == "cart-abandonment":
            qs, _ = _abandoned_carts_queryset(request)
            qs, date_err, _ = _apply_cart_abandonment_filters(qs, request)
            if date_err:
                return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)
            carts = list(qs[:_EXPORT_MAX_ROWS])
            headers, rows = _build_cart_abandonment_export_rows(carts)
            title = "Cart Abandonment Report"
        else:
            qs, date_err, _ = _apply_return_list_filters(
                _admin_returns_queryset().order_by("-created_at"),
                request,
            )
            if date_err:
                return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)
            returns = list(qs[:_EXPORT_MAX_ROWS])
            headers, rows = _build_refunds_export_rows(returns)
            title = "Refunds Report"

        return _build_pdf_response(report, title, headers, rows)
