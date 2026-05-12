import logging

from django.core.management.base import BaseCommand

from catalog.models import Store

from offer.services import sync_zoho_coupons_for_store

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Poll Zoho Commerce coupons into local offer tables.'

    def handle(self, *args, **options):
        stores = Store.objects.filter(is_active=True).exclude(zoho_org_id='').order_by('id')
        total_synced = 0
        total_expired_deleted = 0
        total_missing_deleted = 0

        for store in stores:
            try:
                result = sync_zoho_coupons_for_store(store)
                total_synced += int(result.get('synced') or 0)
                total_expired_deleted += int(result.get('expired_deleted') or 0)
                total_missing_deleted += int(result.get('missing_deleted') or 0)
                self.stdout.write(self.style.SUCCESS(f'Synced coupons for store {store.pk} ({store.name}): {result}'))
            except Exception as exc:
                logger.exception('Coupon sync failed for store %s', store.pk)
                self.stdout.write(self.style.WARNING(f'Skipped store {store.pk} ({store.name}) due to error: {exc}'))

        self.stdout.write(self.style.SUCCESS(
            f'Finished coupon sync. synced={total_synced}, expired_deleted={total_expired_deleted}, missing_deleted={total_missing_deleted}'
        ))
