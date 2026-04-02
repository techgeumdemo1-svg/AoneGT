import os
from celery import Celery
from celery.schedules import schedule

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aonegt.settings')

# Create Celery app
app = Celery('aonegt')

# Load configuration from Django settings (namespace='CELERY')
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all registered Django apps
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
