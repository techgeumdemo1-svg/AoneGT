from django.core.management.base import BaseCommand

from shop.services.loyalty_coupons import purge_stale_loyalty_coupons


class Command(BaseCommand):
    help = 'Delete expired unused loyalty coupons and any used coupons still in the database.'

    def handle(self, *args, **options):
        deleted = purge_stale_loyalty_coupons()
        self.stdout.write(self.style.SUCCESS(f'Purged {deleted} loyalty coupon row(s).'))
