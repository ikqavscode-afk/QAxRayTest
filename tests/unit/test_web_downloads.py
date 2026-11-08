import io
import json
import re
import zipfile

import pytest

from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from app.web import create_app
from tests.unit.canary import assert_no_secrets, leaky_result
from tests.unit.support import StubService, sync_manager

TARGET = RunTarget(site_url="https://example.com/")
SAFE_NAME = re.compile(r'attachment; filename="qaxray-[a-z-]+-[A-Za-z0-9_-]{1,8}\.(md|json|zip)"')


def result(number, status):
    return CheckResult(
        check_id=f"QC-{number:03d}",
        title=f"Проверка {number}",
        status=status,
        severity=Severity.HIGH,
        expected="ожидали",
        actual="получили",
        reason="причина" if status.requires_reason else None,
    )


def report_with_failed():
    return RunReport(results=[result(1, CheckStatus.PASSED), result(2, CheckStatus.FAILED)])


@pytest.fixture
def env():
    service = StubService(RunReport(results=[]))
    manager, store = sync_manager(service)
    app = create_app(service=service, store=store, manager=manager)
    app.config.update(TESTING=True, CSRF_ENABLED=False)
    return app.test_client(), store


def finished(store, report, how="finish"):
    record = store.create(TARGET)
    store.update_progress(record.run_id, report, len(report.results))
    getattr(store, how)(record.run_id, report)
    return record.run_id


def url(run_id, name):
    return f"/runs/{run_id}/downloads/{name}"


@pytest.mark.parametrize("how", ["finish", "stop"])
def test_buttons_are_shown_for_finished_and_stopped_runs(env, how):
    client, store = env
    run_id = finished(store, report_with_failed(), how)
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    for name in ("bug-reports.md", "bug-reports.json", "allure-results.zip"):
        assert url(run_id, name) in html


def test_bug_report_buttons_are_hidden_without_failed_checks(env):
    client, store = env
    run_id = finished(store, RunReport(results=[result(1, CheckStatus.PASSED)]))
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert url(run_id, "allure-results.zip") in html
    assert "bug-reports" not in html
    assert "FAILED" in html and "черновик" in html.lower()


def test_no_buttons_while_running_or_after_internal_error(env):
    client, store = env
    record = store.create(TARGET)
    store.update_progress(record.run_id, report_with_failed(), 5)
    assert "downloads" not in client.get(f"/runs/{record.run_id}").get_data(as_text=True)
    failed = store.create(TARGET)
    store.fail(failed.run_id, "ошибка")
    assert "downloads" not in client.get(f"/runs/{failed.run_id}").get_data(as_text=True)


def test_downloads_are_refused_while_running(env):
    client, store = env
    record = store.create(TARGET)
    store.update_progress(record.run_id, report_with_failed(), 5)
    for name in ("bug-reports.md", "bug-reports.json", "allure-results.zip"):
        assert client.get(url(record.run_id, name)).status_code == 409


def test_download_headers_are_safe(env):
    client, store = env
    run_id = finished(store, report_with_failed())
    for name, mimetype in (
        ("bug-reports.md", "text/markdown"),
        ("bug-reports.json", "application/json"),
        ("allure-results.zip", "application/zip"),
    ):
        reply = client.get(url(run_id, name))
        assert reply.status_code == 200
        assert reply.mimetype == mimetype
        assert SAFE_NAME.fullmatch(reply.headers["Content-Disposition"])
        assert reply.headers["X-Content-Type-Options"] == "nosniff"
        assert "no-store" in reply.headers["Cache-Control"]


def test_bug_report_contents(env):
    client, store = env
    run_id = finished(store, report_with_failed())
    text = client.get(url(run_id, "bug-reports.md")).get_data(as_text=True)
    assert "QC-002" in text and "QC-001" not in text and "не подтверждено" in text
    data = json.loads(client.get(url(run_id, "bug-reports.json")).get_data(as_text=True))
    assert [item["check_id"] for item in data] == ["QC-002"]


def test_allure_archive_contents(env):
    client, store = env
    run_id = finished(store, report_with_failed())
    body = client.get(url(run_id, "allure-results.zip")).data
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = archive.namelist()
    assert len([n for n in names if n.endswith("-result.json")]) == 2
    assert "environment.properties" in names


def test_bug_reports_missing_when_no_failed_checks(env):
    client, store = env
    run_id = finished(store, RunReport(results=[result(1, CheckStatus.PASSED)]))
    assert client.get(url(run_id, "bug-reports.md")).status_code == 404
    assert client.get(url(run_id, "bug-reports.json")).status_code == 404


@pytest.mark.parametrize(
    "name", ["unknown.txt", "..%2Fsecret", "bug-reports.md%00", "ALLURE-RESULTS.ZIP", "a" * 300]
)
def test_unknown_download_names_are_404(env, name):
    client, store = env
    run_id = finished(store, report_with_failed())
    assert client.get(url(run_id, name)).status_code == 404


@pytest.mark.parametrize("run_id", ["short", "x" * 100, "..", "a b" * 8])
def test_downloads_for_bad_or_unknown_run_ids_are_404(env, run_id):
    client, _ = env
    assert client.get(url(run_id, "allure-results.zip")).status_code == 404
    assert client.get(url("a" * 20, "allure-results.zip")).status_code == 404


def test_downloads_never_contain_secrets(env):
    client, store = env
    report = RunReport(results=[leaky_result(1, CheckStatus.FAILED)])
    run_id = finished(store, report)
    for name in ("bug-reports.md", "bug-reports.json"):
        assert_no_secrets(client.get(url(run_id, name)).get_data(as_text=True), name)
    body = client.get(url(run_id, "allure-results.zip")).data
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for name in archive.namelist():
            assert_no_secrets(archive.read(name).decode(), name)
