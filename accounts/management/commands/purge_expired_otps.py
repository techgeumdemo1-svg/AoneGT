from django.core.management.base import BaseCommand

from accounts.services.otp_cleanup import purge_otps


class Command(BaseCommand):
    help = (
        'Delete expired OTP rows from all accounts OTP tables. '
        'Also runs automatically when OTP_PURGE_SCHEDULER_ENABLED=True '
        '(default) and the Django app is running.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-used',
            action='store_true',
            help='Also delete OTP rows already marked as used (even if not yet expired).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report how many rows would be deleted without deleting.',
        )

    def handle(self, *args, **options):
        include_used = options['include_used']
        dry_run = options['dry_run']

        counts = purge_otps(include_used=include_used, dry_run=dry_run)
        total = sum(counts.values())

        verb = 'Would delete' if dry_run else 'Deleted'
        for name, count in counts.items():
            if count:
                self.stdout.write(f'  {name}: {count}')

        if total == 0:
            self.stdout.write(self.style.SUCCESS('No OTP rows to purge.'))
            return

        self.stdout.write(
            self.style.SUCCESS(f'{verb} {total} OTP row(s) total.')
        )
