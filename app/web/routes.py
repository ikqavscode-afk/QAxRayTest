import logging
import re
from typing import Protocol

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.run import RunRecord
from app.schemas.target import RunTarget, describe_errors, parse_path_line
from app.services.run_store import RunLimitError

bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
INTERNAL_ERROR = "Внутренняя ошибка приложения"

# Ошибка про ожидаемые статусы показывается у поля с путями
FIELD_ON_FORM = {"api_expected_statuses": "api_paths"}
EMPTY_VALUES = {"site_url": "", "api_docs_url": "", "api_base_url": "", "api_paths": ""}


class Service(Protocol):
    def run(self, target: RunTarget) -> RunReport: ...


class Store(Protocol):
    def create(self, target: RunTarget) -> RunRecord: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def finish(self, run_id: str, report: RunReport) -> RunRecord: ...

    def fail(self, run_id: str, reason: str) -> RunRecord: ...

    def discard(self, run_id: str) -> None: ...


@bp.get("/")
def index() -> str:
    return render_template("index.html", values=EMPTY_VALUES, errors={})


@bp.post("/check")
def check() -> str | tuple[str, int] | Response:
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

    service: Service = current_app.extensions["run_service"]
    store: Store = current_app.extensions["run_store"]
    try:
        record = store.create(target)
    except RunLimitError:
        errors["site_url"] = (
            "Слишком много одновременных запусков: дождитесь завершения и повторите"
        )
        return render_template("index.html", values=values, errors=errors), 429
    try:
        report = service.run(target)
    except TargetRejectedError as error:
        store.discard(record.run_id)  # проверки не начинались
        errors[error.field] = str(error)
        return render_template("index.html", values=values, errors=errors), 400
    except Exception:
        logger.exception("Запуск %s завершился внутренней ошибкой", record.run_id)
        store.fail(record.run_id, INTERNAL_ERROR)
    else:
        store.finish(record.run_id, report)
    # POST/Redirect/GET: результат живёт по своему адресу, обновление страницы не повторяет запросы
    return redirect(url_for("main.run_page", run_id=record.run_id), code=303)


@bp.get("/runs/<run_id>")
def run_page(run_id: str) -> str | tuple[str, int]:
    store: Store = current_app.extensions["run_store"]
    record = store.get(run_id) if RUN_ID.fullmatch(run_id) else None
    if record is None:
        return render_template("run_not_found.html"), 404
    return render_template("results.html", record=record, site_url=record.target.site_url)


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
