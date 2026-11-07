from dataclasses import replace

import pytest

from app.core.checks.api import ApiContentTypeMatches, ApiJsonParses, ApiStatusIsExpected
from app.core.checks.context import CheckContext
from app.core.ports import NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeHttp, page

API = "https://api.example.com"
JSON = {"content-type": "application/json"}


def reply(status=200, body=b'{"ok": true}', headers=None, truncated=False):
    response = page(status=status, body=body, headers=JSON if headers is None else headers)
    return replace(response, truncated=True) if truncated else response


def context_with(paths, routes=None, base=API, expected=None):
    http = FakeHttp(routes={API + path: value for path, value in (routes or {}).items()})
    context = CheckContext(
        http=http,
        site_url=SITE,
        api_base_url=base,
        api_paths=tuple(paths),
        api_expected_statuses=expected,
    )
    return context, http


# Общие условия: нет адреса API или нет путей


@pytest.mark.parametrize("check", [ApiStatusIsExpected, ApiContentTypeMatches, ApiJsonParses])
def test_checks_are_skipped_without_base_url(check):
    context, http = context_with(["/health"], base=None)
    result = check().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "адрес API" in result.reason
    assert http.calls == []


@pytest.mark.parametrize("check", [ApiStatusIsExpected, ApiContentTypeMatches, ApiJsonParses])
def test_checks_are_skipped_without_paths(check):
    context, http = context_with([])
    result = check().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "GET" in result.reason
    assert http.calls == []


def test_each_path_is_requested_once_for_all_three_checks():
    context, http = context_with(["/a", "/b"], {"/a": reply(), "/b": reply()})
    for check in (ApiStatusIsExpected, ApiContentTypeMatches, ApiJsonParses):
        check().run(context)
    assert http.calls == [API + "/a", API + "/b"]


# QC-021: статус


def test_qc021_passes_when_all_paths_return_200():
    context, _ = context_with(["/a", "/b"], {"/a": reply(), "/b": reply()})
    result = ApiStatusIsExpected().run(context)
    assert result.check_id == "QC-021"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.HIGH
    assert len(result.evidence) == 2


def test_qc021_fails_and_names_the_path_with_unexpected_status():
    context, _ = context_with(["/a", "/b"], {"/a": reply(), "/b": reply(404)})
    result = ApiStatusIsExpected().run(context)
    assert result.status is CheckStatus.FAILED
    assert "/b" in result.actual and "404" in result.actual
    assert "/a" not in result.actual


def test_qc021_default_allows_only_200():
    context, _ = context_with(["/a"], {"/a": reply(204)})
    assert ApiStatusIsExpected().run(context).status is CheckStatus.FAILED


def test_qc021_accepts_status_expected_for_the_path():
    context, _ = context_with(["/ping"], {"/ping": reply(201)}, expected={"/ping": (201,)})
    assert ApiStatusIsExpected().run(context).status is CheckStatus.PASSED


def test_qc021_accepts_any_status_from_the_expected_list():
    routes = {"/a": reply(200), "/b": reply(404)}
    expected = {"/a": (200, 201), "/b": (200, 404)}
    context, _ = context_with(["/a", "/b"], routes, expected=expected)
    assert ApiStatusIsExpected().run(context).status is CheckStatus.PASSED


def test_qc021_fails_when_expected_status_differs_and_shows_what_was_expected():
    context, _ = context_with(["/ping"], {"/ping": reply(200)}, expected={"/ping": (201,)})
    result = ApiStatusIsExpected().run(context)
    assert result.status is CheckStatus.FAILED
    assert "200" in result.actual and "201" in result.actual


def test_qc021_paths_without_expectation_still_require_200():
    routes = {"/ping": reply(201), "/b": reply(201)}
    context, _ = context_with(["/ping", "/b"], routes, expected={"/ping": (201,)})
    result = ApiStatusIsExpected().run(context)
    assert result.status is CheckStatus.FAILED
    assert "/b" in result.actual and "/ping" not in result.actual


def test_qc021_does_not_leak_query_string_into_the_report():
    context, _ = context_with(["/a?token=SECRET"], {"/a?token=SECRET": reply(404)})
    result = ApiStatusIsExpected().run(context)
    text = result.actual + " ".join(result.evidence)
    assert "SECRET" not in text and "token" not in text
    assert "/a" in result.actual


def test_qc021_lets_network_errors_reach_the_runner():
    context, _ = context_with(["/a"], {"/a": NetworkError("нет ответа")})
    with pytest.raises(NetworkError):
        ApiStatusIsExpected().run(context)


# QC-022: Content-Type


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "application/problem+json"],
)
def test_qc022_passes_for_json_content_types(content_type):
    context, _ = context_with(["/a"], {"/a": reply(headers={"content-type": content_type})})
    result = ApiContentTypeMatches().run(context)
    assert result.check_id == "QC-022"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM


def test_qc022_warns_for_html_and_names_the_path():
    html = reply(headers={"content-type": "text/html"})
    context, _ = context_with(["/a", "/b"], {"/a": reply(), "/b": html})
    result = ApiContentTypeMatches().run(context)
    assert result.status is CheckStatus.WARNING
    assert "/b" in result.actual and "text/html" in result.actual


def test_qc022_warns_when_content_type_header_is_missing():
    context, _ = context_with(["/a"], {"/a": reply(headers={})})
    result = ApiContentTypeMatches().run(context)
    assert result.status is CheckStatus.WARNING
    assert "нет" in result.actual


def test_qc022_ignores_error_responses():
    err = reply(404, headers={"content-type": "text/html"})
    context, _ = context_with(["/a", "/b"], {"/a": reply(), "/b": err})
    assert ApiContentTypeMatches().run(context).status is CheckStatus.PASSED


def test_qc022_is_skipped_when_there_are_no_successful_responses():
    context, _ = context_with(["/a"], {"/a": reply(500)})
    result = ApiContentTypeMatches().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "успешн" in result.reason


# QC-023: JSON


def test_qc023_passes_for_valid_json():
    context, _ = context_with(["/a"], {"/a": reply(body=b'{"items": []}')})
    result = ApiJsonParses().run(context)
    assert result.check_id == "QC-023"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.HIGH


def test_qc023_fails_for_broken_json_and_names_the_path():
    context, _ = context_with(["/a", "/b"], {"/a": reply(), "/b": reply(body=b"{oops")})
    result = ApiJsonParses().run(context)
    assert result.status is CheckStatus.FAILED
    assert "/b" in result.actual and "/a" not in result.actual


def test_qc023_fails_for_empty_body_declared_as_json():
    context, _ = context_with(["/a"], {"/a": reply(body=b"")})
    assert ApiJsonParses().run(context).status is CheckStatus.FAILED


def test_qc023_is_skipped_when_no_response_is_declared_as_json():
    html = reply(body=b"<html></html>", headers={"content-type": "text/html"})
    context, _ = context_with(["/a"], {"/a": html})
    result = ApiJsonParses().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "JSON" in result.reason


def test_qc023_ignores_error_responses():
    err = reply(500, body=b"{oops")
    context, _ = context_with(["/a", "/b"], {"/a": reply(), "/b": err})
    assert ApiJsonParses().run(context).status is CheckStatus.PASSED


def test_qc023_warns_when_body_was_truncated_by_the_limit():
    context, _ = context_with(["/a"], {"/a": reply(body=b'{"items": [1, 2', truncated=True)})
    result = ApiJsonParses().run(context)
    assert result.status is CheckStatus.WARNING
    assert "лимит" in result.reason
