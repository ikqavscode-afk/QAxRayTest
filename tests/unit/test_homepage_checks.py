import pytest

from app.core.checks.availability import HomepageResponds, HomepageStatusIsSuccessful
from app.core.ports import NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeHttp, context_for, page


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
