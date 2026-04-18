from django.contrib.auth import authenticate
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken


def authenticate_superuser(validated_data: dict) -> dict:
    """
    Business logic for authenticating a superuser.
    Checks credentials, verifies superuser status, and returns JWT tokens.
    """
    email = validated_data.get('email', '').lower()
    password = validated_data.get('password')

    # Authenticate using Django's built-in authentication mechanism.
    # Note: AoneGT uses 'email' as the username field for authentication.
    user = authenticate(username=email, password=password)

    if not user:
        raise AuthenticationFailed('Invalid email or password.')

    if not user.is_active:
        raise AuthenticationFailed('This account is inactive.')

    # Strict logical check: ensure the user is a superuser
    if not user.is_superuser:
        raise AuthenticationFailed('Access denied: User is not a superuser.')

    # Generate tokens using SimpleJWT
    refresh = RefreshToken.for_user(user)

    return {
        'user': {
            'id': user.id,
            'email': user.email,
            'is_superuser': user.is_superuser,
        },
        'tokens': {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
    }