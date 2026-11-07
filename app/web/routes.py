from typing import Protocol

from flask import Blueprint, current_app, render_template, request
from pydantic import ValidationError

from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.target import RunTarget, describe_errors

bp = Blueprint("main", __name__)

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
            target = RunTarget(
                site_url=values["site_url"],
                api_docs_url=values["api_docs_url"],
                api_base_url=values["api_base_url"],
                api_paths=_split_lines(values["api_paths"]),
            )
        except ValidationError as error:
            errors.update(describe_errors(error))
    if errors or target is None:
        return render_template("index.html", values=values, errors=errors), 400

    service: Service = current_app.extensions["run_service"]
    try:
        report = service.run(target)
    except TargetRejectedError as error:
        errors[error.field] = str(error)
        return render_template("index.html", values=values, errors=errors), 400
    return render_template("results.html", site_url=target.site_url, report=report)


def _split_lines(text: str) -> tuple[str, ...]:
    """По одному пути в строке; пустые строки и пробелы по краям отбрасываются."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())
