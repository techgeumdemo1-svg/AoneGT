from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from shop.models import UserNotification
from shop.services.notifications import create_user_notification


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def welcome_member_notification(sender, instance, created, **kwargs):
    if not created:
        return
    create_user_notification(
        instance,
        UserNotification.Kind.MEMBER_OFFER,
        title='Welcome to AoneGt',
        body='Check out new-member offers and rewards in the app.',
        payload={'event': 'member_welcome', 'screen': 'offers'},
    )
