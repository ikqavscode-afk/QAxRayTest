"""Защита от подделки межсайтовых запросов (CSRF) для POST-запросов (ADR-0013).

Токен хранится в подписанной cookie сессии (HttpOnly, SameSite=Lax) и вставляется скрытым полем в каждую
форму; при отправке сравнивается постоянным по времени сравнением. Дополнительно проверяется заголовок
Origin: запрос с чужого источника отклоняется, даже если токен верный. Ключ подписи создаётся при запуске
процесса: после перезапуска старые страницы просто просят открыть форму заново."""

import hmac
import secrets
from urllib.parse import urlsplit

from flask import Flask, render_template, request, session
from werkzeug.wrappers import Response

SESSION_KEY = "csrf_token"
FIELD = "csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_TOKEN_LENGTH = 128


def csrf_token() -> str:
    """Токен текущей сессии; создаётся при первом обращении."""
    token = session.get(SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def _origin_is_foreign() -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return False  # заголовок есть не у всех запросов; тогда решает токен
    parts = urlsplit(origin)
    return parts.netloc != request.host or parts.scheme not in {"http", "https"}


def _token_is_valid() -> bool:
    expected = session.get(SESSION_KEY)
    sent = request.form.get(FIELD, "")
    if not isinstance(expected, str) or not expected or not sent or len(sent) > MAX_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(expected.encode(), sent.encode())


def _reject() -> tuple[str, int]:
    return render_template("csrf_rejected.html"), 403


def init_csrf(app: Flask) -> None:
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        SESSION_COOKIE_NAME="qaxray_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_ENABLED=True,
    )
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect() -> Response | tuple[str, int] | None:
        if request.method in SAFE_METHODS or not app.config["CSRF_ENABLED"]:
            return None
        if _origin_is_foreign() or not _token_is_valid():
            return _reject()
        return None
