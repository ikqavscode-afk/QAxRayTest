import pytest
from pydantic import ValidationError

from app.schemas.target import MAX_API_PATHS, RunTarget

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
