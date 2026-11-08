import re
from typing import Protocol

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response as RedirectResponse

from app.core.security.policy import TargetRejectedError
from app.schemas.run import RunRecord, RunStatus
from app.schemas.target import RunTarget, describe_errors, parse_path_line
from app.services.report_export import KINDS, build_export, has_failed, is_ready
from app.services.run_store import RunLimitError, RunStore

bp = Blueprint("main", __name__)
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
CHECK_ID = re.compile(r"^QC-\d{3}$")
SEVERITY_WORDS = {"HIGH": "высокая", "MEDIUM": "средняя", "LOW": "низкая"}

# Ошибка про ожидаемые статусы показывается у поля с путями
FIELD_ON_FORM = {"api_expected_statuses": "api_paths"}
EMPTY_VALUES = {"site_url": "", "api_docs_url": "", "api_base_url": "", "api_paths": ""}


class Manager(Protocol):
    def start(self, target: RunTarget) -> RunRecord: ...

    def stop(self, run_id: str) -> bool: ...


Store = RunStore


@bp.get("/")
def index() -> str:
    return render_template("index.html", values=EMPTY_VALUES, errors={})


@bp.post("/check")
def check() -> str | tuple[str, int] | RedirectResponse:
    values = {
        "site_url": request.form.get("site_url", "").strip(),
        "api_docs_url": request.form.get("api_docs_url", "").strip(),
        "api_base_url": request.form.get("api_base_url", "").strip(),
        "api_paths": request.form.get("api_paths", ""),
    }
    consent = request.form.get("consent") == "on"
    errors: dict[str, str] = {}
    if not values["site_url"]:
        errors["site_url"] = "Укажите адрес сайта, например https://example.com"
    if not consent:
        errors["consent"] = "Подтвердите право на тестирование этого сайта"

    target = None
    if values["site_url"]:
        try:
            paths, expected = _parse_path_lines(values["api_paths"])
            target = RunTarget(
                site_url=values["site_url"],
                api_docs_url=values["api_docs_url"],
                api_base_url=values["api_base_url"],
                api_paths=paths,
                api_expected_statuses=expected,
            )
        except ValidationError as error:
            for field, message in describe_errors(error).items():
                errors[FIELD_ON_FORM.get(field, field)] = message
        except ValueError as error:  # неверная запись ожидаемого статуса
            errors["api_paths"] = str(error)
    if errors or target is None:
        return render_template("index.html", values=values, errors=errors), 400

    manager: Manager = current_app.extensions["run_manager"]
    try:
        record = manager.start(target)
    except TargetRejectedError as error:  # адрес проверен до запуска: проверки не начинались
        errors[error.field] = str(error)
        return render_template("index.html", values=values, errors=errors), 400
    except RunLimitError:
        errors["site_url"] = (
            "Слишком много одновременных запусков: дождитесь завершения и повторите"
        )
        return render_template("index.html", values=values, errors=errors), 429
    # POST/Redirect/GET: результат живёт по своему адресу, обновление страницы не повторяет запросы
    return redirect(url_for("main.run_page", run_id=record.run_id), code=303)


@bp.get("/runs/<run_id>")
def run_page(run_id: str) -> str | tuple[str, int]:
    store: Store = current_app.extensions["run_store"]
    record = store.get(run_id) if RUN_ID.fullmatch(run_id) else None
    if record is None:
        return render_template("run_not_found.html"), 404
    done = len(record.report.results) if record.report else 0
    return render_template(
        "results.html",
        record=record,
        report=record.report,
        done=done,
        site_url=record.target.site_url,
        can_download=is_ready(record),
        has_failed=has_failed(record),
    )


@bp.get("/runs/<run_id>/downloads/<kind>")
def download(run_id: str, kind: str) -> Response | tuple[str, int]:
    """Скачивание отчёта: только для завершённого или остановленного запуска."""
    store: Store = current_app.extensions["run_store"]
    record = store.get(run_id) if RUN_ID.fullmatch(run_id) else None
    if record is None or kind not in KINDS:
        return render_template("run_not_found.html"), 404
    if record.status is RunStatus.RUNNING:
        return "Запуск ещё идёт: дождитесь завершения или остановки.", 409
    export = build_export(record, kind)
    if export is None:
        return render_template("run_not_found.html"), 404
    response = Response(export.content, mimetype=export.mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{export.filename}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/runs/<run_id>/checks/<check_id>")
def check_detail(run_id: str, check_id: str) -> str | tuple[str, int]:
    """Страница одной проверки: ожидаемое, фактическое, причина, доказательства."""
    store: Store = current_app.extensions["run_store"]
    record = store.get(run_id) if RUN_ID.fullmatch(run_id) else None
    if record is None:
        return render_template("run_not_found.html"), 404
    results = record.report.results if record.report else []
    found = next((item for item in results if item.check_id == check_id), None)
    if found is None or not CHECK_ID.fullmatch(check_id):
        return render_template("check_not_found.html", record=record), 404
    return render_template(
        "check_detail.html",
        record=record,
        result=found,
        severity=SEVERITY_WORDS[found.severity.value],
        site_url=record.target.site_url,
    )


@bp.post("/runs/<run_id>/stop")
def stop_run(run_id: str) -> RedirectResponse | tuple[str, int]:
    store: Store = current_app.extensions["run_store"]
    manager: Manager = current_app.extensions["run_manager"]
    if not RUN_ID.fullmatch(run_id) or store.get(run_id) is None:
        return render_template("run_not_found.html"), 404
    manager.stop(run_id)  # для завершённого запуска ничего не делает
    return redirect(url_for("main.run_page", run_id=run_id), code=303)


@bp.get("/runs/<run_id>/status")
def run_status(run_id: str) -> Response | tuple[Response, int]:
    """Состояние для скрипта обновления: только статус и счётчики, без данных о цели."""
    store: Store = current_app.extensions["run_store"]
    record = store.get(run_id) if RUN_ID.fullmatch(run_id) else None
    if record is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(
        {
            "status": record.status.value,
            "done": len(record.report.results) if record.report else 0,
            "total": record.total,
            "stop_requested": record.stop_requested,
        }
    )


def _parse_path_lines(text: str) -> tuple[tuple[str, ...], dict[str, tuple[int, ...]]]:
    """По одному пути в строке, после «|» необязательные ожидаемые статусы.

    Пустые строки пропускаются; при повторе пути действует первая запись."""
    paths: list[str] = []
    expected: dict[str, tuple[int, ...]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        path, statuses = parse_path_line(line)
        paths.append(path)
        if statuses is not None:
            expected.setdefault(path, statuses)
    return tuple(paths), expected
