"""
Management command: cleanup_stale_orders

Finds payment_gateway / pay_by_link orders that are still PENDING
after 2 hours and reconciles or cancels them via Geidea.

This is the manual/cron equivalent of the APScheduler
_run_stale_order_cleanup job in shop/scheduler.py.
Business logic is unchanged — calls the same
reconcile_or_cancel_stale_order() function.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from shop.models import Order
from shop.services.geidea import reconcile_or_cancel_stale_order


class Command(BaseCommand):
    help = (
        'Find PAYMENT_GATEWAY and PAY_BY_LINK orders still PENDING after 2 hours '
        'and reconcile with Geidea or cancel them. '
        'Equivalent to the APScheduler _run_stale_order_cleanup job.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List stale orders without reconciling or cancelling.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
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
        self.stdout.write(f'Stale order cleanup — found {count} stale order(s) (PENDING > 2 hours).')

        if count == 0:
            self.stdout.write(self.style.SUCCESS('No stale orders to process.'))
            return

        if dry_run:
            for order in stale_orders:
                self.stdout.write(
                    f'  [DRY RUN] order={order.pk} '
                    f'method={order.payment_method} '
                    f'created={order.created_at}'
                )
            self.stdout.write(self.style.WARNING('Dry run — no changes made.'))
            return

        ok = 0
        failed = 0
        for order in stale_orders:
            try:
                reconcile_or_cancel_stale_order(order)
                ok += 1
            except Exception as exc:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f'  order={order.pk} failed: {exc}')
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. processed={ok} failed={failed} total={count}'
            )
        )
