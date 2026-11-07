import pytest

from app.core.checks.api import ApiBaseResponds, UnknownPathIsHandled
from app.core.checks.context import UNKNOWN_API_PATH, CheckContext
from app.core.ports import NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeHttp, page

API = "https://api.example.com"
UNKNOWN = API + UNKNOWN_API_PATH
JSON = {"content-type": "application/json"}


def context_with(routes=None, base=API):
    http = FakeHttp(routes=routes or {})
    return CheckContext(http=http, site_url=SITE, api_base_url=base), http


def reply(status, body=b'{"error": "not found"}', headers=None, url=API):
    return page(status=status, url=url, body=body, headers=JSON if headers is None else headers)


# QC-020: базовый адрес API отвечает


def test_qc020_passes_when_api_responds():
    context, http = context_with({API: reply(200, url=API)})
    result = ApiBaseResponds().run(context)
    assert result.check_id == "QC-020"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.HIGH
    assert http.calls == [API]


def test_qc020_passes_even_when_root_returns_404_because_server_answered():
    context, _ = context_with({API: reply(404)})
    result = ApiBaseResponds().run(context)
    assert result.status is CheckStatus.PASSED
    assert "404" in result.actual


def test_qc020_fails_on_server_error():
    context, _ = context_with({API: reply(503)})
    result = ApiBaseResponds().run(context)
    assert result.status is CheckStatus.FAILED
    assert "503" in result.actual


def test_qc020_is_skipped_without_base_url_and_makes_no_request():
    context, http = context_with(base=None)
    result = ApiBaseResponds().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "адрес API" in result.reason
    assert http.calls == []


def test_qc020_lets_network_errors_reach_the_runner():
    context, _ = context_with({API: NetworkError("нет ответа")})
    with pytest.raises(NetworkError):
        ApiBaseResponds().run(context)


# QC-025: неизвестный путь


def test_qc025_passes_for_404_with_valid_json():
    context, _ = context_with({UNKNOWN: reply(404)})
    result = UnknownPathIsHandled().run(context)
    assert result.check_id == "QC-025"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW


def test_qc025_passes_for_any_4xx():
    context, _ = context_with({UNKNOWN: reply(401, body=b"")})
    assert UnknownPathIsHandled().run(context).status is CheckStatus.PASSED


def test_qc025_passes_for_html_404_page():
    html = reply(404, body=b"<html>404</html>", headers={"content-type": "text/html"})
    context, _ = context_with({UNKNOWN: html})
    assert UnknownPathIsHandled().run(context).status is CheckStatus.PASSED


def test_qc025_warns_when_json_error_body_is_broken():
    context, _ = context_with({UNKNOWN: reply(404, body=b"{oops")})
    result = UnknownPathIsHandled().run(context)
    assert result.status is CheckStatus.WARNING
    assert "JSON" in result.reason


def test_qc025_fails_on_server_error():
    context, _ = context_with({UNKNOWN: reply(500)})
    result = UnknownPathIsHandled().run(context)
    assert result.status is CheckStatus.FAILED
    assert "500" in result.actual


def test_qc025_warns_when_unknown_path_looks_successful():
    context, _ = context_with({UNKNOWN: reply(200)})
    result = UnknownPathIsHandled().run(context)
    assert result.status is CheckStatus.WARNING
    assert "200" in result.actual


def test_qc025_is_skipped_without_base_url():
    context, http = context_with(base=None)
    assert UnknownPathIsHandled().run(context).status is CheckStatus.SKIPPED
    assert http.calls == []
