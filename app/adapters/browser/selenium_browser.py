"""Адаптер порта браузера на Selenium и Chrome.

Защита: Chrome запускается через прокси-сторож (guard_proxy), поэтому все запросы страницы
проходят политику адресов. Скачивание файлов запрещено, профиль браузера временный, ожидания
явные (WebDriverWait), расширения и фоновые обращения выключены."""

import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait

from app.adapters.browser.guard_proxy import GuardProxy
from app.core.browser import (
    BrowserBlockedError,
    BrowserCertificateError,
    BrowserError,
    BrowserFactory,
    BrowserNetworkError,
    BrowserTimeoutError,
    BrowserUnavailableError,
    PageInfo,
    Viewport,
)
from app.core.security.policy import TargetPolicy, TargetRejectedError

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.1
# У страниц ошибок Chrome (сертификат, сеть) есть этот блок и элемент с кодом ошибки.
ERROR_PAGE_SCRIPT = (
    "const wrapper = document.querySelector('.interstitial-wrapper');"
    "const code = document.getElementById('error-code');"
    "return wrapper && code ? code.textContent.trim() : null;"
)
ALLOWED_SCHEMES = ("http", "https")
DriverFactory = Callable[[ChromeOptions], Any]
ProxyFactory = Callable[[TargetPolicy], Any]

HARDENING_ARGUMENTS = (
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-pings",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)


def _chrome_driver(options: ChromeOptions) -> Any:
    # Selenium Manager сам находит или загружает драйвер, подходящий установленному Chrome.
    return webdriver.Chrome(options=options, service=ChromeService(log_output=os.devnull))


def _short(error: BaseException) -> str:
    text = str(getattr(error, "msg", None) or error)
    first = text.removeprefix("Message: ").strip().splitlines()[0] if text.strip() else ""
    return first[:200] or type(error).__name__


class SeleniumBrowser:
    """Один Chrome, одна страница. Реализует порт BrowserPort."""

    def __init__(
        self,
        policy: TargetPolicy,
        *,
        page_timeout: float = 20.0,
        headless: bool = True,
        driver_factory: DriverFactory | None = None,
        proxy_factory: ProxyFactory = GuardProxy,
    ) -> None:
        self._policy = policy
        self._page_timeout = page_timeout
        self._closed = False
        self._downloads = tempfile.mkdtemp(prefix="qaxray-downloads-")
        self._proxy = proxy_factory(policy)
        try:
            port = self._proxy.start()
            self._driver = (driver_factory or _chrome_driver)(self._options(port, headless))
        except (WebDriverException, OSError) as error:
            self._cleanup()
            raise BrowserUnavailableError(f"Браузер не запустился: {_short(error)}") from error

    def _options(self, proxy_port: int, headless: bool) -> ChromeOptions:
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument(f"--proxy-server=http://127.0.0.1:{proxy_port}")
        options.add_argument("--proxy-bypass-list=<-loopback>")  # даже петля идёт через сторож
        for argument in HARDENING_ARGUMENTS:
            options.add_argument(argument)
        options.add_experimental_option(
            "prefs",
            {
                "download_restrictions": 3,  # скачивание запрещено полностью
                "download.prompt_for_download": False,
                "download.default_directory": self._downloads,
                "safebrowsing.enabled": False,
            },
        )
        return options

    def open(self, url: str, viewport: Viewport) -> PageInfo:
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        self._guard(url)
        started = time.monotonic()
        try:
            self._driver.set_window_rect(width=viewport.width, height=viewport.height)
            # Headless-Chrome не сужает окно меньше 500 px, поэтому ширина задаётся эмуляцией.
            self._driver.execute_cdp_cmd(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": viewport.width,
                    "height": viewport.height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            self._driver.set_page_load_timeout(self._page_timeout)
            self._driver.get(url)
            WebDriverWait(self._driver, self._page_timeout, poll_frequency=POLL_SECONDS).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException as error:
            raise BrowserTimeoutError(
                f"Страница не загрузилась за {self._page_timeout:g} с"
            ) from error
        except WebDriverException as error:
            raise self._classify(url, error) from error
        self._raise_for_error_page()
        final_url = str(self._driver.current_url)
        if urlsplit(final_url).scheme not in ALLOWED_SCHEMES:
            raise BrowserNetworkError("Страница не загрузилась: браузер показал страницу ошибки")
        return PageInfo(
            url=final_url,
            title=str(self._driver.title),
            load_ms=int((time.monotonic() - started) * 1000),
        )

    def _raise_for_error_page(self) -> None:
        """Chrome показывает ошибки как обычную страницу, поэтому распознаём их по разметке."""
        code = self._driver.execute_script(ERROR_PAGE_SCRIPT)
        if not code:
            return
        if "CERT" in str(code) or "SSL" in str(code):
            raise BrowserCertificateError(f"Сертификат сайта не принят браузером ({code})")
        raise BrowserNetworkError(f"Браузер показал страницу ошибки ({code})")

    def _guard(self, url: str) -> None:
        """Адрес проходит политику до навигации: запрещённый в браузер не попадает."""
        if urlsplit(url).scheme.lower() not in ALLOWED_SCHEMES:
            raise BrowserBlockedError("Разрешены только адреса http и https")
        try:
            self._policy.validate(url)
        except TargetRejectedError as error:
            raise BrowserBlockedError(str(error)) from error

    def _classify(self, url: str, error: WebDriverException) -> BrowserError:
        message = _short(error)
        host = (urlsplit(url).hostname or "").lower()
        if host and any(entry.startswith(f"{host}:") for entry in self._proxy.blocked):
            return BrowserBlockedError(f"Адрес {host} запрещён политикой безопасности")
        if "net::ERR_" in message:
            return BrowserNetworkError(f"Сетевой сбой: {message}")
        return BrowserError(f"Сбой браузера: {message}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._driver.quit()
        except (WebDriverException, OSError) as error:
            logger.warning("Браузер не закрылся штатно: %s", type(error).__name__)
        self._cleanup()

    def _cleanup(self) -> None:
        self._proxy.stop()
        shutil.rmtree(self._downloads, ignore_errors=True)


def selenium_browser_factory(
    policy: TargetPolicy, *, page_timeout: float, headless: bool
) -> BrowserFactory:
    """Фабрика для сессии запуска: браузер создаётся лениво, при первой браузерной проверке."""

    def create() -> SeleniumBrowser:
        return SeleniumBrowser(policy, page_timeout=page_timeout, headless=headless)

    return create
