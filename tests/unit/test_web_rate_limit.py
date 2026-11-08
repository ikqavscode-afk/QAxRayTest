import pytest

from app.config import Settings
from app.schemas.report import RunReport
from app.services.rate_limiter import RateLimiter
from app.web import create_app
from tests.unit.support import FakeClock, StubService, sync_manager

FORM = {"site_url": "https://example.com", "consent": "on"}


@pytest.fixture
def env():
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    clock = FakeClock()
    limiter = RateLimiter(2, 60.0, clock=clock)
    app = create_app(service=service, store=store, manager=manager, limiter=limiter)
    app.config.update(TESTING=True, CSRF_ENABLED=False)
    return app, service, clock


def test_requests_over_the_limit_get_429_with_retry_after_and_start_nothing(env):
    app, service, _ = env
    client = app.test_client()
    assert client.post("/check", data=FORM).status_code == 303
    assert client.post("/check", data=FORM).status_code == 303
    reply = client.post("/check", data=FORM)
    assert reply.status_code == 429
    assert int(reply.headers["Retry-After"]) >= 1
    assert "Слишком много" in reply.get_data(as_text=True)
    assert len(service.targets) == 2


def test_form_values_are_kept_when_limited(env):
    app, _, _ = env
    client = app.test_client()
    for _ in range(2):
        client.post("/check", data=FORM)
    html = client.post("/check", data=FORM).get_data(as_text=True)
    assert 'value="https://example.com"' in html


def test_limit_is_lifted_after_the_window(env):
    app, service, clock = env
    client = app.test_client()
    for _ in range(3):
        client.post("/check", data=FORM)
    clock.now = 61
    assert client.post("/check", data=FORM).status_code == 303
    assert len(service.targets) == 3


def test_other_clients_are_not_affected(env):
    app, _, _ = env
    first = app.test_client()
    for _ in range(3):
        first.post("/check", data=FORM, environ_overrides={"REMOTE_ADDR": "198.51.100.1"})
    other = first.post("/check", data=FORM, environ_overrides={"REMOTE_ADDR": "198.51.100.2"})
    assert other.status_code == 303


def test_forwarded_for_header_cannot_dodge_the_limit(env):
    app, _, _ = env
    client = app.test_client()
    statuses = [
        client.post("/check", data=FORM, headers={"X-Forwarded-For": f"203.0.113.{n}"}).status_code
        for n in range(4)
    ]
    assert statuses == [303, 303, 429, 429]


def test_invalid_forms_do_not_use_up_the_limit(env):
    app, _, _ = env
    client = app.test_client()
    for _ in range(5):
        assert client.post("/check", data={"site_url": "", "consent": "on"}).status_code == 400
    assert client.post("/check", data=FORM).status_code == 303


def test_other_pages_are_not_limited(env):
    app, _, _ = env
    client = app.test_client()
    for _ in range(3):
        client.post("/check", data=FORM)
    assert client.get("/").status_code == 200


def test_limiter_comes_from_settings_by_default():
    settings = Settings(rate_limit_runs=1, rate_limit_window=30.0, max_parallel_runs=1)
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    app = create_app(service=service, store=store, manager=manager, settings=settings)
    app.config.update(TESTING=True, CSRF_ENABLED=False)
    client = app.test_client()
    assert client.post("/check", data=FORM).status_code == 303
    assert client.post("/check", data=FORM).status_code == 429
