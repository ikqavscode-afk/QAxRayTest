import pytest
import requests
import responses

from app.adapters.http.client import NetworkError, SafeHttpClient, TooManyRedirectsError
from app.core.security.policy import TargetPolicy, TargetRejectedError

PUBLIC_IP = "93.184.216.34"
URL = "https://example.com/"


@pytest.fixture
def policy():
    return TargetPolicy(resolver=lambda host: [PUBLIC_IP])


@pytest.fixture
def client(policy):
    return SafeHttpClient(policy, timeout=1, max_bytes=50, max_redirects=3, retry_delay=0)


@responses.activate
def test_returns_status_headers_and_body(client):
    responses.add(
        responses.GET, URL, body="hello", status=200, headers={"Content-Type": "text/html"}
    )
    result = client.get(URL)
    assert result.status_code == 200
    assert result.headers["content-type"] == "text/html"
    assert result.body == b"hello"
    assert result.truncated is False
    assert result.url == URL


@responses.activate
def test_body_is_truncated_at_limit(client):
    responses.add(responses.GET, URL, body="x" * 500)
    result = client.get(URL)
    assert result.body == b"x" * 50
    assert result.truncated is True


@responses.activate
def test_error_status_is_returned_not_raised_and_not_retried(client):
    responses.add(responses.GET, URL, status=404)
    assert client.get(URL).status_code == 404
    assert len(responses.calls) == 1


@responses.activate
def test_rejected_url_never_reaches_network(client):
    with pytest.raises(TargetRejectedError):
        client.get("http://127.0.0.1/admin")
    assert len(responses.calls) == 0


@responses.activate
def test_network_failure_is_retried_twice_then_raised(client):
    responses.add(responses.GET, URL, body=requests.exceptions.ConnectTimeout())
    with pytest.raises(NetworkError):
        client.get(URL)
    assert len(responses.calls) == 3


@responses.activate
def test_network_failure_then_success_returns_response(client):
    responses.add(responses.GET, URL, body=requests.exceptions.ConnectionError())
    responses.add(responses.GET, URL, body="ok", status=200)
    assert client.get(URL).body == b"ok"
    assert len(responses.calls) == 2


@responses.activate
def test_redirect_is_followed_and_recorded(client):
    responses.add(responses.GET, URL, status=302, headers={"Location": "https://example.com/final"})
    responses.add(responses.GET, "https://example.com/final", body="done")
    result = client.get(URL)
    assert result.body == b"done"
    assert result.url == "https://example.com/final"
    assert result.redirects == [URL]


@responses.activate
def test_relative_redirect_is_resolved(client):
    responses.add(responses.GET, URL, status=301, headers={"Location": "/next"})
    responses.add(responses.GET, "https://example.com/next", body="next")
    assert client.get(URL).url == "https://example.com/next"


@responses.activate
def test_redirect_to_internal_address_is_blocked_before_request(client):
    responses.add(
        responses.GET, URL, status=302, headers={"Location": "http://169.254.169.254/latest/"}
    )
    with pytest.raises(TargetRejectedError):
        client.get(URL)
    assert len(responses.calls) == 1


@responses.activate
def test_redirect_loop_is_stopped(client):
    responses.add(responses.GET, URL, status=302, headers={"Location": URL})
    with pytest.raises(TooManyRedirectsError):
        client.get(URL)
    assert len(responses.calls) == 4  # первый запрос и 3 перехода
