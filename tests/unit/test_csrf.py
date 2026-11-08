import re

import pytest

from app.schemas.report import RunReport
from app.schemas.target import RunTarget
from app.web import create_app
from tests.unit.support import StubService, sync_manager

TARGET = RunTarget(site_url="https://example.com/")
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
FORM = {"site_url": "https://example.com", "consent": "on"}


@pytest.fixture
def env():
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    app = create_app(service=service, store=store, manager=manager)  # защита включена по умолчанию
    app.config.update(TESTING=True)
    return app, service, store


def token_of(client, path="/"):
    match = TOKEN.search(client.get(path).get_data(as_text=True))
    assert match, f"на странице {path} нет поля csrf_token"
    return match.group(1)


def running_run(store):
    record = store.create(TARGET)
    store.update_progress(record.run_id, RunReport(results=[]), 5)
    return record.run_id


def test_form_carries_a_token_and_cookie_is_restricted(env):
    app, _, _ = env
    reply = app.test_client().get("/")
    assert TOKEN.search(reply.get_data(as_text=True))
    cookie = reply.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_token_is_not_predictable_between_sessions(env):
    app, _, _ = env
    assert token_of(app.test_client()) != token_of(app.test_client())


def test_post_without_token_is_rejected_and_nothing_starts(env):
    app, service, _ = env
    reply = app.test_client().post("/check", data=FORM)
    assert reply.status_code == 403
    assert service.targets == []
    assert "<form" not in reply.get_data(as_text=True)


@pytest.mark.parametrize("bad", ["", "wrong", "x" * 500])
def test_post_with_wrong_token_is_rejected(env, bad):
    app, service, _ = env
    client = app.test_client()
    token_of(client)
    assert client.post("/check", data={**FORM, "csrf_token": bad}).status_code == 403
    assert service.targets == []


def test_post_with_valid_token_works_and_token_is_reusable(env):
    app, service, _ = env
    client = app.test_client()
    token = token_of(client)
    for _ in range(2):
        reply = client.post("/check", data={**FORM, "csrf_token": token})
        assert reply.status_code == 303
    assert len(service.targets) == 2


def test_token_from_another_session_is_rejected(env):
    app, service, _ = env
    victim = token_of(app.test_client())
    attacker = app.test_client()
    token_of(attacker)
    assert attacker.post("/check", data={**FORM, "csrf_token": victim}).status_code == 403
    assert service.targets == []


def test_post_without_any_session_cookie_is_rejected(env):
    app, _, _ = env
    token = token_of(app.test_client())
    assert app.test_client().post("/check", data={**FORM, "csrf_token": token}).status_code == 403


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://localhost.evil.example"]
)
def test_foreign_origin_is_rejected_even_with_valid_token(env, origin):
    app, service, _ = env
    client = app.test_client()
    token = token_of(client)
    reply = client.post("/check", data={**FORM, "csrf_token": token}, headers={"Origin": origin})
    assert reply.status_code == 403
    assert service.targets == []


def test_same_origin_is_accepted(env):
    app, _, _ = env
    client = app.test_client()
    token = token_of(client)
    reply = client.post(
        "/check", data={**FORM, "csrf_token": token}, headers={"Origin": "http://localhost"}
    )
    assert reply.status_code == 303


def test_stop_form_has_a_token_and_stop_requires_it(env):
    app, _, store = env
    run_id = running_run(store)
    client = app.test_client()
    token = token_of(client, f"/runs/{run_id}")
    assert client.post(f"/runs/{run_id}/stop").status_code == 403
    assert store.get(run_id).stop_requested is False
    assert client.post(f"/runs/{run_id}/stop", data={"csrf_token": "bad"}).status_code == 403
    assert client.post(f"/runs/{run_id}/stop", data={"csrf_token": token}).status_code == 303
    assert store.get(run_id).stop_requested is True


def test_safe_requests_need_no_token(env):
    app, _, store = env
    run_id = running_run(store)
    client = app.test_client()
    for path in ("/", f"/runs/{run_id}", f"/runs/{run_id}/status"):
        assert client.get(path).status_code == 200


def test_rejection_page_is_plain_and_does_not_echo_input(env):
    app, _, _ = env
    reply = app.test_client().post("/check", data={**FORM, "site_url": "<b>МАРКЕР</b>"})
    text = reply.get_data(as_text=True)
    assert "МАРКЕР" not in text and "csrf_token" not in text
    assert "href" in text  # путь назад: ссылка на форму


def test_protection_can_be_switched_off_only_by_explicit_config(env):
    app, service, _ = env
    app.config.update(CSRF_ENABLED=False)
    assert app.test_client().post("/check", data=FORM).status_code == 303
    assert len(service.targets) == 1
