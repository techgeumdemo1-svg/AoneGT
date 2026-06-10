"""
Admin finance endpoints for Zoho Books store configuration and journal audit logs.

Endpoints:
  GET  /api/admin/finance/store-config/           — list all ZohoBooksStoreConfig records
  GET  /api/admin/finance/store-config/<store_id>/ — get config for a store
  PUT  /api/admin/finance/store-config/<store_id>/ — create or update config (partial update)
  GET  /api/admin/finance/journals/               — paginated journal log list
  POST /api/admin/finance/journals/<id>/retry/    — retry a failed journal
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Store, ZohoBooksStoreConfig
from shop.models import Order, ZohoBooksJournalLog

from .orders import _paginate_queryset, _parse_order_list_date
from .views import IsStaffUser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class ZohoBooksStoreConfigSerializer(serializers.ModelSerializer):
    store_id = serializers.IntegerField(source='store.id', read_only=True)
    store_name = serializers.CharField(source='store.name', read_only=True)

    class Meta:
        model = ZohoBooksStoreConfig
        fields = (
            'store_id',
            'store_name',
            'deposit_account_id',
            'deposit_account_name',
            'charge_account_id',
            'charge_account_name',
            'vat_account_id',
            'vat_account_name',
            'gateway_charge_rate',
            'paylink_charge_rate',
            'cod_charge_rate',
            'gateway_vat_rate',
            'paylink_vat_rate',
            'cod_vat_rate',
            'journal_gateway_enabled',
            'journal_paylink_enabled',
            'journal_cod_enabled',
            'updated_at',
        )
        read_only_fields = ('store_id', 'store_name', 'updated_at')


class ZohoBooksStoreConfigWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ZohoBooksStoreConfig
        fields = (
            'deposit_account_id',
            'deposit_account_name',
            'charge_account_id',
            'charge_account_name',
            'vat_account_id',
            'vat_account_name',
            'gateway_charge_rate',
            'paylink_charge_rate',
            'cod_charge_rate',
            'gateway_vat_rate',
            'paylink_vat_rate',
            'cod_vat_rate',
            'journal_gateway_enabled',
            'journal_paylink_enabled',
            'journal_cod_enabled',
        )

    def validate_gateway_charge_rate(self, value):
        if value < Decimal('0'):
            raise serializers.ValidationError('Rate must be 0 or greater.')
        return value

    def validate_paylink_charge_rate(self, value):
        if value < Decimal('0'):
            raise serializers.ValidationError('Rate must be 0 or greater.')
        return value

    def validate_cod_charge_rate(self, value):
        if value < Decimal('0'):
            raise serializers.ValidationError('Rate must be 0 or greater.')
        return value

    def validate_gateway_vat_rate(self, value):
        if value < Decimal('0'):
            raise serializers.ValidationError('Rate must be 0 or greater.')
        return value

    def validate_paylink_vat_rate(self, value):
        if value < Decimal('0'):
            raise serializers.ValidationError('Rate must be 0 or greater.')
        return value

    def validate_cod_vat_rate(self, value):
        if value < Decimal('0'):
            raise serializers.ValidationError('Rate must be 0 or greater.')
        return value


class ZohoBooksJournalLogSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(read_only=True)
    store_id = serializers.IntegerField(source='order.store_id', read_only=True)
    store_name = serializers.CharField(source='order.store.name', read_only=True)

    class Meta:
        model = ZohoBooksJournalLog
        fields = (
            'id',
            'order_id',
            'store_id',
            'store_name',
            'journal_type',
            'payment_method',
            'rate_used',
            'base_amount',
            'journal_amount',
            'journal_date',
            'zoho_journal_id',
            'error',
            'created_at',
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Views — Store Config
# ---------------------------------------------------------------------------

class AdminFinanceStoreConfigListAPIView(APIView):
    """GET /api/admin/finance/store-config/ — list all configs."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        configs = (
            ZohoBooksStoreConfig.objects
            .select_related('store')
            .order_by('store__name')
        )
        return Response(
            ZohoBooksStoreConfigSerializer(configs, many=True).data,
            status=status.HTTP_200_OK,
        )


class AdminFinanceStoreConfigDetailAPIView(APIView):
    """
    GET  /api/admin/finance/store-config/<store_id>/ — get config
    PUT  /api/admin/finance/store-config/<store_id>/ — create or update config
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, store_id):
        store = get_object_or_404(Store, pk=store_id)
        try:
            config = store.zoho_books_config
        except ZohoBooksStoreConfig.DoesNotExist:
            return Response(
                {
                    'detail': 'No config found for this store.',
                    'store_id': store.pk,
                    'store_name': store.name,
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            ZohoBooksStoreConfigSerializer(config).data,
            status=status.HTTP_200_OK,
        )

    def put(self, request, store_id):
        store = get_object_or_404(Store, pk=store_id)
        try:
            config = store.zoho_books_config
            created = False
        except ZohoBooksStoreConfig.DoesNotExist:
            config = ZohoBooksStoreConfig(store=store)
            created = True

        # Partial update: only update fields present in request body
        ser = ZohoBooksStoreConfigWriteSerializer(
            config,
            data=request.data,
            partial=True,
        )
        ser.is_valid(raise_exception=True)
        config = ser.save()

        return Response(
            ZohoBooksStoreConfigSerializer(config).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Views — Journal Logs
# ---------------------------------------------------------------------------

class AdminFinanceJournalListAPIView(APIView):
    """GET /api/admin/finance/journals/ — paginated list with filters."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = (
            ZohoBooksJournalLog.objects
            .select_related('order', 'order__store')
            .order_by('-created_at')
        )

        # Filter by store_id
        store_id_raw = (request.query_params.get('store_id') or '').strip()
        if store_id_raw.isdigit():
            qs = qs.filter(order__store_id=int(store_id_raw))

        # Filter by order_id
        order_id_raw = (request.query_params.get('order_id') or '').strip()
        if order_id_raw.isdigit():
            qs = qs.filter(order_id=int(order_id_raw))

        # Filter by journal_type
        journal_type = (request.query_params.get('journal_type') or '').strip()
        if journal_type:
            qs = qs.filter(journal_type=journal_type)

        # Filter by date range
        date_from_raw = (request.query_params.get('date_from') or '').strip()
        date_to_raw = (request.query_params.get('date_to') or '').strip()

        if date_from_raw:
            date_from = _parse_order_list_date(date_from_raw)
            if date_from is None:
                return Response(
                    {'detail': 'Invalid date_from. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(journal_date__gte=date_from)

        if date_to_raw:
            date_to = _parse_order_list_date(date_to_raw)
            if date_to is None:
                return Response(
                    {'detail': 'Invalid date_to. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(journal_date__lte=date_to)

        # Filter failed only
        failed_only = (request.query_params.get('failed_only') or '').strip().lower()
        if failed_only in ('1', 'true', 'yes'):
            qs = qs.filter(zoho_journal_id='')

        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                'results': ZohoBooksJournalLogSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminFinanceJournalRetryAPIView(APIView):
    """POST /api/admin/finance/journals/<id>/retry/ — retry a failed journal."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request, pk):
        log = get_object_or_404(
            ZohoBooksJournalLog.objects.select_related('order', 'order__store'),
            pk=pk,
        )

        if (log.zoho_journal_id or '').strip():
            return Response(
                {'detail': 'Journal was already created successfully.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from shop.services.zoho_books_journals import _post_journal, _write_journal_log

        order = log.order
        debit_account_id = ''
        credit_account_id = ''

        try:
            config = order.store.zoho_books_config
            deposit_account_id = (config.deposit_account_id or '').strip()
            charge_account_id = (config.charge_account_id or '').strip()
            vat_account_id = (config.vat_account_id or '').strip()

            if log.journal_type == ZohoBooksJournalLog.JournalType.PAYMENT_CHARGE:
                debit_account_id = charge_account_id
                credit_account_id = deposit_account_id
            else:
                debit_account_id = vat_account_id
                credit_account_id = deposit_account_id
        except Exception as exc:
            return Response(
                {'detail': f'Could not resolve account IDs from store config: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not debit_account_id or not credit_account_id:
            return Response(
                {'detail': 'Required account IDs are empty in store config.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        journal_id = ''
        error = ''
        try:
            notes = (
                f'AoneGT order #{order.pk} - {log.journal_type} '
                f'({log.payment_method}) [retry]'
            )
            journal_id = _post_journal(
                order,
                debit_account_id=debit_account_id,
                credit_account_id=credit_account_id,
                amount=log.journal_amount,
                journal_date=log.journal_date,
                notes=notes,
            )
        except Exception as exc:
            error = str(exc)[:5000]
            logger.exception(
                'finance-retry: journal retry failed log=%s order=%s error=%s',
                log.pk, order.pk, exc,
            )

        # Update existing log record
        log.zoho_journal_id = journal_id
        log.error = error
        log.save(update_fields=['zoho_journal_id', 'error'])

        return Response(
            {
                'message': 'Retry succeeded.' if journal_id else 'Retry failed — see error.',
                'log': ZohoBooksJournalLogSerializer(log).data,
            },
            status=status.HTTP_200_OK,
        )
