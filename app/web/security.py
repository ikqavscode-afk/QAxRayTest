"""Защита веб-приложения на уровне запросов и ответов (ADR-0016).

Проверка заголовка Host (защита от подмены DNS против самого приложения), доверенный обратный прокси для
настоящего адреса клиента и защитные заголовки ответа, включая строгую политику CSP без встроенных скриптов."""

from urllib.parse import urlsplit

from flask import Flask, Response, request
from werkzeug.middleware.proxy_fix import ProxyFix

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
HSTS = "max-age=31536000"
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # same-origin: чужим сайтам Referer не уходит, а форма отправляет настоящий Origin
    # (при no-referrer браузер шлёт Origin: null, и проверка CSRF отклонила бы запрос)
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _hostname(host: str) -> str:
    """Имя без порта в нижнем регистре; IPv6 остаётся в квадратных скобках."""
    try:
        name = (urlsplit("//" + host.strip()).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""
    return f"[{name}]" if ":" in name else name


def init_security(app: Flask, allowed_hosts: tuple[str, ...], trusted_proxies: int) -> None:
    allowed = frozenset(allowed_hosts)
    if trusted_proxies:
        # доверяем только указанное число прокси; Host из заголовков прокси не берётся
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=trusted_proxies, x_proto=trusted_proxies
        )

    @app.before_request
    def check_host() -> tuple[str, int] | None:
        if _hostname(request.host) not in allowed:
            return "Недопустимое имя хоста.", 400
        return None

    @app.after_request
    def add_headers(response: Response) -> Response:
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        if request.endpoint != "static":
            response.headers.setdefault("Cache-Control", "no-store")
        return response
