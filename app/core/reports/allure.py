"""Результаты в формате Allure (ADR-0012): по одному *-result.json на проверку и архив .zip.

Файлы читает Allure Report. Все тексты проходят через Redactor. Имена файлов строятся только из
UUID, поэтому никакие данные проверки не попадают в имя. Результат детерминирован: один и тот же
запуск даёт одни и те же файлы."""

import hashlib
import io
import json
import uuid
import zipfile

from app.core.reports.bug_report import Environment
from app.core.security.redactor import Redactor
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget

SUITE = "QAxRayTest"
ENVIRONMENT_FILE = "environment.properties"
MAX_LINE = 2000
MAX_STEPS = 50

STATUS_MAP: dict[CheckStatus, str] = {
    CheckStatus.PASSED: "passed",
    CheckStatus.FAILED: "failed",
    CheckStatus.BLOCKED: "broken",
    CheckStatus.SKIPPED: "skipped",
    CheckStatus.WARNING: "passed",  # отличается меткой warning
}
SEVERITY_MAP: dict[Severity, str] = {
    Severity.HIGH: "critical",
    Severity.MEDIUM: "normal",
    Severity.LOW: "minor",
}
_NAMESPACE = uuid.UUID("5b1f0c1e-2d4a-4a57-9a53-0b6a3d2f9e11")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _label(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value}


def _result_json(
    result: CheckResult, target: RunTarget, run_key: str, start_ms: int, redactor: Redactor
) -> dict[str, object]:
    def clean(text: str) -> str:
        return redactor.redact_text(text)[:MAX_LINE]

    site = clean(target.site_url)
    name = clean(f"{result.check_id}: {result.title}")
    message = result.actual if result.reason is None else f"{result.reason}. {result.actual}"
    labels = [
        _label("suite", SUITE),
        _label("feature", site),
        _label("story", result.check_id),
        _label("severity", SEVERITY_MAP[result.severity]),
    ]
    if result.status is CheckStatus.WARNING:
        labels.append(_label("tag", "warning"))
    history = hashlib.md5(f"{result.check_id}|{site}".encode(), usedforsecurity=False)
    return {
        "uuid": str(uuid.uuid5(_NAMESPACE, f"{run_key}|{result.check_id}")),
        "historyId": history.hexdigest(),
        "name": name,
        "fullName": name,
        "status": STATUS_MAP[result.status],
        "statusDetails": {"message": clean(_one_line(message))},
        "stage": "finished",
        "start": start_ms,
        "stop": start_ms + result.duration_ms,
        "description": clean(f"Ожидалось: {result.expected}\n\nФактически: {result.actual}"),
        "labels": labels,
        "steps": [
            {"name": clean(_one_line(line)), "status": "passed", "stage": "finished"}
            for line in result.evidence[:MAX_STEPS]
        ],
        "attachments": [],
        "parameters": [],
    }


def _environment_text(target: RunTarget, env: Environment, redactor: Redactor) -> str:
    lines = [
        f"Приложение={env.app_version}",
        f"Система={env.os_name}",
        f"Сайт={target.site_url}",
    ]
    if env.browser_version:
        lines.append(f"Браузер={env.browser_version}")
    return redactor.redact_text("\n".join(_one_line(line) for line in lines)) + "\n"


def build_allure_files(
    report: RunReport,
    target: RunTarget,
    started_at: float,
    env: Environment,
    redactor: Redactor | None = None,
) -> dict[str, bytes]:
    """Имя файла -> содержимое: результаты проверок и environment.properties."""
    redactor = redactor or Redactor()
    run_key = f"{started_at}|{target.site_url}"
    cursor = int(started_at * 1000)
    files: dict[str, bytes] = {}
    for result in report.results:
        data = _result_json(result, target, run_key, cursor, redactor)
        cursor += result.duration_ms
        files[f"{data['uuid']}-result.json"] = json.dumps(
            data, ensure_ascii=False, indent=2
        ).encode()
    files[ENVIRONMENT_FILE] = _environment_text(target, env, redactor).encode()
    return files


def build_allure_archive(
    report: RunReport,
    target: RunTarget,
    started_at: float,
    env: Environment,
    redactor: Redactor | None = None,
) -> bytes:
    """Архив .zip с теми же файлами; распаковать в каталог и открыть через Allure Report."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in build_allure_files(report, target, started_at, env, redactor).items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()
