import pytest

from app.core.security.policy import TargetPolicy, TargetRejectedError


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://example.com/path?q=1",
    ],
)
def test_http_and_https_are_allowed(url):
    assert TargetPolicy().validate(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com",
        "example.com",
        "",
    ],
)
def test_other_schemes_are_rejected(url):
    with pytest.raises(TargetRejectedError):
        TargetPolicy().validate(url)
