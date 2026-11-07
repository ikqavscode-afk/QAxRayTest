from typing import Protocol

from flask import Blueprint, current_app, render_template, request

from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport

bp = Blueprint("main", __name__)


class Service(Protocol):
    def run(self, site_url: str) -> RunReport: ...


@bp.get("/")
def index() -> str:
    return render_template("index.html", site_url="", errors={})


@bp.post("/check")
def check() -> str | tuple[str, int]:
    site_url = request.form.get("site_url", "").strip()
    consent = request.form.get("consent") == "on"
    errors: dict[str, str] = {}
    if not site_url:
        errors["site_url"] = "Укажите адрес сайта, например https://example.com"
    if not consent:
        errors["consent"] = "Подтвердите право на тестирование этого сайта"
    if errors:
        return render_template("index.html", site_url=site_url, errors=errors), 400

    service: Service = current_app.extensions["run_service"]
    try:
        report = service.run(site_url)
    except TargetRejectedError as error:
        errors["site_url"] = str(error)
        return render_template("index.html", site_url=site_url, errors=errors), 400
    return render_template("results.html", site_url=site_url, report=report)
