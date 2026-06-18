from django.conf import settings
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from shop.models import Order, OrderReturn, UserNotification
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


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def notify_admin_dashboard_customer_registered(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.is_staff or instance.is_superuser:
        return
    from admin_dashboard.realtime import broadcast_customer_registered

    broadcast_customer_registered(instance)


@receiver(pre_save, sender=Order)
def _cache_order_previous_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._admin_realtime_prev = None
        return
    try:
        previous = Order.objects.only(
            'status',
            'customer_tracking_stage',
            'payment_status',
        ).get(pk=instance.pk)
        instance._admin_realtime_prev = {
            'status': previous.status,
            'customer_tracking_stage': previous.customer_tracking_stage or '',
            'payment_status': previous.payment_status or '',
        }
    except Order.DoesNotExist:
        instance._admin_realtime_prev = None


@receiver(post_save, sender=Order)
def notify_admin_dashboard_order_events(sender, instance, created, **kwargs):
    from admin_dashboard.realtime import (
        broadcast_order_cancelled,
        broadcast_order_created,
        broadcast_order_paid,
        broadcast_order_status_updated,
        broadcast_order_tracking_updated,
    )

    if created:
        broadcast_order_created(instance)
        if instance.payment_status == Order.PaymentStatus.PAID:
            broadcast_order_paid(instance)
        return

    previous = getattr(instance, '_admin_realtime_prev', None)
    if not previous:
        return

    previous_status = previous['status']
    previous_tracking = previous['customer_tracking_stage']
    previous_payment = previous.get('payment_status', '')
    current_status = instance.status
    current_tracking = instance.customer_tracking_stage or ''
    current_payment = instance.payment_status or ''

    if current_status == Order.Status.CANCELLED and previous_status != Order.Status.CANCELLED:
        broadcast_order_cancelled(instance)
        return

    if current_status != previous_status:
        broadcast_order_status_updated(instance, previous_status=previous_status)

    if current_tracking != previous_tracking:
        broadcast_order_tracking_updated(
            instance,
            previous_tracking_stage=previous_tracking,
        )

    if (
        current_payment == Order.PaymentStatus.PAID
        and previous_payment != Order.PaymentStatus.PAID
    ):
        broadcast_order_paid(instance)


@receiver(pre_save, sender=OrderReturn)
def _cache_return_previous_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._admin_realtime_prev = None
        return
    try:
        previous = OrderReturn.objects.only('status').get(pk=instance.pk)
        instance._admin_realtime_prev = {'status': previous.status}
    except OrderReturn.DoesNotExist:
        instance._admin_realtime_prev = None


@receiver(post_save, sender=OrderReturn)
def notify_admin_dashboard_return_events(sender, instance, created, **kwargs):
    from admin_dashboard.realtime import (
        broadcast_return_created,
        broadcast_return_status_updated,
    )

    ret = OrderReturn.objects.select_related('order').filter(pk=instance.pk).first()
    if ret is None:
        return

    if created:
        broadcast_return_created(ret)
        return

    previous = getattr(instance, '_admin_realtime_prev', None)
    if not previous:
        return

    previous_status = previous['status']
    if previous_status == ret.status:
        return

    broadcast_return_status_updated(ret, previous_status=previous_status)
