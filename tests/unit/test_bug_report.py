import json

import pytest

from app.core.reports.bug_report import (
    BugReport,
    Environment,
    build_bug_reports,
    escape_markdown,
    render_json,
    render_markdown,
)
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from tests.unit.canary import assert_no_secrets, leaky_result

ENV = Environment(app_version="0.1.0", os_name="TestOS 1.0", browser_version=None)
TARGET = RunTarget(site_url="https://example.com", api_base_url="https://example.com/api")
STARTED = 1_700_000_000.0


def failed(number=1, **kwargs):
    fields = {
        "check_id": f"QC-{number:03d}",
        "title": f"Проверка {number}",
        "status": CheckStatus.FAILED,
        "severity": Severity.HIGH,
        "expected": "ожидали 200",
        "actual": "получили 500",
        "evidence": ["GET / -> 500"],
    }
    fields.update(kwargs)
    return CheckResult(**fields)


def passed(number=2):
    return CheckResult(
        check_id=f"QC-{number:03d}",
        title=f"Проверка {number}",
        status=CheckStatus.PASSED,
        severity=Severity.LOW,
        expected="e",
        actual="a",
    )


def build(results, **kwargs):
    return build_bug_reports(RunReport(results=results), TARGET, STARTED, ENV, **kwargs)


def test_one_draft_per_failed_check():
    drafts = build([failed(1), passed(2), failed(3)])
    assert [d.check_id for d in drafts] == ["QC-001", "QC-003"]


def test_no_drafts_without_failed():
    warning = CheckResult(
        check_id="QC-005",
        title="t",
        status=CheckStatus.WARNING,
        severity=Severity.LOW,
        expected="e",
        actual="a",
        reason="почему",
    )
    assert build([passed(), warning]) == []
    assert build([]) == []


def test_draft_fields_follow_the_concept():
    draft = build([failed(1)])[0]
    assert isinstance(draft, BugReport)
    assert draft.title == "QC-001: Проверка 1"
    assert draft.project == "https://example.com"
    assert draft.status == "не подтверждено"
    assert draft.priority is None
    assert draft.severity == "HIGH"
    assert draft.frequency == "1 запуск"
    assert draft.expected == "ожидали 200"
    assert draft.actual == "получили 500"
    assert draft.evidence == ["GET / -> 500"]
    assert draft.run_time.startswith("2023-11-14T22:13:20")
    assert any("0.1.0" in line for line in draft.environment)
    assert any("TestOS" in line for line in draft.environment)
    assert not any("Chrome" in line for line in draft.environment)
    assert any("https://example.com" in line for line in draft.steps)
    assert any("QC-001" in line for line in draft.steps)


def test_browser_version_is_listed_when_known():
    env = Environment(app_version="0.1.0", os_name="TestOS", browser_version="Chrome 130")
    draft = build_bug_reports(RunReport(results=[failed()]), TARGET, STARTED, env)[0]
    assert any("Chrome 130" in line for line in draft.environment)


def test_api_fields_are_in_steps_only_when_given():
    draft = build([failed()])[0]
    assert any("https://example.com/api" in line for line in draft.steps)
    bare = build_bug_reports(
        RunReport(results=[failed()]), RunTarget(site_url="https://example.com"), STARTED, ENV
    )[0]
    assert not any("API" in line for line in bare.steps)


def test_secrets_never_reach_a_draft_or_its_renderings():
    leaky = leaky_result(1, status=CheckStatus.FAILED)
    target = RunTarget(site_url="https://demo:hunter2-canary@example.com/?token=canary-7f3a9c1e5b")
    draft = build_bug_reports(RunReport(results=[leaky]), target, STARTED, ENV)[0]
    for text in (draft.model_dump_json(), render_markdown(draft), render_json(draft)):
        assert_no_secrets(text, "в черновике bug report")


def test_markdown_escapes_markup_in_every_text():
    hostile = failed(
        1,
        title="*жирный* [ссылка](http://x) <b>",
        expected="| ячейка |\n# заголовок",
        actual="`код` _курсив_",
        evidence=["```\nконец блока\n```", "<script>alert(1)</script>"],
    )
    text = render_markdown(build([hostile])[0])
    for raw in ("<script>", "<b>", "[ссылка](", "```"):
        assert raw not in text
    assert "\n# заголовок" not in text


def test_escape_markdown_flattens_lines():
    assert "\n" not in escape_markdown("a\nb\r\nc")
    assert escape_markdown("a*b") == "a\\*b"


def test_markdown_contains_all_sections():
    text = render_markdown(build([failed()])[0])
    for heading in (
        "Окружение",
        "Шаги воспроизведения",
        "Ожидаемый результат",
        "Фактический результат",
        "Важность",
        "Приоритет",
        "Статус",
        "Частота",
        "Доказательства",
    ):
        assert heading in text
    assert "не подтверждено" in text


def test_json_is_valid_and_complete():
    data = json.loads(render_json(build([failed()])[0]))
    assert data["status"] == "не подтверждено"
    assert data["priority"] is None
    assert data["check_id"] == "QC-001"
    assert data["evidence"] == ["GET / -> 500"]


def test_evidence_is_limited():
    many = failed(1, evidence=[f"строка {n}" for n in range(500)])
    draft = build([many])[0]
    assert len(draft.evidence) <= 50


@pytest.mark.parametrize("bad", ["", "  "])
def test_environment_requires_values(bad):
    with pytest.raises(ValueError):
        Environment(app_version=bad, os_name="x")
