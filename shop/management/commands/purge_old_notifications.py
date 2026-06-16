from django.conf import settings
from django.core.management.base import BaseCommand

from shop.services.notification_cleanup import purge_old_notifications


class Command(BaseCommand):
    help = (
        'Delete in-app UserNotification rows older than NOTIFICATION_RETENTION_DAYS '
        '(default 30). Also runs on a daily schedule when the shop scheduler is active.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            help='Override NOTIFICATION_RETENTION_DAYS for this run.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report how many rows would be deleted without deleting.',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days is None:
            days = int(getattr(settings, 'NOTIFICATION_RETENTION_DAYS', 30) or 0)

        dry_run = options['dry_run']
        count = purge_old_notifications(retention_days=days, dry_run=dry_run)

        if days <= 0:
            self.stdout.write(self.style.WARNING('Retention is 0 — no notifications purged.'))
            return

        verb = 'Would delete' if dry_run else 'Deleted'
        if count == 0:
            self.stdout.write(self.style.SUCCESS(f'No notifications older than {days} day(s).'))
            return

        self.stdout.write(
            self.style.SUCCESS(f'{verb} {count} notification row(s) older than {days} day(s).')
        )
