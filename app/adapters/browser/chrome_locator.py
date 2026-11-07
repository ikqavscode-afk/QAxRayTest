"""Определяет, установлен ли Chrome. Нужен, чтобы отличить «Chrome нет» от «Chrome не запустился».

Ошибка Selenium «Unable to obtain driver» одинакова и без Chrome, и без сети для загрузки драйвера,
поэтому по тексту ошибки их не различить: проверяем стандартные места установки."""

import os
import shutil
import sys
from collections.abc import Callable, Mapping

ENV_VARIABLES = ("CHROME_BIN", "SE_BROWSER_PATH", "SE_CHROME_PATH")
MAC_BINARIES = (
    "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "Applications/Chromium.app/Contents/MacOS/Chromium",
)
LINUX_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)
WINDOWS_BASES = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
WINDOWS_TAIL = "\\Google\\Chrome\\Application\\chrome.exe"


def chrome_installed(
    *,
    environ: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.exists,
    which: Callable[[str], str | None] = shutil.which,
    platform: str = sys.platform,
) -> bool:
    env = os.environ if environ is None else environ
    for variable in ENV_VARIABLES:
        path = env.get(variable)
        if path and exists(path):
            return True
    if platform == "darwin":
        home = env.get("HOME", "")
        candidates = [f"/{path}" for path in MAC_BINARIES]
        if home:
            candidates += [f"{home}/{path}" for path in MAC_BINARIES]
        return any(exists(path) for path in candidates)
    if platform.startswith("win"):
        paths = [f"{env[base]}{WINDOWS_TAIL}" for base in WINDOWS_BASES if env.get(base)]
        return any(exists(path) for path in paths)
    return any(which(name) for name in LINUX_NAMES)
