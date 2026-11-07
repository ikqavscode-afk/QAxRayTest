import io
import json
import re
import zipfile

import pytest

from app.core.reports.allure import (
    STATUS_MAP,
    build_allure_archive,
    build_allure_files,
)
from app.core.reports.bug_report import Environment
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from tests.unit.canary import assert_no_secrets, leaky_report

ENV = Environment(app_version="0.1.0", os_name="TestOS 1.0", browser_version="Chrome 130")
TARGET = RunTarget(site_url="https://example.com")
STARTED = 1_700_000_000.0


def result(number, status, severity=Severity.MEDIUM, duration=100):
    return CheckResult(
        check_id=f"QC-{number:03d}",
        title=f"Проверка {number}",
        status=status,
        severity=severity,
        expected="ожидали",
        actual="получили",
        reason="причина" if status.requires_reason else None,
        evidence=["строка доказательства"],
        duration_ms=duration,
    )


def full_report():
    return RunReport(results=[result(n + 1, s) for n, s in enumerate(CheckStatus)])


def files_of(report):
    return build_allure_files(report, TARGET, STARTED, ENV)


def by_check(report):
    parsed = [
        json.loads(data) for name, data in files_of(report).items() if name.endswith("-result.json")
    ]
    return {item["fullName"].split(":")[0]: item for item in parsed}


def test_status_mapping_is_complete_and_as_decided():
    assert set(STATUS_MAP) == set(CheckStatus)
    assert STATUS_MAP[CheckStatus.PASSED] == "passed"
    assert STATUS_MAP[CheckStatus.FAILED] == "failed"
    assert STATUS_MAP[CheckStatus.BLOCKED] == "broken"
    assert STATUS_MAP[CheckStatus.SKIPPED] == "skipped"
    assert STATUS_MAP[CheckStatus.WARNING] == "passed"


def test_one_result_file_per_check_with_safe_names():
    files = files_of(full_report())
    names = [n for n in files if n.endswith("-result.json")]
    assert len(names) == len(CheckStatus)
    for name in files:
        assert re.fullmatch(r"[0-9a-f-]{36}-result\.json|environment\.properties", name)


def test_result_fields_follow_allure_format():
    data = by_check(full_report())["QC-001"]
    assert data["name"] == "QC-001: Проверка 1"
    assert data["status"] == "passed"
    assert data["stage"] == "finished"
    assert data["stop"] - data["start"] == 100
    assert re.fullmatch(r"[0-9a-f-]{36}", data["uuid"])
    assert re.fullmatch(r"[0-9a-f]{32}", data["historyId"])
    labels = {item["name"]: item["value"] for item in data["labels"]}
    assert labels["suite"] == "QAxRayTest"
    assert labels["severity"] == "normal"
    assert "ожидали" in data["description"] and "получили" in data["description"]
    assert any("строка доказательства" in step["name"] for step in data["steps"])


@pytest.mark.parametrize(
    ("severity", "allure"),
    [(Severity.HIGH, "critical"), (Severity.MEDIUM, "normal"), (Severity.LOW, "minor")],
)
def test_severity_mapping(severity, allure):
    report = RunReport(results=[result(1, CheckStatus.PASSED, severity)])
    data = by_check(report)["QC-001"]
    assert {i["name"]: i["value"] for i in data["labels"]}["severity"] == allure


def test_warning_is_passed_with_tag_and_reason():
    data = by_check(full_report())["QC-003"]
    assert data["status"] == "passed"
    tags = [i["value"] for i in data["labels"] if i["name"] == "tag"]
    assert "warning" in tags
    assert "причина" in data["statusDetails"]["message"]


def test_blocked_and_skipped_carry_reason():
    checks = by_check(full_report())
    for number, status in ((4, "skipped"), (5, "broken")):
        data = checks[f"QC-{number:03d}"]
        assert data["status"] == status
        assert "причина" in data["statusDetails"]["message"]


def test_failed_has_message_from_actual():
    data = by_check(full_report())["QC-002"]
    assert data["status"] == "failed"
    assert "получили" in data["statusDetails"]["message"]


def test_environment_properties():
    text = files_of(full_report())["environment.properties"].decode()
    assert "Приложение=0.1.0" in text
    assert "Chrome 130" in text
    assert "example.com" in text


def test_output_is_deterministic():
    assert files_of(full_report()) == files_of(full_report())


def test_empty_report_gives_only_environment():
    assert list(files_of(RunReport(results=[]))) == ["environment.properties"]


def test_archive_contains_the_same_files():
    report = full_report()
    with zipfile.ZipFile(io.BytesIO(build_allure_archive(report, TARGET, STARTED, ENV))) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == sorted(files_of(report))
        for name in zf.namelist():
            assert "/" not in name and ".." not in name


def test_secrets_never_reach_allure_files():
    report = leaky_report(3)
    target = RunTarget(site_url="https://example.com/?token=canary-7f3a9c1e5b")
    files = build_allure_files(report, target, STARTED, ENV)
    for name, data in files.items():
        assert_no_secrets(data.decode(), f"в {name}")
    with zipfile.ZipFile(io.BytesIO(build_allure_archive(report, target, STARTED, ENV))) as zf:
        for name in zf.namelist():
            assert_no_secrets(zf.read(name).decode(), f"в архиве {name}")
