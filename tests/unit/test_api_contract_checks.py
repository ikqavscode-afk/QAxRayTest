import json
from dataclasses import replace

import pytest

from app.core.checks.api import ApiRequiredFields
from app.core.checks.context import CheckContext
from app.core.ports import NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeHttp, page

API = "https://api.example.com"
DOCS = API + "/openapi.json"
JSON = {"content-type": "application/json"}
PET = {"type": "object", "required": ["name", "id"]}
SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/pet": {
            "get": {
                "responses": {"200": {"content": {"application/json": {"schema": PET}}}},
            }
        }
    },
}


def reply(data, status=200, headers=None, truncated=False):
    body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    response = page(status=status, body=body, headers=JSON if headers is None else headers)
    if truncated:
        response = replace(response, truncated=True)
    return response


def context_with(routes, paths=("/pet",), docs=DOCS, base=API):
    http = FakeHttp(routes=routes)
    context = CheckContext(
        http=http, site_url=SITE, api_docs_url=docs, api_base_url=base, api_paths=tuple(paths)
    )
    return context, http


GOOD = {DOCS: reply(SPEC), API + "/pet": reply({"name": "Rex", "id": 1})}


def test_qc024_passes_when_required_fields_are_present():
    context, _ = context_with(GOOD)
    result = ApiRequiredFields().run(context)
    assert result.check_id == "QC-024"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM


def test_qc024_warns_and_names_missing_field():
    routes = {**GOOD, API + "/pet": reply({"id": 1})}
    context, _ = context_with(routes)
    result = ApiRequiredFields().run(context)
    assert result.status is CheckStatus.WARNING
    assert "/pet" in result.actual and "name" in result.actual
    assert result.reason


def test_qc024_documentation_and_paths_are_requested_once_each():
    context, http = context_with(GOOD)
    ApiRequiredFields().run(context)
    ApiRequiredFields().run(context)
    assert http.calls == [DOCS, API + "/pet"]


def test_qc024_does_not_leak_query_string_into_the_report():
    routes = {**GOOD, API + "/pet?token=SECRET": reply({"id": 1})}
    context, _ = context_with(routes, paths=("/pet?token=SECRET",))
    result = ApiRequiredFields().run(context)
    text = result.actual + " ".join(result.evidence) + (result.reason or "")
    assert "SECRET" not in text


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"docs": None}, "документации"),
        ({"base": None}, "адрес API"),
        ({"paths": ()}, "GET"),
    ],
)
def test_qc024_is_skipped_without_required_input_and_makes_no_request(kwargs, expected_reason):
    context, http = context_with(GOOD, **kwargs)
    result = ApiRequiredFields().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert expected_reason in result.reason
    assert http.calls == []


def test_qc024_is_skipped_when_documentation_is_unavailable():
    context, _ = context_with({**GOOD, DOCS: reply(b"", status=404)})
    result = ApiRequiredFields().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "404" in result.reason


def test_qc024_is_skipped_when_documentation_is_not_json_openapi():
    yaml = reply(b"openapi: 3.0.3", headers={"content-type": "text/yaml"})
    context, _ = context_with({**GOOD, DOCS: yaml})
    result = ApiRequiredFields().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "JSON" in result.reason


def test_qc024_is_skipped_when_documentation_was_truncated():
    context, _ = context_with({**GOOD, DOCS: reply(SPEC, truncated=True)})
    result = ApiRequiredFields().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "лимит" in result.reason


def test_qc024_is_skipped_when_path_is_not_described_in_documentation():
    routes = {**GOOD, API + "/other": reply({"x": 1})}
    context, _ = context_with(routes, paths=("/other",))
    result = ApiRequiredFields().run(context)
    assert result.status is CheckStatus.SKIPPED
    assert "схем" in result.reason


def test_qc024_ignores_responses_that_are_not_successful_json():
    routes = {
        **GOOD,
        API + "/pet": reply(b"<html></html>", headers={"content-type": "text/html"}),
    }
    context, _ = context_with(routes)
    assert ApiRequiredFields().run(context).status is CheckStatus.SKIPPED


def test_qc024_lets_network_errors_reach_the_runner():
    context, _ = context_with({**GOOD, DOCS: NetworkError("нет ответа")})
    with pytest.raises(NetworkError):
        ApiRequiredFields().run(context)
