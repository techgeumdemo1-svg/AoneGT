from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from catalog.models import Product, Store
from catalog.services.product_images import backfill_product_image


class Command(BaseCommand):
    help = (
        'Backfill catalog Product.image_url from Zoho Commerce product detail. '
        'By default only products missing a CDN/HTTP image are processed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--store-id',
            type=int,
            default=None,
            help='Limit to products for this Store primary key.',
        )
        parser.add_argument(
            '--product-id',
            type=int,
            default=None,
            help='Backfill a single Product primary key.',
        )
        parser.add_argument(
            '--all-stores',
            action='store_true',
            help='Process products across all stores (use with --limit).',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=200,
            help='Maximum products to process (default: 200).',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-fetch images even when a usable image_url is already stored.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Call Zoho and print resolved URLs without saving.',
        )
        parser.add_argument(
            '--include-inactive',
            action='store_true',
            help='Include inactive products (default: active only).',
        )

    def handle(self, *args, **options):
        store_id = options['store_id']
        product_id = options['product_id']
        all_stores = options['all_stores']
        limit = max(1, int(options['limit'] or 200))
        force = bool(options['force'])
        dry_run = bool(options['dry_run'])

        if product_id is not None and (store_id is not None or all_stores):
            raise CommandError('Use --product-id alone, or --store-id / --all-stores.')

        if store_id is None and not all_stores and product_id is None:
            raise CommandError('Pass --product-id <id>, --store-id <id>, or --all-stores.')

        qs = Product.objects.select_related('store').order_by('pk')
        if not options.get('include_inactive'):
            qs = qs.filter(is_active=True)
        if product_id is not None:
            qs = qs.filter(pk=product_id)
        elif store_id is not None:
            store = Store.objects.filter(pk=store_id).first()
            if store is None:
                raise CommandError(f'Store id={store_id} not found.')
            qs = qs.filter(store_id=store_id)
        if not force:
            qs = qs.filter(
                Q(image_url='')
                | Q(image_url__isnull=True)
                | Q(image_url__icontains='/api/shop/zoho-products/')
            )
        qs = qs.exclude(zoho_product_id='').exclude(zoho_product_id__isnull=True)

        products = list(qs[:limit])
        if not products:
            self.stdout.write(self.style.WARNING('No products matched.'))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no database writes.'))

        updated = skipped = failed = dry = 0
        for product in products:
            status, message = backfill_product_image(
                product,
                dry_run=dry_run,
                force=force,
            )
            line = f'product={product.pk} store={product.store_id} zoho={product.zoho_product_id} → {message}'
            if status == 'updated':
                updated += 1
                self.stdout.write(self.style.SUCCESS(line))
            elif status == 'dry_run':
                dry += 1
                self.stdout.write(self.style.WARNING(f'[dry-run] {line}'))
            elif status == 'skipped':
                skipped += 1
                self.stdout.write(line)
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(line))

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. updated={updated} dry_run={dry} skipped={skipped} '
                f'failed={failed} processed={len(products)}',
            ),
        )
