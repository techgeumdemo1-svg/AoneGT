"""One-off: align shop_order Zoho Books columns with nullable/empty defaults."""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        'Fix shop_order Zoho Books columns when DB has NOT NULL but Django model '
        'does not set them on insert (allows checkout until Books code is deployed).'
    )

    def handle(self, *args, **options):
        statements = [
            "ALTER TABLE shop_order ALTER COLUMN zoho_books_invoice_id DROP NOT NULL",
            "ALTER TABLE shop_order ALTER COLUMN zoho_books_invoice_id SET DEFAULT ''",
            "ALTER TABLE shop_order ALTER COLUMN zoho_books_invoice_number DROP NOT NULL",
            "ALTER TABLE shop_order ALTER COLUMN zoho_books_invoice_number SET DEFAULT ''",
            "ALTER TABLE shop_order ALTER COLUMN zoho_books_invoice_error DROP NOT NULL",
            "ALTER TABLE shop_order ALTER COLUMN zoho_books_invoice_error SET DEFAULT ''",
        ]
        with connection.cursor() as cursor:
            for sql in statements:
                try:
                    cursor.execute(sql)
                    self.stdout.write(self.style.SUCCESS(f'OK: {sql}'))
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f'Skip ({exc}): {sql}'))
        self.stdout.write(self.style.SUCCESS('Done. Restart runserver and retry checkout.'))
