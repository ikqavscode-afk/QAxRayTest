import os

import pytest

SKIP_REASON = "браузерные тесты выключены: задайте QAXRAY_BROWSER=1 (нужны Chrome и сеть)"


def browser_enabled() -> bool:
    return os.environ.get("QAXRAY_BROWSER") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Без явного включения тесты с настоящим Chrome пропускаются, в том числе в обычном CI."""
    if browser_enabled():
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)
