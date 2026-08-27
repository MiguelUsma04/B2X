"""Autenticación: cuenta de Google del equipo, o contraseña única.

Hay dos formas de entrar y conviven:

- Google (recomendada): cada uno entra con su cuenta del dominio. La app nunca
  ve la contraseña —eso lo maneja Google— y queda registrado quién entró.
- Contraseña única (APP_PASSWORD): la de siempre, útil como respaldo.

Sin ninguna de las dos configurada, la app solo acepta conexiones locales, así
nunca queda expuesta sin protección por un olvido.
"""
import base64
import hmac
import json
import os
import secrets
from ipaddress import ip_address

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "b2x_session"
ESTADO_COOKIE = "b2x_oauth"
MAX_AGE = 60 * 60 * 12  # 12 h
ESTADO_MAX_AGE = 60 * 10   # el ida y vuelta con Google no debería tardar más

# Rutas accesibles sin sesión.
PUBLIC_PATHS = {"/login", "/api/login", "/static/style.css", "/favicon.ico",
                "/auth/google/start", "/auth/google/callback"}

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
EMISORES = {"accounts.google.com", "https://accounts.google.com"}


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


# ------------------------------------------------------------------ Google
def google_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def dominio_permitido() -> str:
    return (os.getenv("GOOGLE_ALLOWED_DOMAIN") or "").strip().lower()


def emails_extra() -> set:
    """Invitados sueltos, fuera del dominio. Rara vez hace falta."""
    crudo = os.getenv("GOOGLE_ALLOWED_EMAILS") or ""
    return {e.strip().lower() for e in crudo.split(",") if e.strip()}


def email_permitido(email: str, hd: str | None) -> bool:
    """¿Esta cuenta puede entrar?

    Se mira el dominio real del email y el claim 'hd' del token. El parámetro
    hd que se le manda a Google es solo una sugerencia de pantalla: no filtra
    nada por sí solo, y creerle sería dejar entrar a cualquier cuenta.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    if email in emails_extra():
        return True
    dominio = dominio_permitido()
    if not dominio:
        return False        # sin dominio configurado no se abre a cualquiera
    return email.split("@")[1] == dominio and (hd or "").lower() in ("", dominio)


def auth_required() -> bool:
    """Hay alguna forma de login configurada => se exige entrar."""
    return password() is not None or google_configured()


def check_password(candidate: str) -> bool:
    pw = password()
    if not pw:
        return False
    # compare_digest: evita filtrar la contraseña por diferencias de tiempo.
    return hmac.compare_digest(candidate.encode(), pw.encode())


def redirect_uri(request: Request) -> str:
    """A dónde vuelve Google. Tiene que coincidir EXACTO con lo registrado.

    Detrás de un proxy el esquema que ve la app suele ser http aunque el
    usuario haya entrado por https, y ahí Google rechaza el intento: por eso
    se puede fijar a mano.
    """
    fijo = (os.getenv("OAUTH_REDIRECT_URL") or "").strip()
    return fijo or str(request.url_for("google_callback"))


def url_de_google(request: Request, estado: str, nonce: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": estado,
        "nonce": nonce,
        "prompt": "select_account",
    }
    dominio = dominio_permitido()
    if dominio:
        # Sugerencia para que la pantalla de Google muestre las cuentas del
        # dominio. No es un control de acceso: el filtro real es del lado
        # nuestro, al validar el token.
        params["hd"] = dominio
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


def guardar_estado(response, estado: str, nonce: str) -> None:
    token = _serializer().dumps({"estado": estado, "nonce": nonce})
    secure = os.getenv("COOKIE_SECURE", "true").lower() != "false"
    response.set_cookie(ESTADO_COOKIE, token, max_age=ESTADO_MAX_AGE, httponly=True,
                        samesite="lax", secure=secure, path="/")


def leer_estado(request: Request) -> dict | None:
    token = request.cookies.get(ESTADO_COOKIE)
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=ESTADO_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def payload_del_id_token(id_token: str) -> dict:
    """Decodifica el cuerpo del ID token.

    No se verifica la firma a propósito: el token no llega por el navegador
    sino de una llamada nuestra al endpoint de Google sobre TLS, y Google
    documenta que en ese caso alcanza. Igual se validan todos los claims que
    importan, que es lo que decide quién entra.
    """
    partes = id_token.split(".")
    if len(partes) != 3:
        raise ValueError("El ID token no tiene la forma esperada.")
    cuerpo = partes[1] + "=" * (-len(partes[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(cuerpo))


def validar_id_token(datos: dict, nonce: str) -> str:
    """Devuelve el email si el token es válido; si no, explica por qué no.

    Levanta PermissionError con un mensaje para mostrar en pantalla.
    """
    import time
    if datos.get("aud") != os.getenv("GOOGLE_CLIENT_ID"):
        raise PermissionError("El token no fue emitido para esta app.")
    if datos.get("iss") not in EMISORES:
        raise PermissionError("El token no viene de Google.")
    if float(datos.get("exp", 0)) < time.time():
        raise PermissionError("El token venció. Probá de nuevo.")
    if nonce and datos.get("nonce") != nonce:
        raise PermissionError("El token no corresponde a este intento de ingreso.")
    if not datos.get("email_verified"):
        raise PermissionError("Esa cuenta de Google no tiene el email verificado.")

    email = (datos.get("email") or "").lower()
    if not email_permitido(email, datos.get("hd")):
        dominio = dominio_permitido() or "el dominio autorizado"
        raise PermissionError(f"{email or 'Esa cuenta'} no pertenece a {dominio}.")
    return email


def issue_cookie(response, email: str | None = None) -> None:
    token = _serializer().dumps({"ok": True, "email": email})
    secure = os.getenv("COOKIE_SECURE", "true").lower() != "false"
    response.set_cookie(
        COOKIE_NAME, token, max_age=MAX_AGE, httponly=True,
        samesite="lax", secure=secure, path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(ESTADO_COOKIE, path="/")


def sesion(request: Request) -> dict | None:
    """Los datos de la sesión, o None si no hay una válida."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        datos = _serializer().loads(token, max_age=MAX_AGE)
        return datos if isinstance(datos, dict) else {"ok": True}
    except (BadSignature, SignatureExpired):
        return None


def valid_session(request: Request) -> bool:
    return sesion(request) is not None


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
            {"detail": "No hay login configurado en el servidor (ni cuenta de "
                       "Google ni APP_PASSWORD). La app solo acepta conexiones "
                       "locales hasta que se defina alguno."},
            status_code=503,
        )

    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    if valid_session(request):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sesión expirada. Volvé a entrar."}, status_code=401)
    return RedirectResponse("/login", status_code=303)
