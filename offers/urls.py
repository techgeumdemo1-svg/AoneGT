from django.urls import path
from .views import SuperuserLoginView

app_name = 'offers'

urlpatterns = [
    path('superuser-login/', SuperuserLoginView.as_view(), name='superuser-login'),
]