import re

import pytest

from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from app.web import create_app
from tests.unit.support import StubService, sync_manager

TARGET = RunTarget(site_url="https://example.com/")


def result(number=1, status=CheckStatus.PASSED, **changes):
    data = {
        "check_id": f"QC-{number:03d}",
        "title": f"Проверка номер {number}",
        "status": status,
        "severity": Severity.HIGH,
        "expected": f"Ожидание {number}",
        "actual": f"Факт {number}",
        "reason": "Причина есть" if status.requires_reason else None,
        "evidence": [],
        "duration_ms": 0,
    }
    data.update(changes)
    return CheckResult(**data)


@pytest.fixture
def env():
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    app = create_app(service=service, store=store, manager=manager)
    app.config.update(TESTING=True)
    return app.test_client(), store


def finished(store, *results):
    record = store.create(TARGET)
    store.finish(record.run_id, RunReport(results=list(results)))
    return record.run_id


def get(client, run_id, check_id="QC-001"):
    return client.get(f"/runs/{run_id}/checks/{check_id}")


# Таблица: три колонки, подробности вынесены на отдельную страницу


def test_results_table_has_three_columns_and_no_inline_details(env):
    client, store = env
    run_id = finished(store, result(1), result(2, CheckStatus.WARNING))
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    headers = re.findall(r'<th scope="col">([^<]+)</th>', html)
    assert headers == ["ID", "Проверка", "Статус"]
    assert "<details" not in html and "Подробности" not in html


def test_table_links_each_check_to_its_page_and_keeps_the_reason_visible(env):
    client, store = env
    run_id = finished(store, result(1), result(2, CheckStatus.WARNING, reason="Видимая причина"))
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert f'href="/runs/{run_id}/checks/QC-001"' in html
    assert f'href="/runs/{run_id}/checks/QC-002"' in html
    assert "Видимая причина" in html


# Страница проверки


def test_detail_page_shows_all_fields_with_status_and_severity_in_words(env):
    client, store = env
    item = result(
        7,
        CheckStatus.WARNING,
        reason="Страница строится скриптами",
        evidence=["GET https://example.com/a -> 200", "GET https://example.com/b -> 404"],
        duration_ms=120,
    )
    run_id = finished(store, item)
    response = get(client, run_id, "QC-007")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "QC-007" in html and "Проверка номер 7" in html and "WARNING" in html
    assert "Ожидание 7" in html and "Факт 7" in html
    assert "Страница строится скриптами" in html
    assert (
        "GET https://example.com/b -&gt; 404" in html or "GET https://example.com/b -> 404" in html
    )
    assert "высокая" in html and "120" in html
    assert "https://example.com/" in html  # адрес сайта


def test_detail_page_uses_a_definition_list_and_links_back_to_the_results(env):
    client, store = env
    run_id = finished(store, result(1))
    html = get(client, run_id).get_data(as_text=True)
    assert "<dl" in html and "<dt" in html and "<dd" in html
    assert f'href="/runs/{run_id}"' in html
    assert '<html lang="ru"' in html and "<h1" in html


def test_optional_parts_are_omitted_when_empty(env):
    client, store = env
    run_id = finished(store, result(1))
    html = get(client, run_id).get_data(as_text=True)
    assert "Причина" not in html and "Доказательства" not in html


def test_text_from_checks_is_escaped_on_the_detail_page(env):
    client, store = env
    run_id = finished(
        store,
        result(1, actual="<script>alert(1)</script>", evidence=["<img src=x onerror=alert(1)>"]),
    )
    html = get(client, run_id).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


# Запуски в процессе и остановленные


def test_running_run_shows_the_checks_that_are_already_done(env):
    client, store = env
    record = store.create(TARGET)
    store.update_progress(record.run_id, RunReport(results=[result(1)]), total=5)
    assert get(client, record.run_id, "QC-001").status_code == 200


def test_check_that_is_not_ready_yet_says_so_and_returns_404(env):
    client, store = env
    record = store.create(TARGET)
    store.update_progress(record.run_id, RunReport(results=[result(1)]), total=5)
    response = get(client, record.run_id, "QC-004")
    assert response.status_code == 404
    assert "пока не готов" in response.get_data(as_text=True)


def test_stopped_run_keeps_details_of_the_checks_that_ran(env):
    client, store = env
    record = store.create(TARGET)
    store.stop(record.run_id, RunReport(results=[result(1), result(2)]))
    assert get(client, record.run_id, "QC-002").status_code == 200
    assert get(client, record.run_id, "QC-003").status_code == 404


def test_missing_check_in_a_finished_run_says_there_is_no_such_check(env):
    client, store = env
    run_id = finished(store, result(1))
    response = get(client, run_id, "QC-009")
    html = response.get_data(as_text=True)
    assert response.status_code == 404
    assert "нет" in html and f'href="/runs/{run_id}"' in html


# Неверные адреса


@pytest.mark.parametrize("check_id", ["QC-1", "QC-0010", "qc-001", "abc", "QC-00a", "QC-001x"])
def test_malformed_check_id_gives_404(env, check_id):
    client, store = env
    run_id = finished(store, result(1))
    assert get(client, run_id, check_id).status_code == 404


@pytest.mark.parametrize("run_id", ["x" * 24, "short", "bad id!"])
def test_unknown_or_malformed_run_gives_the_run_not_found_page(env, run_id):
    client, _ = env
    response = get(client, run_id)
    assert response.status_code == 404
    assert "Запуск не найден" in response.get_data(as_text=True)


def test_failed_run_without_a_report_has_no_check_pages(env):
    client, store = env
    record = store.create(TARGET)
    store.fail(record.run_id, "Внутренняя ошибка приложения")
    assert get(client, record.run_id).status_code == 404
