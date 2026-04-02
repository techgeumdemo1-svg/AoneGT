from celery import shared_task
from django.utils import timezone
from .models import PasswordResetOTP
import logging

logger = logging.getLogger(__name__)


@shared_task
def delete_expired_otps():
    """
    Celery task to delete expired OTPs from the database.
    Runs every 2 minutes as per schedule.
    """
    try:
        # Get current time
        now = timezone.now()
        
        # Delete all expired and unused OTPs
        deleted_count, _ = PasswordResetOTP.objects.filter(
            expires_at__lt=now,
            is_used=False
        ).delete()
        
        logger.info(f"Successfully deleted {deleted_count} expired OTPs at {now}")
        return {
            'status': 'success',
            'deleted_count': deleted_count,
            'timestamp': str(now)
        }
    except Exception as e:
        logger.error(f"Error deleting expired OTPs: {str(e)}")
        return {
            'status': 'error',
            'error': str(e)
        }
