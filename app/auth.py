"""Autenticación por contraseña única.

App de un solo usuario: no hay tabla de usuarios. Una contraseña en el entorno
(APP_PASSWORD) y una cookie de sesión firmada. Sin APP_PASSWORD la app solo
acepta conexiones locales — así nunca queda expuesta sin protección por olvido.
"""
import hmac
import os
import secrets
from ipaddress import ip_address

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "b2x_session"
MAX_AGE = 60 * 60 * 12  # 12 h

# Rutas accesibles sin sesión.
PUBLIC_PATHS = {"/login", "/api/login", "/static/style.css", "/favicon.ico"}


def _secret() -> str:
    """Clave para firmar la cookie. Si no se define, se genera una por arranque
    (efecto: al reiniciar, se cierran las sesiones)."""
    s = os.getenv("SECRET_KEY")
    if not s:
        s = getattr(_secret, "_ephemeral", None)
        if not s:
            s = secrets.token_urlsafe(32)
            _secret._ephemeral = s
    return s


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="b2x-session")


def password() -> str | None:
    pw = os.getenv("APP_PASSWORD") or ""
    return pw or None


def auth_required() -> bool:
    """Hay contraseña configurada => se exige login."""
    return password() is not None


def check_password(candidate: str) -> bool:
    pw = password()
    if not pw:
        return False
    # compare_digest: evita filtrar la contraseña por diferencias de tiempo.
    return hmac.compare_digest(candidate.encode(), pw.encode())


def issue_cookie(response) -> None:
    token = _serializer().dumps({"ok": True})
    secure = os.getenv("COOKIE_SECURE", "true").lower() != "false"
    response.set_cookie(
        COOKIE_NAME, token, max_age=MAX_AGE, httponly=True,
        samesite="lax", secure=secure, path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def valid_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


async def auth_middleware(request: Request, call_next):
    """Bloquea todo salvo las rutas públicas.

    Sin APP_PASSWORD definida, solo se permite acceso desde la propia máquina:
    exponer la app a internet sin contraseña dejaría las API keys al alcance
    de cualquiera.
    """
    path = request.url.path

    if not auth_required():
        if _is_local(request):
            return await call_next(request)
        return JSONResponse(
            {"detail": "APP_PASSWORD no está configurada en el servidor. "
                       "La app solo acepta conexiones locales hasta que se defina."},
            status_code=503,
        )

    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    if valid_session(request):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sesión expirada. Volvé a entrar."}, status_code=401)
    return RedirectResponse("/login", status_code=303)
