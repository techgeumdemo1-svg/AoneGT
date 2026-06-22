from rest_framework.throttling import SimpleRateThrottle


class AdminForgotPasswordThrottle(SimpleRateThrottle):
    scope = 'admin_forgot_password'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        email = str(request.data.get('email', '') or '').strip().lower()
        if not ident and not email:
            return None
        token = f'{ident}:{email or "empty-email"}'
        return self.cache_format % {'scope': self.scope, 'ident': token}


class AdminLoginOTPThrottle(SimpleRateThrottle):
    scope = 'admin_login_otp'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        email = str(request.data.get('email', '') or '').strip().lower()
        if not ident and not email:
            return None
        token = f'{ident}:{email or "empty-email"}'
        return self.cache_format % {'scope': self.scope, 'ident': token}
