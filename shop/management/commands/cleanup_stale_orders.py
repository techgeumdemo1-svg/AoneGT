"""
Management command: cleanup_stale_orders

Finds payment_gateway / pay-by-link orders that are still PENDING after 2 hours
and reconciles or cancels them via the existing Geidea service.

This is the cron-compatible counterpart to the APScheduler job
`_run_stale_order_cleanup` defined in shop/scheduler.py.
The business logic (cutoff window, query, reconciliation call) is
intentionally kept identical to that scheduler job.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from shop.models import Order
from shop.services.geidea import reconcile_or_cancel_stale_order

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Reconcile or cancel payment-gateway orders that have been PENDING '
        'for more than 2 hours. Safe to run repeatedly; uses the same logic '
        'as the APScheduler background job.'
    )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=2)

        stale_orders = Order.objects.filter(
            payment_method__in=[
                Order.PaymentMethod.PAYMENT_GATEWAY,
                Order.PaymentMethod.PAY_BY_LINK,
            ],
            payment_status=Order.PaymentStatus.PENDING,
            created_at__lt=cutoff,
        )

        count = stale_orders.count()
        self.stdout.write(f'Stale order cleanup — found {count} stale order(s).')
        logger.info('Stale order cleanup — found %d stale orders.', count)

        if count == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to clean up.'))
            return

        for order in stale_orders:
            try:
                reconcile_or_cancel_stale_order(order)
            except Exception as exc:  # noqa: BLE001
                logger.exception('Failed to reconcile/cancel order %s', order.pk)
                self.stdout.write(
                    self.style.WARNING(f'  Order {order.pk}: error — {exc}')
                )

        self.stdout.write(self.style.SUCCESS(f'Finished processing {count} stale order(s).'))
