from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiToken, Status


class PoCBearerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth = get_authorization_header(request).decode('utf-8')
        if not auth:
            return None

        parts = auth.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise AuthenticationFailed('Invalid authorization header.')

        token = ApiToken.objects.select_related('user', 'user__company', 'user__site').filter(key=parts[1]).first()
        if token is None or token.is_expired:
            raise AuthenticationFailed('Invalid or expired token.')
        if token.user.status != Status.ACTIVE:
            raise AuthenticationFailed('Inactive user.')

        request.poc_user = token.user
        return (token.user, token)
