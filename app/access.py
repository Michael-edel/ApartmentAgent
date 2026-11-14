"""Optional HTTP authentication for a single ApartmentAgent installation."""
from base64 import b64decode
from binascii import Error as Base64Error
from secrets import compare_digest
from urllib.parse import urlsplit

from fastapi import Request, Response


def require_basic_auth(app, username: str, password: str) -> None:
    if not username or ':' in username or not password:
        raise ValueError('Укажите APP_AUTH_USERNAME и APP_AUTH_PASSWORD для защиты приложения')
    expected_user = username.encode()
    expected_password = password.encode()

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        scheme, _, token = request.headers.get('Authorization', '').partition(' ')
        if scheme.lower() == 'basic' and len(token) <= 8192:
            try:
                user, separator, supplied = b64decode(token, validate=True).partition(b':')
            except (ValueError, Base64Error):
                pass
            else:
                valid_user = compare_digest(user, expected_user)
                valid_password = compare_digest(supplied, expected_password)
                if separator and valid_user and valid_password:
                    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                        origin = request.headers.get('Origin')
                        if request.headers.get('Sec-Fetch-Site') == 'cross-site' or (
                            origin and urlsplit(origin).netloc != request.url.netloc
                        ):
                            return Response(status_code=403)
                    return await call_next(request)
        return Response('Требуется вход в ApartmentAgent', status_code=401,
                        headers={'WWW-Authenticate': 'Basic realm="ApartmentAgent", charset="UTF-8"',
                                 'Cache-Control': 'no-store'})
