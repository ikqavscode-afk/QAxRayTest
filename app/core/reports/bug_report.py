"""Черновик bug report для проверок со статусом FAILED.

Черновик не подтверждён: приоритет пуст, статус «не подтверждено». Человек проверяет находку
и сам решает, заводить ли дефект. Все тексты проходят через Redactor до попадания в черновик,
а при выводе в Markdown разметка экранируется."""

import json
import platform
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.security.redactor import Redactor
from app.schemas.report import RunReport
from app.schemas.result import CheckResult
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget

STATUS_UNCONFIRMED = "не подтверждено"
FREQUENCY_ONE_RUN = "1 запуск"
MAX_EVIDENCE = 50
MAX_LINE = 2000

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|#~&()])")
_WHITESPACE = re.compile(r"\s+")


class Environment(BaseModel):
    """Где выполнялся запуск: версия приложения, система и (если была) версия Chrome."""

    model_config = ConfigDict(frozen=True)

    app_version: str = Field(min_length=1, pattern=r"\S")
    os_name: str = Field(min_length=1, pattern=r"\S")
    browser_version: str | None = None


def current_environment(app_version: str, browser_version: str | None = None) -> Environment:
    return Environment(
        app_version=app_version,
        os_name=f"{platform.system()} {platform.release()}".strip() or "неизвестно",
        browser_version=browser_version,
    )


class BugReport(BaseModel):
    """Черновик дефекта по одной проваленной проверке."""

    model_config = ConfigDict(frozen=True)

    check_id: str
    title: str
    project: str
    environment: list[str]
    steps: list[str]
    expected: str
    actual: str
    severity: str
    priority: str | None = None
    status: str = STATUS_UNCONFIRMED
    frequency: str = FREQUENCY_ONE_RUN
    run_time: str
    evidence: list[str] = Field(default_factory=list)


def _api_lines(target: RunTarget) -> list[str]:
    lines = []
    if target.api_docs_url:
        lines.append(f"Документация API: {target.api_docs_url}")
    if target.api_base_url:
        lines.append(f"Базовый адрес API: {target.api_base_url}")
    if target.api_paths:
        lines.append("Пути API: " + ", ".join(target.api_paths))
    return lines


def _draft(
    result: CheckResult,
    target: RunTarget,
    started_at: float,
    env: Environment,
    redactor: Redactor,
) -> BugReport:
    def clean(text: str) -> str:
        return redactor.redact_text(text)[:MAX_LINE]

    environment = [f"Приложение: {env.app_version}", f"Система: {env.os_name}"]
    if env.browser_version:
        environment.append(f"Браузер: {env.browser_version}")
    steps = [
        f"Открыть QAxRayTest и указать адрес сайта: {target.site_url}",
        *_api_lines(target),
        f"Запустить проверку {result.check_id}: {result.title}",
        f"Ожидалось: {result.expected}",
    ]
    run_time = datetime.fromtimestamp(started_at, UTC).isoformat(timespec="seconds")
    return BugReport(
        check_id=result.check_id,
        title=clean(f"{result.check_id}: {result.title}"),
        project=clean(target.site_url),
        environment=[clean(line) for line in environment],
        steps=[clean(line) for line in steps],
        expected=clean(result.expected),
        actual=clean(result.actual),
        severity=result.severity.value,
        run_time=run_time,
        evidence=[clean(item) for item in result.evidence[:MAX_EVIDENCE]],
    )


def build_bug_reports(
    report: RunReport,
    target: RunTarget,
    started_at: float,
    env: Environment,
    redactor: Redactor | None = None,
) -> list[BugReport]:
    """Один черновик на каждую проверку FAILED; без FAILED список пуст."""
    redactor = redactor or Redactor()
    return [
        _draft(result, target, started_at, env, redactor)
        for result in report.results
        if result.status is CheckStatus.FAILED
    ]


def escape_markdown(text: str) -> str:
    """Одна строка без разметки: переводы строк сводятся к пробелу, спецсимволы экранируются."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", _WHITESPACE.sub(" ", text).strip())


def _items(lines: list[str]) -> list[str]:
    return [f"- {escape_markdown(line)}" for line in lines] or ["- нет"]


def render_markdown(draft: BugReport) -> str:
    lines = [
        f"# {escape_markdown(draft.title)}",
        "",
        f"**Проект:** {escape_markdown(draft.project)}",
        f"**Статус:** {escape_markdown(draft.status)}",
        f"**Важность:** {escape_markdown(draft.severity)}",
        f"**Приоритет:** {escape_markdown(draft.priority or 'не назначен')}",
        f"**Частота:** {escape_markdown(draft.frequency)}",
        f"**Время запуска:** {escape_markdown(draft.run_time)}",
        "",
        "## Окружение",
        *_items(draft.environment),
        "",
        "## Шаги воспроизведения",
        *[f"{n}. {escape_markdown(step)}" for n, step in enumerate(draft.steps, start=1)],
        "",
        "## Ожидаемый результат",
        escape_markdown(draft.expected),
        "",
        "## Фактический результат",
        escape_markdown(draft.actual),
        "",
        "## Доказательства",
        *_items(draft.evidence),
        "",
    ]
    return "\n".join(lines)


def render_json(draft: BugReport) -> str:
    return json.dumps(draft.model_dump(), ensure_ascii=False, indent=2)


def render_markdown_bundle(drafts: list[BugReport]) -> str:
    """Все черновики запуска в одном Markdown-файле, разделённые горизонтальной чертой."""
    return "\n---\n\n".join(render_markdown(draft) for draft in drafts)


def render_json_bundle(drafts: list[BugReport]) -> str:
    return json.dumps([draft.model_dump() for draft in drafts], ensure_ascii=False, indent=2)
