import logging
from collections.abc import Sequence

from app.core.checks.base import Check
from app.core.checks.context import CheckContext
from app.core.ports import HttpClientError
from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.result import CheckResult
from app.schemas.status import CheckStatus

logger = logging.getLogger(__name__)


class Runner:
    """Выполняет проверки по очереди. Сбой одной проверки не останавливает запуск."""

    def __init__(self, checks: Sequence[Check]) -> None:
        self._checks = list(checks)

    def run(self, context: CheckContext) -> RunReport:
        return RunReport(results=[self._run_one(check, context) for check in self._checks])

    def _run_one(self, check: Check, context: CheckContext) -> CheckResult:
        try:
            return check.run(context)
        except (HttpClientError, TargetRejectedError) as error:
            return self._blocked(check, str(error).strip() or type(error).__name__)
        except Exception as error:  # noqa: BLE001 - сбой одной проверки не должен ронять запуск
            name = type(error).__name__
            logger.warning("Проверка %s завершилась ошибкой %s", check.check_id, name)
            return self._blocked(check, f"Внутренняя ошибка проверки ({name}): {error}")

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
