from urllib.parse import urlsplit

from app.core.checks.context import CheckContext
from app.core.ports import HttpResponse, TooManyRedirectsError
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus


def _request_line(response: HttpResponse) -> str:
    return f"GET {response.url} -> {response.status_code}"


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
            evidence=[_request_line(response)],
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
            evidence=[_request_line(response)],
            duration_ms=response.elapsed_ms,
        )


class HttpsIsUsed:
    check_id = "QC-003"
    title = "Используется HTTPS"
    severity = Severity.MEDIUM

    def run(self, context: CheckContext) -> CheckResult:
        response = context.homepage()
        secure = urlsplit(response.url).scheme.lower() == "https"
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=CheckStatus.PASSED if secure else CheckStatus.WARNING,
            severity=self.severity,
            expected="Итоговый адрес использует https",
            actual=f"Итоговый адрес {response.url}",
            reason=None if secure else "Сайт открыт по незащищённому http без перехода на https",
            evidence=[_request_line(response)],
            duration_ms=response.elapsed_ms,
        )


class NoRedirectLoop:
    check_id = "QC-004"
    title = "Нет цикла перенаправлений"
    severity = Severity.MEDIUM

    def run(self, context: CheckContext) -> CheckResult:
        # Для этой проверки превышение лимита перенаправлений это находка, а не сбой среды.
        try:
            response = context.homepage()
        except TooManyRedirectsError as error:
            return self._result(CheckStatus.FAILED, f"Клиент остановил переходы: {error}")
        chain = [*response.redirects, response.url]
        if len(set(chain)) != len(chain):
            return self._result(
                CheckStatus.FAILED,
                "Адрес повторяется в цепочке перенаправлений",
                evidence=[" -> ".join(chain)],
                duration_ms=response.elapsed_ms,
            )
        return self._result(
            CheckStatus.PASSED,
            f"Перенаправлений: {len(response.redirects)}",
            evidence=[" -> ".join(chain)],
            duration_ms=response.elapsed_ms,
        )

    def _result(
        self,
        status: CheckStatus,
        actual: str,
        evidence: list[str] | None = None,
        duration_ms: int = 0,
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="Цепочка перенаправлений конечна и без повторов",
            actual=actual,
            evidence=evidence or [],
            duration_ms=duration_ms,
        )


class ContentTypeIsHtml:
    check_id = "QC-005"
    title = "Content-Type главной страницы: HTML"
    severity = Severity.MEDIUM

    def run(self, context: CheckContext) -> CheckResult:
        response = context.homepage()
        raw = response.headers.get("content-type", "")
        media_type = raw.split(";")[0].strip().lower()
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=CheckStatus.PASSED if media_type == "text/html" else CheckStatus.FAILED,
            severity=self.severity,
            expected="Content-Type: text/html",
            actual=f"Content-Type: {raw}" if raw else "Заголовок Content-Type отсутствует",
            evidence=[_request_line(response)],
            duration_ms=response.elapsed_ms,
        )
