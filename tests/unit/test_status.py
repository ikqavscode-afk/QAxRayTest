import pytest

from app.schemas.status import CheckStatus


def test_all_statuses_from_adr_exist():
    assert {s.value for s in CheckStatus} == {"PASSED", "FAILED", "WARNING", "SKIPPED", "BLOCKED"}


@pytest.mark.parametrize("status", [CheckStatus.WARNING, CheckStatus.SKIPPED, CheckStatus.BLOCKED])
def test_reason_is_required(status):
    assert status.requires_reason is True


@pytest.mark.parametrize("status", [CheckStatus.PASSED, CheckStatus.FAILED])
def test_reason_is_not_required(status):
    assert status.requires_reason is False
