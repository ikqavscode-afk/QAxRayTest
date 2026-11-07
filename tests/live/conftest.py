import os

import pytest

SKIP_REASON = "живые тесты выключены: задайте QAXRAY_LIVE=1 и QAXRAY_LIVE_TARGETS=путь/к/файлу.json"


def live_enabled() -> bool:
    return os.environ.get("QAXRAY_LIVE") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Без явного включения живые тесты пропускаются: ни локально, ни в CI сеть не нужна."""
    if live_enabled():
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
