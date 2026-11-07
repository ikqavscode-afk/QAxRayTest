from typing import Protocol

from flask import Blueprint, current_app, render_template, request
from pydantic import ValidationError

from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.target import RunTarget, describe_errors, parse_path_line

bp = Blueprint("main", __name__)

# Ошибка про ожидаемые статусы показывается у поля с путями
FIELD_ON_FORM = {"api_expected_statuses": "api_paths"}
EMPTY_VALUES = {"site_url": "", "api_docs_url": "", "api_base_url": "", "api_paths": ""}


class Service(Protocol):
    def run(self, target: RunTarget) -> RunReport: ...


@bp.get("/")
def index() -> str:
    return render_template("index.html", values=EMPTY_VALUES, errors={})


@bp.post("/check")
def check() -> str | tuple[str, int]:
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
    try:
        report = service.run(target)
    except TargetRejectedError as error:
        errors[error.field] = str(error)
        return render_template("index.html", values=values, errors=errors), 400
    return render_template("results.html", site_url=target.site_url, report=report)


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
