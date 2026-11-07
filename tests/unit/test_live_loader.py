import json
from pathlib import Path

import pytest

from app.schemas.status import CheckStatus
from tests.live.loader import LiveCase, LiveTargetsError, load_cases

EXAMPLE = Path(__file__).resolve().parents[1] / "live" / "targets.example.json"


def write(tmp_path, data):
    path = tmp_path / "targets.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_minimal_case_is_loaded_with_defaults(tmp_path):
    cases = load_cases(write(tmp_path, [{"name": "сайт", "site_url": "https://example.com/"}]))
    assert cases == [
        LiveCase(
            name="сайт",
            target=cases[0].target,
            expect={},
            rejected=False,
        )
    ]
    assert cases[0].target.site_url == "https://example.com/"
    assert cases[0].target.api_base_url is None


def test_full_case_with_api_and_expectations(tmp_path):
    data = [
        {
            "name": "api",
            "site_url": "https://example.com/",
            "api_docs_url": "https://api.example.com/openapi.json",
            "api_base_url": "https://api.example.com",
            "api_paths": ["/ping", "/items"],
            "api_expected_statuses": {"/ping": [201]},
            "expect": {"QC-021": "PASSED", "QC-024": "SKIPPED"},
        }
    ]
    case = load_cases(write(tmp_path, data))[0]
    assert case.target.api_paths == ("/ping", "/items")
    assert case.target.api_expected_statuses == {"/ping": (201,)}
    assert case.expect == {"QC-021": CheckStatus.PASSED, "QC-024": CheckStatus.SKIPPED}


def test_rejected_flag_is_loaded(tmp_path):
    data = [{"name": "ssrf", "site_url": "http://example.com/", "rejected": True}]
    assert load_cases(write(tmp_path, data))[0].rejected is True


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ("не json", "JSON"),
        ({"name": "x"}, "список"),
        ([], "пуст"),
        ([{"site_url": "https://example.com/"}], "name"),
        ([{"name": "x"}], "site_url"),
        ([{"name": "x", "site_ulr": "https://example.com/"}], "site_ulr"),
        ([{"name": "x", "site_url": "https://example.com/", "expect": {"QC-1": "PASSED"}}], "QC-1"),
        (
            [{"name": "x", "site_url": "https://example.com/", "expect": {"QC-001": "OK"}}],
            "OK",
        ),
        ([{"name": "x", "site_url": "https://example.com/", "api_paths": ["bad"]}], "x"),
        (
            [
                {"name": "x", "site_url": "https://example.com/"},
                {"name": "x", "site_url": "https://example.org/"},
            ],
            "повтор",
        ),
    ],
)
def test_invalid_files_are_rejected_with_a_helpful_message(tmp_path, data, fragment):
    with pytest.raises(LiveTargetsError) as caught:
        load_cases(write(tmp_path, data))
    assert fragment in str(caught.value)


def test_missing_file_is_reported_with_its_path(tmp_path):
    missing = tmp_path / "нет.json"
    with pytest.raises(LiveTargetsError, match="нет.json"):
        load_cases(missing)


def test_shipped_example_loads_and_uses_only_neutral_addresses():
    cases = load_cases(EXAMPLE)
    assert cases
    text = EXAMPLE.read_text(encoding="utf-8")
    hosts = {line.split("//")[1].split("/")[0] for line in text.splitlines() if "https://" in line}
    assert hosts <= {"example.com", "example.org"}
