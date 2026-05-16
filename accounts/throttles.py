from rest_framework.throttling import SimpleRateThrottle


class ForgotPasswordRateThrottle(SimpleRateThrottle):
    scope = 'forgot_password'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        email = str(request.data.get('email', '') or '').strip().lower()
        if not ident and not email:
            return None
        token = f'{ident}:{email or "empty-email"}'
        return self.cache_format % {'scope': self.scope, 'ident': token}


class DeactivateAccountOTPThrottle(SimpleRateThrottle):
    scope = 'deactivate_account_otp'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}
        return None


class DeleteAccountOTPThrottle(SimpleRateThrottle):
    scope = 'delete_account_otp'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}
        return None


class ReactivateAccountOTPThrottle(SimpleRateThrottle):
    scope = 'reactivate_account_otp'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        email = str(request.data.get('email', '') or '').strip().lower()
        if not ident and not email:
            return None
        token = f'{ident}:{email or "empty-email"}'
        return self.cache_format % {'scope': self.scope, 'ident': token}


class ChangePasswordOTPThrottle(SimpleRateThrottle):
    scope = 'change_password_otp'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}
        return None
