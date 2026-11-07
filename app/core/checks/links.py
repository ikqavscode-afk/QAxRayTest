from urllib.parse import urldefrag, urljoin, urlsplit

from app.core.browser import RenderedPage
from app.core.checks.context import CheckContext
from app.core.checks.refine import Verdict, refine
from app.core.checks.reporting import without_query
from app.core.html_summary import HtmlSummary
from app.core.ports import HttpClientError, HttpResponse
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

MAX_LINKS = 50
MAX_INTERNAL_LINKS = 10
ACCESS_LIMITED = frozenset({401, 403, 429})  # возможная защита сайта, а не битая ссылка
DEFAULT_PORTS = {"http": 80, "https": 443}
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
        return refine(context, self._from_html(context), self._from_browser)

    @staticmethod
    def _from_browser(rendered: RenderedPage) -> Verdict:
        links = rendered.hrefs[:MAX_LINKS]
        if not links:
            return Verdict(CheckStatus.SKIPPED, "ссылок нет", "В отрисованной странице ссылок нет")
        bad = sum(1 for href in links if _is_placeholder(href))
        if bad:
            return Verdict(
                CheckStatus.WARNING,
                f"пустых, «#» или javascript: ссылок: {bad} из {len(links)}",
                f"Ссылок-заглушек: {bad}; проверьте, что у основных ссылок есть реальный адрес",
            )
        return Verdict(CheckStatus.PASSED, f"проверено ссылок: {len(links)}, заглушек нет")

    def _from_html(self, context: CheckContext) -> CheckResult:
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
        return refine(context, self._from_html(context), self._from_browser)

    @staticmethod
    def _from_browser(rendered: RenderedPage) -> Verdict:
        if rendered.has_navigation:
            return Verdict(CheckStatus.PASSED, "найден nav или блок ссылок в шапке")
        return Verdict(
            CheckStatus.WARNING,
            "навигация не найдена",
            "Навигации нет ни в исходном HTML, ни на отрисованной странице",
        )

    def _from_html(self, context: CheckContext) -> CheckResult:
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


def _origin(url: str) -> tuple[str, int] | None:
    """Хост и порт для сравнения «тот же сайт». None для не-http и адресов с данными доступа."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname or parts.username or parts.password:
        return None
    return parts.hostname.lower(), port or DEFAULT_PORTS[parts.scheme]


def internal_links(base_url: str, hrefs: tuple[str, ...]) -> list[str]:
    """Уникальные внутренние ссылки в порядке документа, не более MAX_INTERNAL_LINKS.

    Внутренняя: тот же хост и порт, схема http или https. Заглушки, фрагменты, адрес самой
    страницы и чужие домены (в том числе протокол-относительные `//host`) отбрасываются."""
    base_origin = _origin(base_url)
    seen = {urldefrag(base_url)[0]}
    found: list[str] = []
    for href in hrefs:
        if _is_placeholder(href):
            continue
        try:
            absolute = urldefrag(urljoin(base_url, href))[0]
        except ValueError:
            continue
        if absolute in seen or _origin(absolute) != base_origin:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) == MAX_INTERNAL_LINKS:
            break
    return found


def _line(response: HttpResponse) -> str:
    return f"GET {without_query(response.url)} -> {response.status_code}"


class InternalLinksAreReachable:
    check_id = "QC-012"
    title = "Внутренние ссылки доступны"
    severity = Severity.HIGH
    expected = "До 10 внутренних ссылок (тот же домен, только GET) отвечают 2xx/3xx"

    def run(self, context: CheckContext) -> CheckResult:
        summary = context.homepage_html()  # сетевой сбой главной превратит в BLOCKED исполнитель
        if not _is_html(summary):
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", reason=NOT_HTML_REASON)
        links = internal_links(context.homepage().url, summary.hrefs)
        if not links:
            reason = (
                SCRIPTS_REASON if summary.script_count else "В исходном HTML нет внутренних ссылок"
            )
            return self._result(CheckStatus.SKIPPED, "Не оценивалось", reason=reason)
        broken: list[str] = []
        unconfirmed: list[str] = []
        evidence: list[str] = []
        for url in links:
            label = urlsplit(url).path or "/"
            try:
                response = context.get_cached(url)
            except HttpClientError:
                unconfirmed.append(f"{label}: нет ответа")
                continue
            evidence.append(_line(response))
            if response.status_code >= 400:
                target = unconfirmed if response.status_code in ACCESS_LIMITED else broken
                target.append(f"{label} -> {response.status_code}")
        if broken:
            actual = "Недоступные ссылки: " + "; ".join(broken)
            if unconfirmed:
                actual += ". Не подтверждены: " + "; ".join(unconfirmed)
            return self._result(CheckStatus.FAILED, actual, evidence=evidence)
        if unconfirmed:
            return self._result(
                CheckStatus.WARNING,
                "Не удалось подтвердить: " + "; ".join(unconfirmed),
                reason="Ответ 401, 403, 429 или сбой сети может быть защитой сайта "
                "или временной проблемой; проверьте вручную",
                evidence=evidence,
            )
        return self._result(
            CheckStatus.PASSED,
            f"Проверено внутренних ссылок: {len(links)}, все отвечают (2xx/3xx)",
            evidence=evidence,
        )

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
            expected=self.expected,
            actual=actual,
            reason=reason,
            evidence=evidence or [],
        )
