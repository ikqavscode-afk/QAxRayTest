"""Три сквозных сценария: форма, фон, прогресс, остановка, отчёты. Внешние сайты не затрагиваются."""

import io
import json
import re
import zipfile

from app.core.browser import BrowserNotInstalledError
from app.core.reports.allure import build_allure_archive
from app.core.reports.bug_report import (
    Environment,
    build_bug_reports,
    render_json,
    render_markdown,
)
from app.schemas.run import RunStatus
from app.schemas.status import CheckStatus
from tests.integration.conftest import API, SITE
from tests.unit.canary import SECRET, assert_no_secrets

PATHS = "/health\n/broken\n/missing | 404"
ENV = Environment(app_version="0.1.0", os_name="TestOS")


def statuses(record):
    return {item.check_id: item.status for item in record.report.results}


def test_scenario_full_run_from_form_to_reports(stack, network):
    """Форма -> фон -> страницы -> черновики и Allure; секрет из адреса нигде не просачивается."""
    run_id, reply = stack.run(
        site_url=f"{SITE}/?token={SECRET}",
        api_base_url=API,
        api_paths=PATHS,
    )
    assert reply.status_code == 303
    record = stack.store.get(run_id)
    assert record.status is RunStatus.COMPLETED
    assert len(record.report.results) == record.total == 25
    # Сетевой сбой на одном пути не блокирует остальные (PB-045)
    got = statuses(record)
    assert got["QC-021"] is CheckStatus.WARNING
    qc021 = next(r for r in record.report.results if r.check_id == "QC-021")
    assert "/broken" in qc021.actual and "/health" not in qc021.actual
    # Браузерные проверки получили заглушку и закрыли её
    assert stack.factory.calls == 1 and stack.browser.closed >= 1

    page = stack.client.get(f"/runs/{run_id}")
    status = stack.client.get(f"/runs/{run_id}/status")
    detail = stack.client.get(f"/runs/{run_id}/checks/QC-021")
    assert page.status_code == detail.status_code == status.status_code == 200
    data = json.loads(status.get_data(as_text=True))
    assert data["status"] == "COMPLETED" and data["done"] == data["total"] == 25
    for name, reply_ in (("страница", page), ("статус", status), ("деталь", detail)):
        assert_no_secrets(reply_.get_data(as_text=True), f"на {name}")

    # Отчёты строятся из сохранённого (замаскированного) отчёта
    drafts = build_bug_reports(record.report, record.target, record.created_at, ENV)
    archive = build_allure_archive(record.report, record.target, record.created_at, ENV)
    for draft in drafts:
        assert_no_secrets(render_markdown(draft) + render_json(draft), "в bug report")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert len([n for n in zf.namelist() if n.endswith("-result.json")]) == 25
        for name in zf.namelist():
            assert_no_secrets(zf.read(name).decode(), f"в архиве {name}")
    # Скачивание из интерфейса отдаёт те же замаскированные данные
    for kind in ("bug-reports.md", "bug-reports.json", "allure-results.zip"):
        reply_ = stack.client.get(f"/runs/{run_id}/downloads/{kind}")
        assert reply_.status_code in (200, 404)  # 404: FAILED-проверок нет, черновиков нет
        assert_no_secrets(
            reply_.get_data(as_text=True) if kind != "allure-results.zip" else "", kind
        )
    assert stack.client.get(f"/runs/{run_id}/downloads/allure-results.zip").status_code == 200
    # Все обращения шли к имитированной сети и только к разрешённым хостам
    hosts = {re.match(r"https://([^/]+)", call.request.url).group(1) for call in network.calls}
    assert hosts <= {"site.example.com", "api.example.com"}


def test_scenario_stop_in_the_middle(stack, network, gate):
    """Остановка во время выполнения: запуск STOPPED, отчёт частичный, браузер закрыт."""
    entered, release = gate
    stack_run = stack.client.post(
        "/check", data={"site_url": SITE + "/", "consent": "on", "csrf_token": stack.token()}
    )
    assert stack_run.status_code == 303
    run_id = stack_run.headers["Location"].rsplit("/", 1)[-1]
    assert entered.wait(10), "запуск не дошёл до сети"
    running = json.loads(stack.client.get(f"/runs/{run_id}/status").get_data(as_text=True))
    assert running["status"] == "RUNNING"

    stopped = stack.client.post(
        f"/runs/{run_id}/stop", data={"csrf_token": stack.token(f"/runs/{run_id}")}
    )
    assert stopped.status_code == 303
    release.set()
    assert stack.manager.wait(run_id, 20)

    record = stack.store.get(run_id)
    assert record.status is RunStatus.STOPPED
    assert len(record.report.results) < record.total
    shown = json.loads(stack.client.get(f"/runs/{run_id}/status").get_data(as_text=True))
    assert shown["status"] == "STOPPED"


def test_scenario_rejected_address_and_missing_browser(make_stack, network):
    """Запрещённый адрес отклоняется до сети; без Chrome браузерные проверки пропускаются."""
    stack = make_stack()
    before = len(network.calls)
    run_id, reply = stack.run(site_url="https://internal.example.com/")
    assert run_id is None and reply.status_code == 400
    assert len(network.calls) == before  # ни одного запроса
    assert stack.store.get("x" * 20) is None
    # без токена форма не принимается совсем
    forged = stack.client.post("/check", data={"site_url": SITE, "consent": "on"})
    assert forged.status_code == 403
    assert len(network.calls) == before

    stack = make_stack(browser_error=BrowserNotInstalledError("нет Chrome"))
    run_id, _ = stack.run()
    record = stack.store.get(run_id)
    assert record.status is RunStatus.COMPLETED
    skipped = [r for r in record.report.results if r.status is CheckStatus.SKIPPED]
    assert any("Chrome" in (r.reason or "") for r in skipped)
    assert not [
        r
        for r in record.report.results
        if r.status is CheckStatus.BLOCKED and "Chrome" in (r.reason or "")
    ]
