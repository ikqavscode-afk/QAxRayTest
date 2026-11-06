import pytest
from pydantic import ValidationError

from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus


def make(**overrides):
    data = {
        "check_id": "QC-001",
        "title": "Главная отвечает в пределах тайм-аута",
        "status": CheckStatus.PASSED,
        "severity": Severity.HIGH,
        "expected": "Ответ получен за 10 с",
        "actual": "Ответ получен за 0.4 с",
    }
    data.update(overrides)
    return CheckResult(**data)


def test_valid_result_is_created():
    result = make()
    assert result.status is CheckStatus.PASSED
    assert result.reason is None
    assert result.evidence == []


@pytest.mark.parametrize("status", [CheckStatus.WARNING, CheckStatus.SKIPPED, CheckStatus.BLOCKED])
def test_status_that_requires_reason_without_reason_is_rejected(status):
    with pytest.raises(ValidationError):
        make(status=status)


@pytest.mark.parametrize("status", [CheckStatus.WARNING, CheckStatus.SKIPPED, CheckStatus.BLOCKED])
def test_status_that_requires_reason_with_reason_is_accepted(status):
    assert make(status=status, reason="Формы на странице нет").reason == "Формы на странице нет"


def test_blank_reason_is_rejected():
    with pytest.raises(ValidationError):
        make(status=CheckStatus.SKIPPED, reason="   ")


@pytest.mark.parametrize("check_id", ["QC-1", "QC-0001", "X-001", "qc-001", ""])
def test_invalid_check_id_is_rejected(check_id):
    with pytest.raises(ValidationError):
        make(check_id=check_id)


def test_result_is_immutable():
    result = make()
    with pytest.raises(ValidationError):
        result.status = CheckStatus.FAILED


def test_evidence_list_is_not_shared_between_results():
    first, second = make(), make()
    first.evidence.append("фрагмент ответа")
    assert second.evidence == []


def test_result_serializes_to_plain_json_values():
    data = make().model_dump(mode="json")
    assert data["status"] == "PASSED"
    assert data["severity"] == "HIGH"
