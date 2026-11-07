from app.core.checks.context import CheckContext
from app.core.html_summary import HtmlSummary
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

MAX_LINKS = 50
NOT_HTML_REASON = "Ответ не похож на HTML-документ"
SCRIPTS_REASON = "Содержимое строится скриптами; проверьте страницу в браузере"


def _is_html(summary: HtmlSummary) -> bool:
    return summary.has_html or summary.has_body


def _is_placeholder(href: str) -> bool:
    lowered = href.strip().lower()
    return lowered in ("", "#") or lowered.startswith("javascript:")


class LinksAreWellFormed:
    check_id = "QC-011"
    title = "Ссылки имеют корректный href"
    severity = Severity.MEDIUM

    def run(self, context: CheckContext) -> CheckResult:
        summary = context.homepage_html()
        if not _is_html(summary):
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", NOT_HTML_REASON)
        links = summary.hrefs[:MAX_LINKS]
        if not links:
            if summary.script_count:
                return self._result(
                    CheckStatus.WARNING, "В исходном HTML ссылок нет", SCRIPTS_REASON
                )
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", "В исходном HTML ссылок нет")
        bad = sum(1 for href in links if _is_placeholder(href))
        if bad:
            return self._result(
                CheckStatus.WARNING,
                f"Пустых, «#» или javascript: ссылок: {bad} из {len(links)}",
                f"Ссылок-заглушек: {bad}; проверьте, что у основных ссылок есть реальный адрес",
            )
        return self._result(CheckStatus.PASSED, f"Проверено ссылок: {len(links)}, заглушек нет")

    def _result(self, status: CheckStatus, actual: str, reason: str | None = None) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="Нет пустых, «#» и javascript: ссылок (проверяется до 50 ссылок)",
            actual=actual,
            reason=reason,
        )


class NavigationFound:
    check_id = "QC-013"
    title = "Основная навигация найдена"
    severity = Severity.LOW

    def run(self, context: CheckContext) -> CheckResult:
        summary = context.homepage_html()
        if not _is_html(summary):
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", NOT_HTML_REASON)
        if summary.has_navigation:
            return self._result(CheckStatus.PASSED, "Найден nav или блок ссылок в шапке")
        reason = (
            SCRIPTS_REASON if summary.script_count else "В исходном HTML нет nav и ссылок в шапке"
        )
        return self._result(CheckStatus.WARNING, "Навигация в исходном HTML не найдена", reason)

    def _result(self, status: CheckStatus, actual: str, reason: str | None = None) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected="Есть nav, role=navigation или ссылки в шапке",
            actual=actual,
            reason=reason,
        )
