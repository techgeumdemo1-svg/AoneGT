"""Retry Zoho Commerce sales order creation for orders missing zoho_salesorder_id."""

from django.core.management.base import BaseCommand
from django.db.models import Q

from shop.models import Order
from shop.services.zoho_sales_order import (
    maybe_create_zoho_sales_order_for_order,
    zoho_commerce_sales_order_enabled,
)


class Command(BaseCommand):
    help = (
        'Retry Zoho Commerce sales order sync for orders that have no '
        'zoho_salesorder_id (default: pending_zoho_sync and sync_failed).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--order-id',
            type=int,
            help='Retry a single order by primary key.',
        )
        parser.add_argument(
            '--store-id',
            type=int,
            help='Limit to orders for this store.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Maximum number of orders to process (default: 50).',
        )
        parser.add_argument(
            '--include-synced',
            action='store_true',
            help='Also retry orders already marked synced (still missing sales order id).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List matching orders without calling Zoho.',
        )

    def handle(self, *args, **options):
        if not zoho_commerce_sales_order_enabled():
            self.stdout.write(
                self.style.WARNING(
                    'ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED is False — '
                    'Commerce sales order sync is disabled.',
                ),
            )
            return

        order_id = options.get('order_id')
        store_id = options.get('store_id')
        limit = max(1, int(options.get('limit') or 50))
        dry_run = bool(options.get('dry_run'))
        include_synced = bool(options.get('include_synced'))

        statuses = [Order.Status.PENDING_ZOHO_SYNC, Order.Status.SYNC_FAILED]
        if include_synced:
            statuses.append(Order.Status.SYNCED)

        qs = (
            Order.objects.filter(status__in=statuses)
            .filter(Q(zoho_salesorder_id='') | Q(zoho_salesorder_id__isnull=True))
            .exclude(status=Order.Status.CANCELLED)
            .select_related('store')
            .order_by('pk')
        )
        if order_id:
            qs = qs.filter(pk=order_id)
        if store_id:
            qs = qs.filter(store_id=store_id)

        orders = list(qs[:limit])
        if not orders:
            self.stdout.write(self.style.WARNING('No orders matched.'))
            return

        self.stdout.write(f'Found {len(orders)} order(s) to retry.')
        if dry_run:
            for order in orders:
                self.stdout.write(
                    f'  order={order.pk} store={order.store_id} '
                    f'status={order.status} error={(order.zoho_sync_error or "")[:120]}'
                )
            self.stdout.write(self.style.SUCCESS('Dry run complete.'))
            return

        ok = 0
        failed = 0
        for order in orders:
            maybe_create_zoho_sales_order_for_order(order.pk)
            order.refresh_from_db(fields=['zoho_salesorder_id', 'zoho_sync_error', 'updated_at'])
            if (order.zoho_salesorder_id or '').strip():
                ok += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'order={order.pk} synced salesorder_id={order.zoho_salesorder_id}',
                    ),
                )
            else:
                failed += 1
                err = (order.zoho_sync_error or 'Unknown error')[:500]
                self.stdout.write(self.style.ERROR(f'order={order.pk} failed: {err}'))

        self.stdout.write(
            self.style.SUCCESS(f'Done. success={ok} failed={failed} total={len(orders)}'),
        )
