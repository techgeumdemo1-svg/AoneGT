"""Background scheduler for periodic OTP cleanup."""

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


def should_start_otp_purge_scheduler():
    if not getattr(settings, 'OTP_PURGE_SCHEDULER_ENABLED', False):
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
    lock_path = settings.BASE_DIR / '.otp_purge_scheduler.lock'
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


def _run_otp_purge_job():
    from accounts.services.otp_cleanup import purge_otps

    include_used = getattr(settings, 'OTP_PURGE_INCLUDE_USED', False)
    try:
        counts = purge_otps(include_used=include_used, dry_run=False)
        total = sum(counts.values())
        if total:
            logger.info('otp-purge: deleted %s row(s) %s', total, counts)
    except Exception:
        logger.exception('otp-purge: scheduled job failed')


def start_otp_purge_scheduler():
    global _scheduler

    if not should_start_otp_purge_scheduler():
        return

    with _scheduler_lock:
        if _scheduler is not None:
            return

        if not _acquire_process_lock():
            logger.debug('otp-purge: scheduler not started (another process holds the lock)')
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError:
            logger.warning(
                'otp-purge: APScheduler not installed — run pip install APScheduler '
                'or use: python manage.py purge_expired_otps'
            )
            return

        interval_minutes = max(
            1,
            int(getattr(settings, 'OTP_PURGE_INTERVAL_MINUTES', 60)),
        )
        run_on_start = getattr(settings, 'OTP_PURGE_RUN_ON_START', True)

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _run_otp_purge_job,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id='purge_expired_otps',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _scheduler = scheduler

        if run_on_start:
            threading.Thread(target=_run_otp_purge_job, daemon=True).start()

        logger.info(
            'otp-purge: scheduler started (every %s min, include_used=%s)',
            interval_minutes,
            getattr(settings, 'OTP_PURGE_INCLUDE_USED', False),
        )
