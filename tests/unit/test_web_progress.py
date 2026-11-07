import re
from pathlib import Path

import pytest

from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.run import RunStatus
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from app.web import create_app
from tests.unit.support import StubService, sync_manager

TARGET = RunTarget(site_url="https://example.com/")
STATIC = Path(__file__).resolve().parents[2] / "app/web/static"


def result(number):
    return CheckResult(
        check_id=f"QC-{number:03d}",
        title=f"Проверка {number}",
        status=CheckStatus.PASSED,
        severity=Severity.LOW,
        expected="-",
        actual="-",
    )


def partial(count):
    return RunReport(results=[result(n + 1) for n in range(count)])


@pytest.fixture
def env():
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    app = create_app(service=service, store=store, manager=manager)
    app.config.update(TESTING=True)
    return app.test_client(), store, service


def running(store, done=2, total=5, stop_requested=False):
    record = store.create(TARGET)
    if total:
        store.update_progress(record.run_id, partial(done), total)
    if stop_requested:
        store.request_stop(record.run_id)
    return record.run_id


def page(client, run_id):
    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


# Работающий запуск


def test_running_run_shows_progress_bar_status_text_and_partial_results(env):
    client, store, _ = env
    html = page(client, running(store, done=2, total=5))
    assert "Выполнено 2 из 5" in html
    assert re.search(r'<progress[^>]*max="5"[^>]*value="2"', html)
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert "QC-001" in html and "QC-002" in html and "QC-003" not in html


def test_progress_bar_has_an_accessible_name(env):
    client, store, _ = env
    html = page(client, running(store))
    assert re.search(r'<progress[^>]*aria-labelledby="progress-text"', html)


def test_running_run_before_the_first_report_says_it_is_preparing(env):
    client, store, _ = env
    html = page(client, running(store, total=0))
    assert "Подготовка запуска" in html and "<progress" not in html


def test_running_run_offers_a_stop_button_and_a_refresh_link_without_javascript(env):
    client, store, _ = env
    run_id = running(store)
    html = page(client, run_id)
    assert f'action="/runs/{run_id}/stop"' in html and 'method="post"' in html
    assert "Остановить запуск" in html
    assert f'href="/runs/{run_id}"' in html and "Обновить" in html


def test_running_run_loads_the_refresh_script_and_finished_runs_do_not(env):
    client, store, _ = env
    assert "/static/run.js" in page(client, running(store))
    done = store.create(TARGET)
    store.finish(done.run_id, partial(3))
    html = page(client, done.run_id)
    assert "/static/run.js" not in html and "Остановить запуск" not in html


def test_stop_requested_is_announced_on_the_page(env):
    client, store, _ = env
    assert "Остановка запрошена" in page(client, running(store, stop_requested=True))


# Остановленный и неудачный запуск


def test_stopped_run_says_how_many_checks_were_done_and_keeps_partial_results(env):
    client, store, _ = env
    record = store.create(TARGET)
    store.update_progress(record.run_id, partial(2), 5)
    store.stop(record.run_id, partial(2))
    html = page(client, record.run_id)
    assert "Запуск остановлен" in html and "2 из 5" in html
    assert "QC-001" in html and "QC-002" in html
    assert "Остановить запуск" not in html


def test_failed_run_shows_an_alert_without_a_table(env):
    client, store, _ = env
    record = store.create(TARGET)
    store.fail(record.run_id, "Внутренняя ошибка приложения")
    html = page(client, record.run_id)
    assert 'role="alert"' in html and "внутренней ошибкой" in html
    assert "<table" not in html


# Остановка


def test_stop_request_marks_the_run_and_redirects_back_to_it(env):
    client, store, _ = env
    run_id = running(store)
    response = client.post(f"/runs/{run_id}/stop")
    assert response.status_code == 303 and response.headers["Location"] == f"/runs/{run_id}"
    assert store.get(run_id).stop_requested is True


def test_stop_of_a_finished_run_is_harmless(env):
    client, store, _ = env
    record = store.create(TARGET)
    store.finish(record.run_id, partial(1))
    response = client.post(f"/runs/{record.run_id}/stop")
    assert response.status_code == 303
    assert store.get(record.run_id).status is RunStatus.COMPLETED


@pytest.mark.parametrize("run_id", ["x" * 24, "short", "bad id!"])
def test_stop_of_unknown_or_malformed_run_is_404(env, run_id):
    client, _, _ = env
    assert client.post(f"/runs/{run_id}/stop").status_code == 404


def test_stop_url_does_not_accept_get(env):
    client, store, _ = env
    assert client.get(f"/runs/{running(store)}/stop").status_code == 405


# Статус в JSON


def test_status_endpoint_returns_only_counts_and_status(env):
    client, store, _ = env
    run_id = running(store, done=3, total=7, stop_requested=True)
    response = client.get(f"/runs/{run_id}/status")
    assert response.status_code == 200 and response.is_json
    assert response.get_json() == {
        "status": "RUNNING",
        "done": 3,
        "total": 7,
        "stop_requested": True,
    }
    assert "example.com" not in response.get_data(as_text=True)  # адрес цели не раскрывается


@pytest.mark.parametrize("run_id", ["x" * 24, "short"])
def test_status_of_unknown_run_is_404_json(env, run_id):
    client, _, _ = env
    response = client.get(f"/runs/{run_id}/status")
    assert response.status_code == 404 and response.is_json


# Скрипт обновления


def test_refresh_script_is_served_and_avoids_unsafe_constructs():
    script = (STATIC / "run.js").read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "eval(", "document.write", "outerHTML", "http://", "https://"):
        assert forbidden not in script
    assert "/status" in script and "fetch(" in script


def test_refresh_script_is_reachable_through_the_app(env):
    client, _, _ = env
    response = client.get("/static/run.js")
    assert response.status_code == 200 and "javascript" in response.content_type


# Старт фонового запуска


def test_post_starts_a_background_run_and_redirects_without_waiting(env):
    client, _, service = env
    response = client.post("/check", data={"site_url": "https://example.com", "consent": "on"})
    assert response.status_code == 303 and response.headers["Location"].startswith("/runs/")
    assert len(service.targets) == 1
