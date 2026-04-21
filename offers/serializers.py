from rest_framework import serializers


class SuperuserLoginSerializer(serializers.Serializer):
    """
    Handles basic input validation for superuser login credentials.
    Actual authentication is deferred to the service layer.
    """
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, style={'input_type': 'password'}
    )