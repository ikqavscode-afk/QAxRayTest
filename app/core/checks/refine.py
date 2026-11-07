"""Уточнение результата по исходному HTML результатом по отрисованной странице.

Когда по исходному HTML получился WARNING (например, страница строится скриптами) и браузер подключён,
итоговый вердикт даёт отрисованная страница, а в тексте результата остаются оба взгляда. Если браузер
недоступен, остаётся результат по HTML с пометкой, что уточнение не выполнено."""

from collections.abc import Callable
from dataclasses import dataclass

from app.core.browser import BrowserError, RenderedPage
from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult
from app.schemas.status import CheckStatus

DEFAULT_REASON = "Проверьте страницу вручную"


@dataclass(frozen=True)
class Verdict:
    """Итог по отрисованной странице: статус, что найдено, причина (для WARNING и SKIPPED)."""

    status: CheckStatus
    actual: str
    reason: str | None = None


def refine(
    context: CheckContext,
    html_result: CheckResult,
    verdict_for: Callable[[RenderedPage], Verdict],
) -> CheckResult:
    if html_result.status is not CheckStatus.WARNING or not context.has_browser:
        return html_result
    try:
        rendered = context.rendered_page()
    except BrowserError as error:
        note = f"уточнение в браузере не выполнено: {error}"
        merged = f"{html_result.reason}; {note}" if html_result.reason else note
        return html_result.model_copy(update={"reason": merged})
    verdict = verdict_for(rendered)
    reason = verdict.reason or (DEFAULT_REASON if verdict.status.requires_reason else None)
    return html_result.model_copy(
        update={
            "status": verdict.status,
            "actual": f"В браузере: {verdict.actual}. По исходному HTML: {html_result.actual}",
            "reason": reason,
            "evidence": [*html_result.evidence, f"Исходный HTML: {html_result.status.value}"],
        }
    )
