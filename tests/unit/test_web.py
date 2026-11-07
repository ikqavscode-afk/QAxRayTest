import pytest

from app.config import SettingsError
from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.run import RunStatus
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from app.services.run_store import InMemoryRunStore
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
    def _make(service, store=None):
        app = create_app(service=service, store=store)
        app.config.update(TESTING=True)
        return app.test_client()

    return _make


def post(client, follow=True, **overrides):
    """Отправка формы; по умолчанию с переходом по перенаправлению (POST/Redirect/GET)."""
    data = {"site_url": "https://example.com", "consent": "on"}
    data.update(overrides)
    data = {key: value for key, value in data.items() if value is not None}
    return client.post("/check", data=data, follow_redirects=follow)


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
    assert service.targets == []


def test_empty_address_is_rejected_with_message(make_client):
    service = StubService(RunReport())
    response = post(make_client(service), site_url="   ")
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Укажите адрес сайта" in html
    assert 'aria-invalid="true"' in html
    assert service.targets == []


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
    assert [t.site_url for t in service.targets] == ["https://example.com"]
    for expected in ["QC-001", "QC-002", "QC-003", "PASSED", "FAILED", "WARNING"]:
        assert expected in html
    assert "сайт открыт по http" in html
    assert "PASSED: 1" in html or "PASSED</" in html


def test_text_from_checks_is_escaped(make_client):
    report = RunReport(results=[result("QC-001", CheckStatus.WARNING, "<script>alert(1)</script>")])
    html = post(make_client(StubService(report))).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_home_page_has_optional_api_fields_with_labels(make_client):
    html = make_client(StubService()).get("/").get_data(as_text=True)
    for name in ["api_docs_url", "api_base_url", "api_paths"]:
        assert f'name="{name}"' in html and f'for="{name}"' in html
    assert "<textarea" in html


def test_api_fields_are_optional_and_default_to_empty(make_client):
    service = StubService(RunReport())
    assert post(make_client(service)).status_code == 200
    target = service.targets[0]
    assert target.api_docs_url is None and target.api_base_url is None
    assert target.api_paths == ()


def test_api_fields_are_passed_to_service_and_paths_are_split_by_lines(make_client):
    service = StubService(RunReport())
    response = post(
        make_client(service),
        api_docs_url="https://api.example.com/openapi.json",
        api_base_url="https://api.example.com",
        api_paths="/health\r\n\r\n  /items  \n/health\n",
    )
    assert response.status_code == 200
    target = service.targets[0]
    assert target.api_docs_url == "https://api.example.com/openapi.json"
    assert target.api_base_url == "https://api.example.com"
    assert target.api_paths == ("/health", "/items")


def test_invalid_api_path_is_rejected_next_to_field_and_input_is_kept(make_client):
    service = StubService(RunReport())
    response = post(make_client(service), api_paths="/health\nhealth")
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert 'id="api_paths_msg"' in html and 'role="alert"' in html
    assert "/health" in html and "health" in html
    assert service.targets == []


def test_too_many_api_paths_are_rejected(make_client):
    service = StubService(RunReport())
    paths = "\n".join(f"/p{number}" for number in range(11))
    response = post(make_client(service), api_paths=paths)
    assert response.status_code == 400
    assert "не более 10" in response.get_data(as_text=True)
    assert service.targets == []


def test_rejected_api_address_is_shown_at_its_own_field(make_client):
    service = StubService(error=TargetRejectedError("внутренний адрес", field="api_base_url"))
    response = post(make_client(service), api_base_url="http://127.0.0.1")
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert 'id="api_base_url_msg"' in html and "внутренний адрес" in html
    assert 'id="site_url_msg"' not in html
    assert "http://127.0.0.1" in html


def test_path_line_with_pipe_sets_expected_status(make_client):
    service = StubService(RunReport())
    response = post(make_client(service), api_paths="/ping | 201\n/items\n/a?x=1|200,404")
    assert response.status_code == 200
    target = service.targets[0]
    assert target.api_paths == ("/ping", "/items", "/a?x=1")
    assert target.api_expected_statuses == {"/ping": (201,), "/a?x=1": (200, 404)}


def test_bad_expected_status_is_reported_at_paths_field(make_client):
    service = StubService(RunReport())
    response = post(make_client(service), api_paths="/ping | abc")
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert 'id="api_paths_msg"' in html and "статус" in html
    assert "/ping | abc" in html
    assert service.targets == []


def test_form_hint_explains_expected_status_syntax(make_client):
    html = make_client(StubService()).get("/").get_data(as_text=True)
    assert "/ping | 201" in html


def test_app_refuses_to_start_with_bad_settings(monkeypatch):
    monkeypatch.setenv("QAXRAY_MAX_BYTES", "1")
    with pytest.raises(SettingsError, match="QAXRAY_MAX_BYTES"):
        create_app()


# POST/Redirect/GET: результат живёт по своему адресу


def test_successful_post_redirects_to_the_run_page_without_repeating_the_check(make_client):
    service = StubService(RunReport(results=[result("QC-001", CheckStatus.PASSED)]))
    client = make_client(service)
    response = post(client, follow=False)
    assert response.status_code == 303
    location = response.headers["Location"]
    assert location.startswith("/runs/")
    for _ in range(3):  # обновление страницы не запускает проверки заново
        page = client.get(location)
        assert page.status_code == 200 and "QC-001" in page.get_data(as_text=True)
    assert len(service.targets) == 1


def test_run_page_shows_the_address_and_summary_of_the_stored_run(make_client):
    report = RunReport(results=[result("QC-001", CheckStatus.PASSED)])
    client = make_client(StubService(report))
    html = post(client).get_data(as_text=True)
    assert "https://example.com" in html and "PASSED" in html


def test_run_is_stored_with_status_completed(make_client):
    store = InMemoryRunStore()
    client = make_client(StubService(RunReport(results=[])), store=store)
    location = post(client, follow=False).headers["Location"]
    record = store.get(location.rsplit("/", 1)[-1])
    assert record.status is RunStatus.COMPLETED and record.target.site_url == "https://example.com"


@pytest.mark.parametrize("run_id", ["x" * 24, "short", "a" * 100, "bad id!"])
def test_unknown_or_malformed_run_id_gives_404_page(make_client, run_id):
    response = make_client(StubService()).get(f"/runs/{run_id}")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert "не найден" in html and 'href="/"' in html


def test_path_traversal_in_run_id_is_not_found(make_client):
    assert make_client(StubService()).get("/runs/../../etc/passwd").status_code == 404


def test_expired_run_gives_404(make_client):
    from tests.unit.support import FakeClock

    clock = FakeClock()
    store = InMemoryRunStore(ttl_seconds=60, clock=clock)
    client = make_client(StubService(RunReport(results=[])), store=store)
    location = post(client, follow=False).headers["Location"]
    clock.now = 61
    assert client.get(location).status_code == 404


def test_rejected_address_does_not_leave_a_stored_run(make_client):
    store = InMemoryRunStore()
    service = StubService(error=TargetRejectedError("внутренний адрес"))
    response = post(make_client(service, store=store), site_url="http://127.0.0.1")
    assert response.status_code == 400
    assert store._runs == {}  # запуск не начинался


def test_internal_error_is_recorded_without_leaking_details(make_client):
    store = InMemoryRunStore()
    service = StubService(error=RuntimeError("секретная подробность"))
    client = make_client(service, store=store)
    response = post(client)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "внутренней ошибкой" in html and "секретная подробность" not in html
    only = next(iter(store._runs.values()))
    assert only.status is RunStatus.FAILED


def test_too_many_running_runs_are_refused_politely(make_client):
    store = InMemoryRunStore(max_runs=1)
    store.create(RunTarget(site_url="https://example.com/"))  # занимает единственное место
    response = post(make_client(StubService(RunReport(results=[])), store=store))
    assert response.status_code == 429
    assert "Слишком много" in response.get_data(as_text=True)
