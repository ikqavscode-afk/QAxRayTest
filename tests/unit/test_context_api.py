import pytest

from app.core.checks.context import UNKNOWN_API_PATH, CheckContext
from app.core.ports import NetworkError
from tests.unit.support import SITE, FakeHttp, page


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


API = "https://api.example.com"


def test_api_get_requests_each_address_once():
    http = FakeHttp(response=page())
    context = CheckContext(http=http, site_url=SITE, api_base_url=API)
    first = context.api_get(f"{API}/health")
    assert context.api_get(f"{API}/health") is first
    assert http.calls == [f"{API}/health"]


def test_api_get_caches_errors_too():
    http = FakeHttp(error=NetworkError("нет ответа"))
    context = CheckContext(http=http, site_url=SITE, api_base_url=API)
    for _ in range(2):
        with pytest.raises(NetworkError):
            context.api_get(f"{API}/health")
    assert len(http.calls) == 1


def test_api_root_and_unknown_path_use_the_base_address():
    http = FakeHttp(response=page())
    context = CheckContext(http=http, site_url=SITE, api_base_url=API + "/")
    context.api_root()
    context.api_unknown_path()
    assert http.calls == [API + "/", API + UNKNOWN_API_PATH]


def test_api_docs_requests_documentation_url_and_requires_it():
    http = FakeHttp(response=page())
    context = CheckContext(
        http=http, site_url=SITE, api_docs_url=API + "/openapi.json", api_base_url=API
    )
    context.api_docs()
    assert http.calls == [API + "/openapi.json"]
    with pytest.raises(ValueError, match="документации"):
        make(API).api_docs()
