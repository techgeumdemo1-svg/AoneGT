from django.core.management.base import BaseCommand, CommandError

from catalog.models import Store
from catalog.services.zoho_product_sync import (
    ZohoProductSyncError,
    iter_syncable_stores,
    sync_store_from_zoho,
)


class Command(BaseCommand):
    help = (
        'Fetch products from Zoho Commerce (GET /store/api/v1/products) and upsert local '
        'catalog rows by Zoho product/variant id. Requires ZohoCommerce.items.READ and '
        'ZOHO_ACCESS_TOKEN + ZOHO_COMMERCE_ORGANIZATION_ID (or per-store tokens/org).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--store-id',
            type=int,
            default=None,
            help='Sync only this local Store primary key.',
        )
        parser.add_argument(
            '--all-stores',
            action='store_true',
            help='Sync every active Store (each uses its own zoho_org_id when set).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse Zoho pages and count rows without writing the database.',
        )
        parser.add_argument(
            '--filter-by',
            type=str,
            default='Status.Active',
            help='Zoho filter_by (e.g. Status.Active, Status.All).',
        )
        parser.add_argument(
            '--per-page',
            type=int,
            default=100,
            help='Page size (10, 25, 50, 100, or 200 per Zoho).',
        )

    def handle(self, *args, **options):
        store_id = options['store_id']
        all_stores = options['all_stores']
        if store_id is None and not all_stores:
            raise CommandError('Pass --store-id <id> or --all-stores.')
        if store_id is not None and all_stores:
            raise CommandError('Use either --store-id or --all-stores, not both.')

        qs = iter_syncable_stores()
        if store_id is not None:
            store = Store.objects.filter(pk=store_id).first()
            if not store:
                raise CommandError(f'Store id={store_id} not found.')
            stores = [store]
        else:
            stores = list(qs)

        for store in stores:
            self.stdout.write(f'Syncing store pk={store.pk} ({store.name!r}) …')
            try:
                stats = sync_store_from_zoho(
                    store,
                    filter_by=options['filter_by'],
                    per_page=options['per_page'],
                    dry_run=options['dry_run'],
                )
            except (ZohoProductSyncError, OSError) as e:
                raise CommandError(str(e)) from e

            self.stdout.write(self.style.SUCCESS(f"  pages={stats['pages']} raw_products={stats['raw_products']} rows={stats['rows']}"))
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('  dry-run: no database writes'))
            else:
                self.stdout.write(
                    f"  created={stats['created']} updated={stats['updated']} unchanged={stats['unchanged']}",
                )
            for err in stats.get('errors') or []:
                self.stdout.write(self.style.ERROR(f'  error: {err}'))
