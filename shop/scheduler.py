"""Background scheduler for stale payment gateway order cleanup."""

import logging
import os
import sys
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()
_process_lock_file = None


def _is_management_command():
    return os.path.basename(sys.argv[0]) in ('manage.py', 'django-admin')


def should_start_geidea_cleanup_scheduler():
    """Only start in runserver (reloader child) or gunicorn workers."""
    if not getattr(settings, 'GEIDEA_STALE_CLEANUP_ENABLED', True):
        return False

    if _is_management_command():
        if len(sys.argv) < 2 or sys.argv[1] != 'runserver':
            return False
        # runserver autoreload: only the reloader child should start the scheduler.
        return os.environ.get('RUN_MAIN') == 'true'

    return True


def _acquire_process_lock():
    """Ensure only one worker/process runs the scheduler (gunicorn, runserver)."""
    global _process_lock_file
    lock_path = settings.BASE_DIR / '.geidea_cleanup_scheduler.lock'
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, 'w')
        if sys.platform == 'win32':
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(str(os.getpid()))
        handle.flush()
        _process_lock_file = handle
        return True
    except (OSError, IOError):
        if 'handle' in locals():
            handle.close()
        return False


def _run_stale_order_cleanup():
    """
    Finds payment_gateway orders that are still PENDING after 2 hours
    and reconciles or cancels them.
    """
    from datetime import timedelta

    from django.utils import timezone

    from shop.models import Order
    from shop.services.geidea import reconcile_or_cancel_stale_order

    try:
        cutoff = timezone.now() - timedelta(hours=2)
        stale_orders = Order.objects.filter(
            payment_method=Order.PaymentMethod.PAYMENT_GATEWAY,
            payment_status=Order.PaymentStatus.PENDING,
            created_at__lt=cutoff,
        )

        count = stale_orders.count()
        logger.info("Stale order cleanup — found %d stale orders.", count)

        for order in stale_orders:
            reconcile_or_cancel_stale_order(order)

    except Exception:
        logger.exception('geidea-cleanup: scheduled job failed')


def start_geidea_cleanup_scheduler():
    global _scheduler

    if not should_start_geidea_cleanup_scheduler():
        return

    with _scheduler_lock:
        if _scheduler is not None:
            return

        if not _acquire_process_lock():
            logger.debug('geidea-cleanup: scheduler not started (another process holds the lock)')
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError:
            logger.warning(
                'geidea-cleanup: APScheduler not installed — run pip install APScheduler '
                'to enable automatic stale order cleanup.'
            )
            return

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _run_stale_order_cleanup,
            trigger=IntervalTrigger(hours=1),
            id='cleanup_stale_payment_gateway_orders',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _scheduler = scheduler

        logger.info('geidea-cleanup: scheduler started (every 60 min)')
