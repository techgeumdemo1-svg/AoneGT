from django.urls import path
from .views import (
    RegisterAPIView,
    CheckEmailAPIView,
    CheckZohoContactAPIView,
    RequestRegistrationOTPAPIView,
    LoginAPIView,
    ForgotPasswordAPIView,
    ResetPasswordAPIView,
    ProfileAPIView,
)

urlpatterns = [
    path('register/', RegisterAPIView.as_view(), name='register'),
    path('check-email/', CheckEmailAPIView.as_view(), name='check-email'),
    path('check-zoho-contact/', CheckZohoContactAPIView.as_view(), name='check-zoho-contact'),
    path(
        'request-registration-code/',
        RequestRegistrationOTPAPIView.as_view(),
        name='request-registration-code',
    ),
    path('login/', LoginAPIView.as_view(), name='login'),
    path('forgot-password/', ForgotPasswordAPIView.as_view(), name='forgot-password'),
    path('reset-password/', ResetPasswordAPIView.as_view(), name='reset-password'),
    path('profile/', ProfileAPIView.as_view(), name='profile'),
]
