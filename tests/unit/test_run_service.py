import pytest

from app.core.checks.catalog import default_checks
from app.core.ports import NetworkError
from app.core.security.policy import TargetPolicy, TargetRejectedError
from app.schemas.status import CheckStatus
from app.services.run_service import RunService
from tests.unit.support import SITE, FakeHttp, page

PUBLIC_IP = "93.184.216.34"


def make_service(http):
    policy = TargetPolicy(resolver=lambda host: [PUBLIC_IP])
    return RunService(http=http, policy=policy, checks=default_checks())


def test_default_profile_contains_group_a_in_order():
    ids = [check.check_id for check in default_checks()]
    assert ids == ["QC-001", "QC-002", "QC-003", "QC-004", "QC-005"]


def test_check_ids_are_unique():
    ids = [check.check_id for check in default_checks()]
    assert len(ids) == len(set(ids))


def test_healthy_site_passes_all_checks():
    report = make_service(FakeHttp(response=page())).run(SITE)
    assert len(report.results) == 5
    assert report.summary[CheckStatus.PASSED] == 5


def test_one_failing_check_does_not_hide_the_others():
    report = make_service(FakeHttp(response=page(status=404))).run(SITE)
    statuses = {result.check_id: result.status for result in report.results}
    assert statuses["QC-002"] is CheckStatus.FAILED
    assert statuses["QC-001"] is CheckStatus.PASSED
    assert report.summary[CheckStatus.FAILED] == 1


def test_network_failure_blocks_all_checks_but_site_is_requested_once():
    http = FakeHttp(error=NetworkError("нет ответа"))
    report = make_service(http).run(SITE)
    assert report.summary[CheckStatus.BLOCKED] == 5
    assert len(http.calls) == 1


def test_internal_address_is_rejected_before_any_request():
    http = FakeHttp(response=page())
    with pytest.raises(TargetRejectedError):
        make_service(http).run("http://127.0.0.1/admin")
    assert http.calls == []
