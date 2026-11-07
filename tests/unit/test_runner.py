import pytest

from app.core.browser import BrowserBudgetError, BrowserNetworkError, BrowserUnavailableError
from app.core.checks.context import CheckContext
from app.core.execution.runner import Runner
from app.core.ports import NetworkError, TooManyRedirectsError
from app.core.security.policy import TargetRejectedError
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus


class StubCheck:
    """Проверка-заглушка: возвращает заданный статус или бросает заданную ошибку."""

    severity = Severity.MEDIUM

    def __init__(self, check_id, status=CheckStatus.PASSED, error=None):
        self.check_id = check_id
        self.title = f"Проверка {check_id}"
        self._status = status
        self._error = error

    def run(self, context):
        if self._error is not None:
            raise self._error
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=self._status,
            severity=self.severity,
            expected="ожидание",
            actual="факт",
            reason="причина" if self._status.requires_reason else None,
        )


CONTEXT = CheckContext(http=None, site_url="https://example.com/")  # type: ignore[arg-type]


def run(*checks):
    return Runner(list(checks)).run(CONTEXT)


def test_all_checks_run_in_order():
    report = run(StubCheck("QC-001"), StubCheck("QC-002"), StubCheck("QC-003"))
    assert [r.check_id for r in report.results] == ["QC-001", "QC-002", "QC-003"]


def test_results_keep_original_statuses():
    report = run(StubCheck("QC-001", CheckStatus.FAILED), StubCheck("QC-002", CheckStatus.SKIPPED))
    assert [r.status for r in report.results] == [CheckStatus.FAILED, CheckStatus.SKIPPED]


@pytest.mark.parametrize(
    "error",
    [
        NetworkError("нет ответа от сайта"),
        TooManyRedirectsError("слишком много перенаправлений"),
        TargetRejectedError("внутренний адрес"),
        BrowserNetworkError("браузер не открыл страницу"),
        BrowserUnavailableError("драйвер браузера не найден"),
        BrowserBudgetError("исчерпан лимит времени браузера"),
    ],
)
def test_known_infrastructure_errors_become_blocked(error):
    result = run(StubCheck("QC-001", error=error)).results[0]
    assert result.status is CheckStatus.BLOCKED
    assert str(error) in result.reason
    assert result.check_id == "QC-001"
    assert result.title == "Проверка QC-001"
    assert result.severity is Severity.MEDIUM


def test_unexpected_error_becomes_blocked_and_run_continues():
    report = run(
        StubCheck("QC-001"),
        StubCheck("QC-002", error=RuntimeError("сбой внутри проверки")),
        StubCheck("QC-003"),
    )
    assert [r.status for r in report.results] == [
        CheckStatus.PASSED,
        CheckStatus.BLOCKED,
        CheckStatus.PASSED,
    ]
    assert "RuntimeError" in report.results[1].reason


def test_error_with_empty_message_still_gets_a_reason():
    result = run(StubCheck("QC-001", error=NetworkError(""))).results[0]
    assert result.status is CheckStatus.BLOCKED
    assert result.reason.strip()


def test_keyboard_interrupt_is_not_swallowed():
    with pytest.raises(KeyboardInterrupt):
        run(StubCheck("QC-001", error=KeyboardInterrupt()))


def test_summary_counts_every_status_including_zeros():
    report = run(
        StubCheck("QC-001"),
        StubCheck("QC-002"),
        StubCheck("QC-003", CheckStatus.FAILED),
        StubCheck("QC-004", error=NetworkError("нет ответа")),
    )
    assert report.summary == {
        CheckStatus.PASSED: 2,
        CheckStatus.FAILED: 1,
        CheckStatus.WARNING: 0,
        CheckStatus.SKIPPED: 0,
        CheckStatus.BLOCKED: 1,
    }


def test_empty_run_has_zero_summary():
    assert sum(run().summary.values()) == 0
