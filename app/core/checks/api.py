import json

from app.core.checks.context import CheckContext
from app.core.ports import HttpResponse
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

NO_BASE_REASON = "Базовый адрес API не указан"


def _request_line(response: HttpResponse) -> str:
    return f"GET {response.url} -> {response.status_code}"


def _is_json(response: HttpResponse) -> bool:
    return "json" in response.headers.get("content-type", "").lower()


class ApiBaseResponds:
    check_id = "QC-020"
    title = "Базовый адрес API отвечает"
    severity = Severity.HIGH

    def run(self, context: CheckContext) -> CheckResult:
        if context.api_base_url is None:
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", reason=NO_BASE_REASON)
        response = context.api_root()  # сетевой сбой превратит в BLOCKED исполнитель
        evidence = [_request_line(response)]
        if response.status_code >= 500:
            return self._result(
                CheckStatus.FAILED,
                f"Сервер ответил ошибкой {response.status_code}",
                evidence=evidence,
                duration_ms=response.elapsed_ms,
            )
        return self._result(
            CheckStatus.PASSED,
            f"Ответ {response.status_code} получен за {response.elapsed_ms} мс",
            evidence=evidence,
            duration_ms=response.elapsed_ms,
        )

    def _result(
        self,
        status: CheckStatus,
        actual: str,
        *,
        reason: str | None = None,
        evidence: list[str] | None = None,
        duration_ms: int = 0,
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="Сервер отвечает в пределах тайм-аута без ошибки 5xx",
            actual=actual,
            reason=reason,
            evidence=evidence or [],
            duration_ms=duration_ms,
        )


class UnknownPathIsHandled:
    check_id = "QC-025"
    title = "Неизвестный путь обрабатывается предсказуемо"
    severity = Severity.LOW

    def run(self, context: CheckContext) -> CheckResult:
        if context.api_base_url is None:
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", reason=NO_BASE_REASON)
        response = context.api_unknown_path()
        evidence = [_request_line(response)]
        status_code = response.status_code
        if status_code >= 500:
            return self._result(
                CheckStatus.FAILED, f"Сервер ответил ошибкой {status_code}", evidence=evidence
            )
        if status_code < 400:
            return self._result(
                CheckStatus.WARNING,
                f"Несуществующий путь вернул {status_code}",
                reason="Ошибки могут маскироваться под успешные ответы; ожидался код 4xx",
                evidence=evidence,
            )
        if _is_json(response) and not response.truncated and not self._parses(response):
            return self._result(
                CheckStatus.WARNING,
                f"Код {status_code}, но тело ответа не является корректным JSON",
                reason="Заголовок говорит о JSON, а тело не разбирается",
                evidence=evidence,
            )
        return self._result(CheckStatus.PASSED, f"Код {status_code}", evidence=evidence)

    @staticmethod
    def _parses(response: HttpResponse) -> bool:
        if not response.body:
            return True  # пустое тело допустимо для 4xx
        try:
            json.loads(response.body.decode("utf-8", errors="replace"))
        except ValueError:
            return False
        return True

    def _result(
        self,
        status: CheckStatus,
        actual: str,
        *,
        reason: str | None = None,
        evidence: list[str] | None = None,
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="Код 4xx и разбираемое тело, без ошибки 5xx",
            actual=actual,
            reason=reason,
            evidence=evidence or [],
        )
