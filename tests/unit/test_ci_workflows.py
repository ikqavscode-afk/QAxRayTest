"""Структура заданий CI: быстрое задание не зависит от браузера, браузерное изолировано."""

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_fast_ci_never_starts_a_browser_or_touches_the_network_suites():
    ci = text("ci.yml")
    assert "QAXRAY_BROWSER" not in ci and "QAXRAY_LIVE" not in ci
    assert "pytest" in ci and "ruff check" in ci and "mypy" in ci


def test_browser_workflow_is_separate_and_runs_only_the_browser_tests():
    browser = text("browser.yml")
    assert "tests/browser" in browser
    assert 'QAXRAY_BROWSER: "1"' in browser
    assert "tests/live" not in browser


def test_browser_workflow_can_be_run_by_hand_and_on_a_schedule_and_has_a_time_limit():
    browser = text("browser.yml")
    assert "workflow_dispatch" in browser
    assert "schedule:" in browser and "cron:" in browser
    assert "timeout-minutes:" in browser


def test_browser_workflow_runs_on_changes_to_browser_code_only():
    browser = text("browser.yml")
    assert "paths:" in browser
    assert "app/adapters/browser" in browser and "tests/browser" in browser


def test_browser_workflow_uses_no_sandbox_only_in_the_throwaway_runner():
    assert 'QAXRAY_BROWSER_NO_SANDBOX: "1"' in text("browser.yml")
    assert "QAXRAY_BROWSER_NO_SANDBOX" not in text("ci.yml")


def test_dependency_audit_is_separate_scheduled_and_never_blocks_the_fast_ci():
    audit = text("audit.yml")
    assert "pip-audit" in audit
    assert "workflow_dispatch" in audit and "schedule:" in audit and "cron:" in audit
    assert "timeout-minutes:" in audit
    assert "pip-audit" not in text("ci.yml")
