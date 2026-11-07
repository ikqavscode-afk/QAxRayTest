"""Секреты не должны попадать ни на один путь вывода (страницы, JSON, консоль, хранилище)."""

import pytest

from app.cli import main
from app.core.security.redactor import Redactor
from app.schemas.report import RunReport
from app.schemas.target import RunTarget
from app.services.run_manager import RunManager
from app.services.run_store import InMemoryRunStore
from app.web import create_app
from tests.unit.canary import PASSWORD, SECRET, assert_no_secrets, leaky_report
from tests.unit.support import StubService, sync_manager


@pytest.fixture
def env():
    service = StubService(leaky_report(3))
    manager, store = sync_manager(service)
    app = create_app(service=service, store=store, manager=manager)
    app.config.update(TESTING=True)
    return app.test_client(), store


def finished_run(client):
    response = client.post(
        "/check", data={"site_url": "https://example.com", "consent": "on"}, follow_redirects=False
    )
    return response.headers["Location"]


def test_the_store_never_holds_secrets_from_results():
    store = InMemoryRunStore()
    manager = RunManager(StubService(leaky_report(2)), store, starter=lambda job: job())
    record = manager.start(RunTarget(site_url="https://example.com/"))
    saved = store.get(record.run_id)
    assert_no_secrets(saved.report.model_dump_json(), "в хранилище")


def test_partial_progress_reports_are_masked_too():
    store = InMemoryRunStore()
    seen = []

    class Spy(StubService):
        def run(self, target, on_progress=None, control=None):
            on_progress(leaky_report(1), 3)
            seen.append(next(iter(store._runs.values())).report.model_dump_json())
            return leaky_report(3)

    RunManager(Spy(leaky_report(3)), store, starter=lambda job: job()).start(
        RunTarget(site_url="https://example.com/")
    )
    assert seen and all(PASSWORD not in text and SECRET not in text for text in seen)


def test_results_page_and_check_pages_do_not_leak_secrets(env):
    client, _ = env
    location = finished_run(client)
    assert_no_secrets(client.get(location).get_data(as_text=True), "на странице результата")
    for number in (1, 2, 3):
        page = client.get(f"{location}/checks/QC-{number:03d}")
        assert_no_secrets(page.get_data(as_text=True), "на странице проверки")


def test_status_json_exposes_only_counters(env):
    client, _ = env
    location = finished_run(client)
    assert_no_secrets(client.get(f"{location}/status").get_data(as_text=True), "в JSON статуса")


def test_site_address_with_a_token_is_masked_where_it_is_shown(env):
    client, _ = env
    response = client.post(
        "/check",
        data={"site_url": f"https://example.com/?token={SECRET}", "consent": "on"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert SECRET not in html and "example.com" in html


def test_console_output_does_not_leak_secrets(capsys):
    class Service:
        def run(self, target):
            return RunReport(results=leaky_report(2).results)

    main(["https://example.com"], service=Service())
    out = capsys.readouterr().out
    assert_no_secrets(out, "в консоли")
    assert "QC-001" in out  # сам отчёт выведен


def test_internal_error_logs_do_not_leak_secrets(caplog):
    store = InMemoryRunStore()
    service = StubService(error=RuntimeError(f"сбой url=/a?token={SECRET}&password={PASSWORD}"))
    manager = RunManager(service, store, starter=lambda job: job())
    with caplog.at_level("ERROR"):
        manager.start(RunTarget(site_url="https://example.com/"))
    record = next(iter(store._runs.values()))
    assert record.error == "Внутренняя ошибка приложения"
    assert_no_secrets(str(record), "в записи запуска")


def test_redactor_default_is_used_by_the_run_manager():
    assert isinstance(RunManager(StubService(), InMemoryRunStore())._redactor, Redactor)
