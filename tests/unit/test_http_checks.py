import pytest

from app.core.checks.availability import ContentTypeIsHtml, HttpsIsUsed, NoRedirectLoop
from app.core.ports import NetworkError, TooManyRedirectsError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import FakeHttp, context_for, page


def run(check, **kwargs):
    return check.run(context_for(FakeHttp(**kwargs)))


# QC-003: HTTPS


def test_qc003_passes_for_https():
    result = run(HttpsIsUsed(), response=page(url="https://example.com/"))
    assert result.check_id == "QC-003"
    assert result.status is CheckStatus.PASSED


def test_qc003_passes_when_http_redirects_to_https():
    response = page(url="https://example.com/", redirects=["http://example.com/"])
    assert run(HttpsIsUsed(), response=response).status is CheckStatus.PASSED


def test_qc003_warns_for_plain_http_and_explains_why():
    result = run(HttpsIsUsed(), response=page(url="http://example.com/"))
    assert result.status is CheckStatus.WARNING
    assert "http" in result.reason
    assert result.severity is Severity.MEDIUM


# QC-004: нет цикла перенаправлений


def test_qc004_passes_without_redirects():
    result = run(NoRedirectLoop(), response=page())
    assert result.check_id == "QC-004"
    assert result.status is CheckStatus.PASSED
    assert "0" in result.actual


def test_qc004_passes_for_short_chain_of_different_addresses():
    response = page(
        url="https://example.com/c",
        redirects=["http://example.com/", "https://example.com/a"],
    )
    assert run(NoRedirectLoop(), response=response).status is CheckStatus.PASSED


def test_qc004_fails_when_address_repeats_in_chain():
    response = page(
        url="https://example.com/a",
        redirects=["https://example.com/a", "https://example.com/b"],
    )
    assert run(NoRedirectLoop(), response=response).status is CheckStatus.FAILED


def test_qc004_fails_when_client_gave_up_on_redirects():
    result = run(NoRedirectLoop(), error=TooManyRedirectsError("больше 5 перенаправлений"))
    assert result.status is CheckStatus.FAILED
    assert "больше 5" in result.actual


def test_qc004_still_propagates_network_errors():
    with pytest.raises(NetworkError):
        run(NoRedirectLoop(), error=NetworkError("нет ответа"))


# QC-005: Content-Type


@pytest.mark.parametrize(
    "content_type", ["text/html", "text/html; charset=utf-8", "TEXT/HTML;charset=UTF-8"]
)
def test_qc005_passes_for_html(content_type):
    result = run(ContentTypeIsHtml(), response=page(headers={"content-type": content_type}))
    assert result.check_id == "QC-005"
    assert result.status is CheckStatus.PASSED


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", "image/png"])
def test_qc005_fails_for_other_types_and_reports_actual(content_type):
    result = run(ContentTypeIsHtml(), response=page(headers={"content-type": content_type}))
    assert result.status is CheckStatus.FAILED
    assert content_type in result.actual


def test_qc005_fails_when_header_is_missing():
    result = run(ContentTypeIsHtml(), response=page(headers={}))
    assert result.status is CheckStatus.FAILED
    assert "отсутствует" in result.actual
