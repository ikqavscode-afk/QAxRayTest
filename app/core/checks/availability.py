from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus


class HomepageResponds:
    check_id = "QC-001"
    title = "Главная отвечает в пределах тайм-аута"
    severity = Severity.HIGH

    def run(self, context: CheckContext) -> CheckResult:
        response = context.homepage()
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=CheckStatus.PASSED,
            severity=self.severity,
            expected="Ответ получен в пределах тайм-аута",
            actual=f"Ответ получен за {response.elapsed_ms} мс",
            evidence=[f"GET {response.url} -> {response.status_code}"],
            duration_ms=response.elapsed_ms,
        )


class HomepageStatusIsSuccessful:
    check_id = "QC-002"
    title = "Статус ответа успешный"
    severity = Severity.HIGH

    def run(self, context: CheckContext) -> CheckResult:
        response = context.homepage()
        ok = 200 <= response.status_code < 300
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            severity=self.severity,
            expected="Код ответа 2xx",
            actual=f"Код ответа {response.status_code}",
            evidence=[f"GET {response.url} -> {response.status_code}"],
            duration_ms=response.elapsed_ms,
        )
