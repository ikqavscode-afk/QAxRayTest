import pytest

from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.web import create_app
from tests.unit.support import StubService


def result(check_id, status, reason=None):
    return CheckResult(
        check_id=check_id,
        title=f"Проверка {check_id}",
        status=status,
        severity=Severity.MEDIUM,
        expected="ожидание",
        actual="факт",
        reason=reason,
    )


@pytest.fixture
def make_client():
    def _make(service):
        app = create_app(service=service)
        app.config.update(TESTING=True)
        return app.test_client()

    return _make


def post(client, **overrides):
    data = {"site_url": "https://example.com", "consent": "on"}
    data.update(overrides)
    data = {key: value for key, value in data.items() if value is not None}
    return client.post("/check", data=data)


def test_home_page_has_form_with_labels_and_consent(make_client):
    html = make_client(StubService()).get("/").get_data(as_text=True)
    assert '<html lang="ru"' in html
    assert 'name="site_url"' in html and 'for="site_url"' in html
    assert 'name="consent"' in html and 'for="consent"' in html
    assert 'method="post"' in html.lower()


def test_missing_consent_is_rejected_next_to_checkbox_and_nothing_runs(make_client):
    service = StubService(RunReport())
    response = post(make_client(service), consent=None)
    assert response.status_code == 400
    assert "Подтвердите право" in response.get_data(as_text=True)
    assert service.urls == []


def test_empty_address_is_rejected_with_message(make_client):
    service = StubService(RunReport())
    response = post(make_client(service), site_url="   ")
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Укажите адрес сайта" in html
    assert 'aria-invalid="true"' in html
    assert service.urls == []


def test_rejected_address_shows_policy_message_and_keeps_input(make_client):
    service = StubService(error=TargetRejectedError("внутренний адрес"))
    response = post(make_client(service), site_url="http://127.0.0.1")
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "внутренний адрес" in html
    assert "http://127.0.0.1" in html


def test_successful_run_shows_every_result_with_status_word_and_summary(make_client):
    report = RunReport(
        results=[
            result("QC-001", CheckStatus.PASSED),
            result("QC-002", CheckStatus.FAILED),
            result("QC-003", CheckStatus.WARNING, "сайт открыт по http"),
        ]
    )
    service = StubService(report)
    response = post(make_client(service), site_url="  https://example.com  ")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert service.urls == ["https://example.com"]
    for expected in ["QC-001", "QC-002", "QC-003", "PASSED", "FAILED", "WARNING"]:
        assert expected in html
    assert "сайт открыт по http" in html
    assert "PASSED: 1" in html or "PASSED</" in html


def test_text_from_checks_is_escaped(make_client):
    report = RunReport(results=[result("QC-001", CheckStatus.WARNING, "<script>alert(1)</script>")])
    html = post(make_client(StubService(report))).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
