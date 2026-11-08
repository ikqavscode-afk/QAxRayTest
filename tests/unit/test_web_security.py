import re
from pathlib import Path

import pytest

from app.config import Settings
from app.schemas.report import RunReport
from app.schemas.target import RunTarget
from app.services.rate_limiter import RateLimiter
from app.web import create_app
from tests.unit.support import StubService, sync_manager

FORM = {"site_url": "https://example.com", "consent": "on"}
TEMPLATES = Path(__file__).resolve().parents[2] / "app/web/templates"


def make(settings=None, limiter=None, global_limiter=None):
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    app = create_app(
        service=service,
        store=store,
        manager=manager,
        limiter=limiter or RateLimiter(100, 60),
        global_limiter=global_limiter,
        settings=settings or Settings(),
    )
    app.config.update(TESTING=True, CSRF_ENABLED=False)
    return app, service, store


# Заголовок Host: защита от подмены DNS против самого приложения


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "localhost:5000", "[::1]:5000", "LOCALHOST"]
)
def test_local_hosts_are_allowed_by_default(host):
    app, _, _ = make()
    assert app.test_client().get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    ["evil.example", "evil.example:5000", "localhost.evil.example", "127.0.0.1.evil.example", ""],
)
def test_foreign_hosts_are_rejected_before_anything_else(host):
    app, service, _ = make()
    client = app.test_client()
    assert client.get("/", headers={"Host": host}).status_code == 400
    assert client.post("/check", data=FORM, headers={"Host": host}).status_code == 400
    assert service.targets == []


def test_static_files_also_require_an_allowed_host():
    app, _, _ = make()
    assert (
        app.test_client().get("/static/style.css", headers={"Host": "evil.example"}).status_code
        == 400
    )


def test_rejection_does_not_echo_the_host():
    app, _, _ = make()
    text = app.test_client().get("/", headers={"Host": "МАРКЕР.example"}).get_data(as_text=True)
    assert "МАРКЕР" not in text


def test_allowed_hosts_come_from_settings():
    app, _, _ = make(Settings(allowed_hosts=("demo.example.org",)))
    client = app.test_client()
    assert client.get("/", headers={"Host": "demo.example.org"}).status_code == 200
    assert client.get("/", headers={"Host": "localhost"}).status_code == 400


# Защитные заголовки


def pages(app, store):
    record = store.create(RunTarget(site_url="https://example.com/"))
    store.update_progress(record.run_id, RunReport(results=[]), 3)
    client = app.test_client()
    return [
        client.get("/"),
        client.get(f"/runs/{record.run_id}"),
        client.get(f"/runs/{record.run_id}/status"),
        client.get("/runs/aaaaaaaaaaaaaaaaaaaa"),
        client.get("/static/style.css"),
        client.post("/check", data={"site_url": ""}),
    ]


def test_every_response_carries_the_security_headers():
    app, _, store = make()
    for reply in pages(app, store):
        policy = reply.headers["Content-Security-Policy"]
        assert "default-src 'none'" in policy
        assert "script-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "form-action 'self'" in policy
        assert "base-uri 'none'" in policy
        assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
        assert reply.headers["X-Content-Type-Options"] == "nosniff"
        assert reply.headers["X-Frame-Options"] == "DENY"
        # не no-referrer: при нём браузер шлёт Origin: null с формы, и защита от CSRF отклонила бы её
        assert reply.headers["Referrer-Policy"] == "same-origin"
        assert "camera=()" in reply.headers["Permissions-Policy"]
        assert reply.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert reply.headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_dynamic_pages_are_not_cached_but_static_files_may_be():
    app, _, store = make()
    replies = pages(app, store)
    assert all(
        "no-store" in r.headers["Cache-Control"]
        for r in replies
        if r.status_code != 200 or "text/html" in r.mimetype
    )
    assert "no-store" in replies[0].headers["Cache-Control"]
    assert "no-store" not in replies[4].headers.get("Cache-Control", "")


def test_hsts_only_over_https():
    app, _, _ = make()
    client = app.test_client()
    assert "Strict-Transport-Security" not in client.get("/").headers
    secure = client.get("/", base_url="https://localhost")
    assert "max-age=" in secure.headers["Strict-Transport-Security"]


def test_templates_have_no_inline_scripts_styles_or_event_handlers():
    """Строгая политика CSP без unsafe-inline возможна только пока в шаблонах нет встроенных скриптов и стилей."""
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), path.name
        assert "<style" not in text, path.name
        assert not re.search(r"\sstyle=", text), path.name
        assert not re.search(r"\son[a-z]+=", text), path.name


# Доверенный прокси: настоящий адрес клиента для ограничения частоты


def post_as(client, proxy_ip, forwarded):
    return client.post(
        "/check",
        data=FORM,
        headers={"X-Forwarded-For": forwarded},
        environ_overrides={"REMOTE_ADDR": proxy_ip},
    ).status_code


def test_without_trusted_proxies_the_forwarded_header_is_ignored():
    app, _, _ = make(limiter=RateLimiter(1, 60))
    client = app.test_client()
    assert post_as(client, "10.0.0.9", "203.0.113.1") == 303
    assert post_as(client, "10.0.0.9", "203.0.113.2") == 429


def test_with_one_trusted_proxy_clients_are_told_apart():
    app, _, _ = make(Settings(trusted_proxies=1), limiter=RateLimiter(1, 60))
    client = app.test_client()
    assert post_as(client, "10.0.0.9", "203.0.113.1") == 303
    assert post_as(client, "10.0.0.9", "203.0.113.2") == 303
    assert post_as(client, "10.0.0.9", "203.0.113.1") == 429


def test_with_one_trusted_proxy_a_spoofed_prefix_does_not_dodge_the_limit():
    app, _, _ = make(Settings(trusted_proxies=1), limiter=RateLimiter(1, 60))
    client = app.test_client()
    assert post_as(client, "10.0.0.9", "1.1.1.1, 203.0.113.1") == 303
    assert post_as(client, "10.0.0.9", "2.2.2.2, 203.0.113.1") == 429


# Общий предел запусков на весь сервис


def test_global_limit_stops_a_crowd_of_different_clients():
    app, service, _ = make(global_limiter=RateLimiter(3, 60))
    client = app.test_client()
    codes = [
        client.post("/check", data=FORM, environ_overrides={"REMOTE_ADDR": f"198.51.100.{n}"})
        for n in range(5)
    ]
    assert [c.status_code for c in codes] == [303, 303, 303, 429, 429]
    assert int(codes[3].headers["Retry-After"]) >= 1
    assert "занят" in codes[3].get_data(as_text=True)
    assert len(service.targets) == 3


def test_global_limit_defaults_to_a_multiple_of_the_per_client_limit():
    app, _, _ = make(Settings(rate_limit_runs=1))
    client = app.test_client()
    results = [
        client.post(
            "/check", data=FORM, environ_overrides={"REMOTE_ADDR": f"198.51.100.{n}"}
        ).status_code
        for n in range(5)
    ]
    assert results.count(303) == 3 and results.count(429) == 2
