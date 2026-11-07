import pytest
from pydantic import ValidationError

from app.schemas.target import (
    MAX_API_PATHS,
    RunTarget,
    describe_errors,
    parse_expectation,
    parse_path_line,
    parse_statuses,
)

SITE = "https://example.com/"


def test_only_site_url_is_required():
    target = RunTarget(site_url=SITE)
    assert target.api_docs_url is None
    assert target.api_base_url is None
    assert target.api_paths == ()


def test_blank_optional_urls_become_none_and_values_are_trimmed():
    target = RunTarget(
        site_url=f"  {SITE}  ", api_docs_url="   ", api_base_url=" https://api.example.com "
    )
    assert target.site_url == SITE
    assert target.api_docs_url is None
    assert target.api_base_url == "https://api.example.com"


def test_blank_site_url_is_rejected():
    with pytest.raises(ValidationError):
        RunTarget(site_url="   ")


def test_valid_paths_are_kept_in_order():
    target = RunTarget(site_url=SITE, api_paths=["/health", "/products?limit=5", "/v1/items/1"])
    assert target.api_paths == ("/health", "/products?limit=5", "/v1/items/1")


def test_duplicate_paths_are_removed_keeping_first():
    target = RunTarget(site_url=SITE, api_paths=["/a", "/b", "/a"])
    assert target.api_paths == ("/a", "/b")


@pytest.mark.parametrize(
    "path",
    [
        "health",  # нет ведущего «/»
        "",
        "//evil.example.org/x",  # путь не должен менять хост
        "http://evil.example.org/x",
        "/a b",
        "/a\nb",
        "/a\\b",
        "/../etc/passwd",
        "/a/../b",
        "/" + "x" * 300,
    ],
)
def test_unsafe_or_malformed_paths_are_rejected(path):
    with pytest.raises(ValidationError):
        RunTarget(site_url=SITE, api_paths=[path])


def test_too_many_paths_are_rejected():
    paths = [f"/p{number}" for number in range(MAX_API_PATHS + 1)]
    with pytest.raises(ValidationError):
        RunTarget(site_url=SITE, api_paths=paths)


def test_exactly_max_paths_are_allowed():
    paths = [f"/p{number}" for number in range(MAX_API_PATHS)]
    assert len(RunTarget(site_url=SITE, api_paths=paths).api_paths) == MAX_API_PATHS


def test_target_is_immutable():
    target = RunTarget(site_url=SITE)
    with pytest.raises(ValidationError):
        target.site_url = "https://other.example.com/"  # type: ignore[misc]


def test_describe_errors_maps_fields_to_readable_messages():
    with pytest.raises(ValidationError) as caught:
        RunTarget(site_url=SITE, api_paths=["health"])
    errors = describe_errors(caught.value)
    assert list(errors) == ["api_paths"]
    assert "«/»" in errors["api_paths"]
    assert "Value error" not in errors["api_paths"]


def test_describe_errors_reports_blank_site_url():
    with pytest.raises(ValidationError) as caught:
        RunTarget(site_url="  ")
    assert list(describe_errors(caught.value)) == ["site_url"]


# Ожидаемые статусы для GET-путей


def test_expected_statuses_default_to_empty():
    assert RunTarget(site_url=SITE).api_expected_statuses == {}


def test_expected_statuses_are_kept_as_tuples_for_known_paths():
    target = RunTarget(
        site_url=SITE, api_paths=["/ping", "/items"], api_expected_statuses={"/ping": [200, 201]}
    )
    assert target.api_expected_statuses == {"/ping": (200, 201)}


def test_expected_statuses_for_unknown_path_are_rejected():
    with pytest.raises(ValidationError) as caught:
        RunTarget(site_url=SITE, api_paths=["/ping"], api_expected_statuses={"/other": [200]})
    assert list(describe_errors(caught.value)) == ["api_expected_statuses"]


@pytest.mark.parametrize("statuses", [[99], [600], [], [200, 201, 202, 203, 204, 205]])
def test_invalid_expected_statuses_are_rejected(statuses):
    with pytest.raises(ValidationError):
        RunTarget(site_url=SITE, api_paths=["/ping"], api_expected_statuses={"/ping": statuses})


def test_parse_statuses_accepts_lists_with_spaces():
    assert parse_statuses("200,201") == (200, 201)
    assert parse_statuses(" 201 ") == (201,)
    assert parse_statuses("200 , 404") == (200, 404)


@pytest.mark.parametrize("text", ["", "abc", "99", "200,,201", "200;201"])
def test_parse_statuses_rejects_garbage(text):
    with pytest.raises(ValueError, match="статус"):
        parse_statuses(text)


def test_parse_path_line_without_status():
    assert parse_path_line("/ping") == ("/ping", None)
    assert parse_path_line("  /items?limit=5  ") == ("/items?limit=5", None)


def test_parse_path_line_with_status_splits_on_last_pipe():
    assert parse_path_line("/ping | 201") == ("/ping", (201,))
    assert parse_path_line("/a?x=1|200,404") == ("/a?x=1", (200, 404))


@pytest.mark.parametrize("line", ["/ping |", "/ping | abc", "/ping | 99"])
def test_parse_path_line_rejects_bad_status_part(line):
    with pytest.raises(ValueError, match="статус"):
        parse_path_line(line)


def test_parse_expectation_uses_the_last_equals_sign():
    assert parse_expectation("/ping=201") == ("/ping", (201,))
    assert parse_expectation("/a?limit=5=200,201") == ("/a?limit=5", (200, 201))


@pytest.mark.parametrize("text", ["/ping", "/ping=abc", "=200", "/ping="])
def test_parse_expectation_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_expectation(text)
