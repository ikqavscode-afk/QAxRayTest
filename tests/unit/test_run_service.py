import pytest

from app.core.checks.catalog import default_checks
from app.core.ports import NetworkError
from app.core.security.policy import TargetPolicy, TargetRejectedError
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from app.services.run_service import RunService
from tests.unit.support import SITE, FakeHttp, page

PUBLIC_IP = "93.184.216.34"


def make_service(http):
    policy = TargetPolicy(resolver=lambda host: [PUBLIC_IP])
    return RunService(http=http, policy=policy, checks=default_checks())


def test_default_profile_contains_profile_in_order():
    ids = [check.check_id for check in default_checks()]
    assert ids == [f"QC-00{number}" for number in range(1, 9)] + [
        "QC-011",
        "QC-013",
        "QC-020",
        "QC-021",
        "QC-022",
        "QC-023",
        "QC-024",
        "QC-025",
    ]


def test_check_ids_are_unique():
    ids = [check.check_id for check in default_checks()]
    assert len(ids) == len(set(ids))


def test_healthy_site_passes_all_checks():
    report = make_service(FakeHttp(response=page())).run(SITE)
    assert len(report.results) == 16
    assert report.summary[CheckStatus.PASSED] == 10
    assert report.summary[CheckStatus.SKIPPED] == 6  # адрес API не указан


def test_one_failing_check_does_not_hide_the_others():
    report = make_service(FakeHttp(response=page(status=404))).run(SITE)
    statuses = {result.check_id: result.status for result in report.results}
    assert statuses["QC-002"] is CheckStatus.FAILED
    assert statuses["QC-001"] is CheckStatus.PASSED
    assert report.summary[CheckStatus.FAILED] == 1


def test_network_failure_blocks_all_checks_but_site_is_requested_once():
    http = FakeHttp(error=NetworkError("нет ответа"))
    report = make_service(http).run(SITE)
    assert report.summary[CheckStatus.BLOCKED] == 10
    assert report.summary[CheckStatus.SKIPPED] == 6
    assert len(http.calls) == 1


def test_internal_address_is_rejected_before_any_request():
    http = FakeHttp(response=page())
    with pytest.raises(TargetRejectedError):
        make_service(http).run("http://127.0.0.1/admin")
    assert http.calls == []


class SpyCheck:
    """Проверка-наблюдатель: запоминает контекст, который получил от сервиса."""

    check_id = "QC-999"
    title = "Наблюдатель"
    severity = Severity.LOW

    def __init__(self):
        self.context = None

    def run(self, context):
        self.context = context
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=CheckStatus.PASSED,
            severity=self.severity,
            expected="-",
            actual="-",
        )


def spy_service(http):
    spy = SpyCheck()
    policy = TargetPolicy(resolver=lambda host: [PUBLIC_IP])
    return RunService(http=http, policy=policy, checks=[spy]), spy


def test_service_accepts_plain_string_for_backward_compatibility():
    service, spy = spy_service(FakeHttp(response=page()))
    service.run(SITE)
    assert spy.context.site_url == SITE
    assert spy.context.api_base_url is None


def test_service_passes_api_data_from_target_to_context():
    service, spy = spy_service(FakeHttp(response=page()))
    target = RunTarget(
        site_url=SITE,
        api_docs_url="https://api.example.com/openapi.json",
        api_base_url="https://api.example.com",
        api_paths=["/health"],
        api_expected_statuses={"/health": [201]},
    )
    service.run(target)
    assert spy.context.api_docs_url == "https://api.example.com/openapi.json"
    assert spy.context.api_base_url == "https://api.example.com"
    assert spy.context.api_paths == ("/health",)
    assert spy.context.api_expected_statuses == {"/health": (201,)}


@pytest.mark.parametrize("field", ["api_base_url", "api_docs_url"])
def test_internal_api_address_is_rejected_before_any_request(field):
    http = FakeHttp(response=page())
    service, _ = spy_service(http)
    target = RunTarget(site_url=SITE, **{field: "http://169.254.169.254/latest"})
    with pytest.raises(TargetRejectedError) as caught:
        service.run(target)
    assert caught.value.field == field  # форма покажет ошибку у нужного поля
    assert http.calls == []
