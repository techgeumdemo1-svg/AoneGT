from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'
    verbose_name = 'Shop'

    def ready(self):
        import shop.signals  # noqa: F401

        from shop.scheduler import start_geidea_cleanup_scheduler

        start_geidea_cleanup_scheduler()
