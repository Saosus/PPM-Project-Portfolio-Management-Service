from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

User = get_user_model()


class SignedTokenAuthentication(BaseAuthentication):
    """Stateless signed bearer token; no extra database table is required."""
    keyword = b"bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None
        if header[0].lower() != self.keyword:
            return None
        if len(header) != 2:
            raise AuthenticationFailed("Некорректный заголовок авторизации")
        try:
            payload = signing.loads(
                header[1].decode("ascii"),
                salt="ppm-api-token-v1",
                max_age=settings.SIGNED_TOKEN_MAX_AGE_SECONDS,
            )
            user = User.objects.get(pk=payload["user_id"], is_active=True)
        except (UnicodeDecodeError, signing.BadSignature, signing.SignatureExpired, KeyError, User.DoesNotExist, TypeError):
            raise AuthenticationFailed("Токен недействителен или истёк")
        return user, None

    def authenticate_header(self, request):
        return "Bearer"
