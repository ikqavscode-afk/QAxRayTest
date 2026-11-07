import pytest

from app.core.checks.context import CheckContext
from tests.unit.support import SITE, FakeHttp


def make(base=None):
    return CheckContext(http=FakeHttp(), site_url=SITE, api_base_url=base)


def test_context_without_api_data_keeps_defaults():
    context = CheckContext(http=FakeHttp(), site_url=SITE)
    assert context.api_docs_url is None
    assert context.api_base_url is None
    assert context.api_paths == ()


def test_api_url_joins_base_and_path():
    assert make("https://api.example.com").api_url("/health") == "https://api.example.com/health"


def test_api_url_does_not_double_slashes():
    assert make("https://api.example.com/").api_url("/health") == "https://api.example.com/health"


def test_api_url_keeps_base_path_prefix():
    context = make("https://api.example.com/v1/")
    assert context.api_url("/items") == "https://api.example.com/v1/items"


def test_api_url_without_base_is_an_error():
    with pytest.raises(ValueError, match="API"):
        make(None).api_url("/health")
