"""Файлы для скачивания по сохранённому запуску: черновики bug report и архив Allure.

Берёт только уже замаскированный отчёт из хранилища; имена файлов фиксированные и безопасные."""

from dataclasses import dataclass
from importlib import metadata

from app.core.reports.allure import build_allure_archive
from app.core.reports.bug_report import (
    Environment,
    build_bug_reports,
    current_environment,
    render_json_bundle,
    render_markdown_bundle,
)
from app.schemas.run import RunRecord, RunStatus
from app.schemas.status import CheckStatus

KINDS = ("bug-reports.md", "bug-reports.json", "allure-results.zip")
READY = {RunStatus.COMPLETED, RunStatus.STOPPED}


@dataclass(frozen=True)
class Export:
    filename: str
    mimetype: str
    content: bytes


def app_version() -> str:
    try:
        return metadata.version("qaxraytest")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def environment() -> Environment:
    return current_environment(app_version())


def is_ready(record: RunRecord) -> bool:
    """Скачивать можно, когда запуск завершён или остановлен и в нём есть результаты."""
    return record.status in READY and record.report is not None and bool(record.report.results)


def has_failed(record: RunRecord) -> bool:
    return record.report is not None and any(
        item.status is CheckStatus.FAILED for item in record.report.results
    )


def build_export(record: RunRecord, kind: str) -> Export | None:
    """Файл для скачивания или None: вид неизвестен, результатов нет или FAILED-проверок нет."""
    if kind not in KINDS or not is_ready(record) or record.report is None:
        return None
    short = record.run_id[:8]
    env = environment()
    if kind == "allure-results.zip":
        content = build_allure_archive(record.report, record.target, record.created_at, env)
        return Export(f"qaxray-allure-results-{short}.zip", "application/zip", content)
    drafts = build_bug_reports(record.report, record.target, record.created_at, env)
    if not drafts:
        return None
    if kind == "bug-reports.md":
        text = render_markdown_bundle(drafts)
        return Export(f"qaxray-bug-reports-{short}.md", "text/markdown", text.encode())
    text = render_json_bundle(drafts)
    return Export(f"qaxray-bug-reports-{short}.json", "application/json", text.encode())
