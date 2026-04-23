from django.urls import path
from .views import create_superuser

urlpatterns = [
    path('create-superuser/', create_superuser),
]