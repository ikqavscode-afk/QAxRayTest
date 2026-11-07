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


# Прогресс и остановка


def test_progress_is_reported_at_start_and_after_every_check():
    calls = []
    Runner([StubCheck("QC-001"), StubCheck("QC-002"), StubCheck("QC-003")]).run(
        CONTEXT, on_progress=lambda report, total: calls.append((len(report.results), total))
    )
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_progress_reports_the_partial_report_with_results_so_far():
    seen = []
    Runner([StubCheck("QC-001"), StubCheck("QC-002")]).run(
        CONTEXT, on_progress=lambda report, total: seen.append([r.check_id for r in report.results])
    )
    assert seen == [[], ["QC-001"], ["QC-001", "QC-002"]]


def test_stop_flag_is_checked_between_checks_and_remaining_checks_do_not_run():
    ran = []

    class Recording(StubCheck):
        def run(self, context):
            ran.append(self.check_id)
            return super().run(context)

    flags = iter([False, False, True, True])
    report = Runner([Recording("QC-001"), Recording("QC-002"), Recording("QC-003")]).run(
        CONTEXT, should_stop=lambda: next(flags)
    )
    assert ran == ["QC-001", "QC-002"]
    assert [r.check_id for r in report.results] == ["QC-001", "QC-002"]


def test_a_stop_before_the_first_check_gives_an_empty_report():
    report = Runner([StubCheck("QC-001")]).run(CONTEXT, should_stop=lambda: True)
    assert report.results == []


def test_runner_without_callbacks_behaves_as_before():
    assert len(run(StubCheck("QC-001"), StubCheck("QC-002")).results) == 2
