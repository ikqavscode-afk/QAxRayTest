import pytest

from app.core.checks.availability import HomepageResponds, HomepageStatusIsSuccessful
from app.core.checks.context import CheckContext
from app.core.ports import HttpResponse, NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus

SITE = "https://example.com/"


class FakeHttp:
    """Заглушка клиента: отдаёт заранее заданный ответ или ошибку и считает вызовы."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.response


def page(status=200, elapsed_ms=120):
    return HttpResponse(
        url=SITE,
        status_code=status,
        headers={"content-type": "text/html"},
        body=b"<html></html>",
        truncated=False,
        elapsed_ms=elapsed_ms,
    )


def context_for(http):
    return CheckContext(http=http, site_url=SITE)


def test_homepage_is_requested_only_once():
    http = FakeHttp(response=page())
    context = context_for(http)
    context.homepage()
    context.homepage()
    assert http.calls == [SITE]


def test_homepage_error_is_cached_too():
    http = FakeHttp(error=NetworkError("нет ответа"))
    context = context_for(http)
    for _ in range(2):
        with pytest.raises(NetworkError):
            context.homepage()
    assert len(http.calls) == 1


def test_qc001_passes_when_page_responds():
    result = HomepageResponds().run(context_for(FakeHttp(response=page(elapsed_ms=250))))
    assert result.check_id == "QC-001"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.HIGH
    assert "250" in result.actual


def test_qc001_does_not_swallow_network_error():
    context = context_for(FakeHttp(error=NetworkError("нет ответа")))
    with pytest.raises(NetworkError):
        HomepageResponds().run(context)


@pytest.mark.parametrize("status", [200, 201, 204])
def test_qc002_passes_for_2xx(status):
    result = HomepageStatusIsSuccessful().run(context_for(FakeHttp(response=page(status))))
    assert result.check_id == "QC-002"
    assert result.status is CheckStatus.PASSED


@pytest.mark.parametrize("status", [301, 403, 404, 500, 503])
def test_qc002_fails_for_other_codes_and_reports_actual_status(status):
    result = HomepageStatusIsSuccessful().run(context_for(FakeHttp(response=page(status))))
    assert result.status is CheckStatus.FAILED
    assert str(status) in result.actual
