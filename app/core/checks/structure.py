from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

NOT_HTML_REASON = "Ответ не похож на HTML-документ"


class PageHasTitle:
    check_id = "QC-006"
    title = "У страницы есть title"
    severity = Severity.MEDIUM

    def run(self, context: CheckContext) -> CheckResult:
        summary = context.homepage_html()
        if not summary.has_html and not summary.has_body:
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", NOT_HTML_REASON)
        if summary.title:
            return self._result(CheckStatus.PASSED, f"Заголовок: {summary.title[:100]}")
        return self._result(
            CheckStatus.WARNING,
            "В исходном HTML заголовок не найден",
            "Заголовок может задаваться скриптом; уточните проверкой в браузере",
        )

    def _result(self, status: CheckStatus, actual: str, reason: str | None = None) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="В документе есть непустой title",
            actual=actual,
            reason=reason,
        )


class BodyIsNotEmpty:
    check_id = "QC-007"
    title = "Страница не пуста"
    severity = Severity.HIGH

    def run(self, context: CheckContext) -> CheckResult:
        summary = context.homepage_html()
        if not summary.has_html and not summary.has_body:
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", NOT_HTML_REASON)
        length = len(summary.visible_text)
        if length:
            return self._result(CheckStatus.PASSED, f"Видимого текста: {length} символов")
        if summary.script_count:
            return self._result(
                CheckStatus.WARNING,
                "В исходном HTML видимого текста нет",
                "Содержимое строится скриптами; проверьте страницу в браузере",
            )
        return self._result(CheckStatus.FAILED, "Видимого текста нет, скриптов нет")

    def _result(self, status: CheckStatus, actual: str, reason: str | None = None) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="На странице есть видимое содержимое",
            actual=actual,
            reason=reason,
        )


class HtmlStructure:
    check_id = "QC-008"
    title = "Базовая структура HTML"
    severity = Severity.LOW

    def run(self, context: CheckContext) -> CheckResult:
        summary = context.homepage_html()
        if not summary.has_html:
            return self._result(
                CheckStatus.FAILED, "Тег html не найден: ответ не похож на HTML-документ"
            )
        missing = [
            name
            for name, present in (
                ("head", summary.has_head),
                ("body", summary.has_body),
                ("атрибут lang", summary.lang is not None),
            )
            if not present
        ]
        if missing:
            return self._result(
                CheckStatus.WARNING,
                f"Не найдено: {', '.join(missing)}",
                f"Не найдено: {', '.join(missing)}",
            )
        return self._result(CheckStatus.PASSED, "Есть html, head, body и lang")

    def _result(self, status: CheckStatus, actual: str, reason: str | None = None) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="Есть html, head, body и атрибут lang",
            actual=actual,
            reason=reason,
        )
