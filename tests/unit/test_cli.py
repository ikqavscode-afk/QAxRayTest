from app.bootstrap import build_run_service
from app.cli import format_report, main
from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.services.run_service import RunService


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


class StubService:
    def __init__(self, report=None, error=None):
        self.report = report
        self.error = error
        self.targets = []

    def run(self, target):
        self.targets.append(target)
        if self.error:
            raise self.error
        return self.report


def report_of(*results):
    return RunReport(results=list(results))


def test_report_text_lists_every_check_with_status_and_title():
    text = format_report(
        report_of(result("QC-001", CheckStatus.PASSED), result("QC-002", CheckStatus.FAILED))
    )
    assert "QC-001" in text and "PASSED" in text and "Проверка QC-001" in text
    assert "QC-002" in text and "FAILED" in text


def test_report_text_shows_reason_for_warning():
    text = format_report(report_of(result("QC-003", CheckStatus.WARNING, "сайт открыт по http")))
    assert "сайт открыт по http" in text


def test_report_text_ends_with_summary_counts():
    text = format_report(
        report_of(
            result("QC-001", CheckStatus.PASSED),
            result("QC-002", CheckStatus.PASSED),
            result("QC-003", CheckStatus.FAILED),
        )
    )
    assert "PASSED: 2" in text
    assert "FAILED: 1" in text
    assert "BLOCKED: 0" in text


def test_main_returns_zero_when_nothing_failed_or_blocked(capsys):
    service = StubService(report_of(result("QC-001", CheckStatus.PASSED)))
    assert main(["https://example.com"], service=service) == 0
    assert [t.site_url for t in service.targets] == ["https://example.com"]
    assert "QC-001" in capsys.readouterr().out


def test_main_returns_one_when_check_failed():
    service = StubService(report_of(result("QC-001", CheckStatus.FAILED)))
    assert main(["https://example.com"], service=service) == 1


def test_main_returns_one_when_check_blocked():
    service = StubService(report_of(result("QC-001", CheckStatus.BLOCKED, "нет сети")))
    assert main(["https://example.com"], service=service) == 1


def test_main_returns_two_and_explains_when_address_is_rejected(capsys):
    service = StubService(error=TargetRejectedError("внутренний адрес"))
    assert main(["http://127.0.0.1"], service=service) == 2
    assert "внутренний адрес" in capsys.readouterr().out


def test_bootstrap_builds_real_service_with_group_a_checks():
    service = build_run_service()
    assert isinstance(service, RunService)


def test_main_passes_api_options_to_service():
    service = StubService(report_of(result("QC-001", CheckStatus.PASSED)))
    argv = [
        "https://example.com",
        "--api-docs",
        "https://api.example.com/openapi.json",
        "--api-base",
        "https://api.example.com",
        "--api-path",
        "/health",
        "--api-path",
        "/items",
    ]
    assert main(argv, service=service) == 0
    target = service.targets[0]
    assert target.api_docs_url == "https://api.example.com/openapi.json"
    assert target.api_base_url == "https://api.example.com"
    assert target.api_paths == ("/health", "/items")


def test_main_without_api_options_leaves_them_empty():
    service = StubService(report_of(result("QC-001", CheckStatus.PASSED)))
    main(["https://example.com"], service=service)
    target = service.targets[0]
    assert target.api_docs_url is None and target.api_base_url is None
    assert target.api_paths == ()


def test_main_returns_two_for_invalid_api_path_and_runs_nothing(capsys):
    service = StubService(report_of())
    assert main(["https://example.com", "--api-path", "health"], service=service) == 2
    assert service.targets == []
    assert "--api-path" in capsys.readouterr().out
