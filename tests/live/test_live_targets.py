"""Живые проверки по целям из внешнего файла. Выключены по умолчанию.

    QAXRAY_LIVE=1 QAXRAY_LIVE_TARGETS=/путь/к/targets.json python -m pytest tests/live -v

Один запуск на цель, между целями пауза, выполняются только безопасные GET."""

import os
import time
from pathlib import Path

import pytest

from app.bootstrap import build_run_service
from app.core.security.policy import TargetRejectedError
from tests.live.conftest import live_enabled
from tests.live.loader import LiveCase, LiveTargetsError, load_cases

PAUSE_SECONDS = 1.5
EXPECTED_CHECKS = 16

pytestmark = pytest.mark.live


def discover_cases() -> list[LiveCase | None]:
    if not live_enabled():
        return [None]  # заглушка: тест будет пропущен хуком из conftest.py
    path = os.environ.get("QAXRAY_LIVE_TARGETS")
    if not path:
        raise pytest.UsageError("QAXRAY_LIVE=1 требует QAXRAY_LIVE_TARGETS=путь/к/файлу.json")
    try:
        return list(load_cases(Path(path)))
    except LiveTargetsError as error:
        raise pytest.UsageError(str(error)) from error


@pytest.mark.parametrize("case", discover_cases(), ids=lambda c: c.name if c else "выключено")
def test_live_target(case: LiveCase) -> None:
    time.sleep(PAUSE_SECONDS)
    service = build_run_service()

    if case.rejected:
        with pytest.raises(TargetRejectedError):
            service.run(case.target)
        return

    report = service.run(case.target)
    by_id = {result.check_id: result for result in report.results}

    assert len(report.results) == EXPECTED_CHECKS
    assert len(by_id) == EXPECTED_CHECKS  # идентификаторы не повторяются

    # Параметры запроса (в них бывают токены) не должны попасть в отчёт.
    text = report.model_dump_json()
    for path in case.target.api_paths:
        if "?" in path:
            assert path.split("?", 1)[1] not in text

    for check_id, expected in case.expect.items():
        actual = by_id[check_id]
        assert actual.status is expected, (
            f"{check_id}: ждали {expected.value}, получили {actual.status.value} "
            f"({actual.reason or actual.actual})"
        )
