import logging
from collections.abc import Callable, Sequence

from app.core.browser import BrowserError, BrowserNotInstalledError
from app.core.checks.base import Check
from app.core.checks.context import CheckContext
from app.core.ports import HttpClientError
from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.result import CheckResult
from app.schemas.status import CheckStatus

logger = logging.getLogger(__name__)


ProgressCallback = Callable[[RunReport, int], None]  # (отчёт на данный момент, всего проверок)


class Runner:
    """Выполняет проверки по очереди. Сбой одной проверки не останавливает запуск."""

    def __init__(self, checks: Sequence[Check]) -> None:
        self._checks = list(checks)

    def run(
        self,
        context: CheckContext,
        *,
        on_progress: ProgressCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> RunReport:
        """Выполняет проверки по очереди.

        on_progress вызывается в начале (пустой отчёт, чтобы стало известно число проверок) и после
        каждой проверки. should_stop проверяется перед каждой проверкой: при остановке остальные
        не выполняются, возвращается отчёт по выполненным."""
        total = len(self._checks)
        results: list[CheckResult] = []
        if on_progress:
            on_progress(RunReport(results=[]), total)
        for check in self._checks:
            if should_stop and should_stop():
                break
            results.append(self._run_one(check, context))
            if on_progress:
                on_progress(RunReport(results=list(results)), total)
        return RunReport(results=results)

    def _run_one(self, check: Check, context: CheckContext) -> CheckResult:
        try:
            return check.run(context)
        except BrowserNotInstalledError:
            return self._skipped(
                check,
                "Chrome не найден: установите Google Chrome, и браузерные проверки заработают",
            )
        except (HttpClientError, TargetRejectedError, BrowserError) as error:
            return self._blocked(check, str(error).strip() or type(error).__name__)
        except Exception as error:  # noqa: BLE001 - сбой одной проверки не должен ронять запуск
            name = type(error).__name__
            logger.warning("Проверка %s завершилась ошибкой %s", check.check_id, name)
            return self._blocked(check, f"Внутренняя ошибка проверки ({name}): {error}")

    @staticmethod
    def _skipped(check: Check, reason: str) -> CheckResult:
        return CheckResult(
            check_id=check.check_id,
            title=check.title,
            status=CheckStatus.SKIPPED,
            severity=check.severity,
            expected="Проверка выполнена",
            actual="Не оценивалось",
            reason=reason,
        )

    @staticmethod
    def _blocked(check: Check, reason: str) -> CheckResult:
        return CheckResult(
            check_id=check.check_id,
            title=check.title,
            status=CheckStatus.BLOCKED,
            severity=check.severity,
            expected="Проверка выполнена",
            actual="Проверка не выполнена",
            reason=reason,
        )
