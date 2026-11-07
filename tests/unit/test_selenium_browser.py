"""Адаптер Selenium проверяется на поддельном драйвере: настоящий браузер здесь не нужен."""

import os

import pytest
from selenium.common.exceptions import TimeoutException, WebDriverException

from app.adapters.browser.selenium_browser import SeleniumBrowser
from app.core.browser import (
    DESKTOP,
    MOBILE,
    BrowserBlockedError,
    BrowserCertificateError,
    BrowserError,
    BrowserNetworkError,
    BrowserTimeoutError,
    BrowserUnavailableError,
)
from app.core.security.policy import TargetPolicy

PUBLIC = "93.184.216.34"
URL = "https://site.test/"
POLICY = TargetPolicy(resolver=lambda host: ["127.0.0.1"] if "internal" in host else [PUBLIC])


class FakeProxy:
    def __init__(self, policy=None):
        self.port = 43210
        self.blocked = []
        self.stopped = 0

    def start(self):
        return self.port

    def stop(self):
        self.stopped += 1


class FakeDriver:
    def __init__(self, options=None):
        self.options = options
        self.get_calls = []
        self.rect = None
        self.page_load_timeout = None
        self.current_url = URL
        self.title = "Тест"
        self.ready_state = "complete"
        self.get_error = None
        self.quit_error = None
        self.quit_calls = 0
        self.error_code = None  # код страницы ошибки Chrome (None: обычная страница)
        self.cdp_calls = []

    def get(self, url):
        self.get_calls.append(url)
        if self.get_error:
            raise self.get_error

    def set_window_rect(self, width=None, height=None, x=None, y=None):
        self.rect = (width, height)

    def set_page_load_timeout(self, seconds):
        self.page_load_timeout = seconds

    def execute_script(self, script, *args):
        if "error-code" in script:
            return self.error_code
        return self.ready_state

    def execute_cdp_cmd(self, command, params):
        self.cdp_calls.append((command, params))

    def quit(self):
        self.quit_calls += 1
        if self.quit_error:
            raise self.quit_error


def make(**kwargs):
    proxy = FakeProxy()
    drivers = []

    def driver_factory(options):
        driver = FakeDriver(options)
        drivers.append(driver)
        return driver

    browser = SeleniumBrowser(
        POLICY,
        page_timeout=kwargs.pop("page_timeout", 5.0),
        driver_factory=kwargs.pop("driver_factory", driver_factory),
        proxy_factory=lambda policy: proxy,
        **kwargs,
    )
    return browser, proxy, drivers


# Запуск браузера


def test_browser_starts_through_the_guard_proxy_with_hardened_options():
    _, proxy, drivers = make()
    arguments = drivers[0].options.arguments
    assert f"--proxy-server=http://127.0.0.1:{proxy.port}" in arguments
    assert "--proxy-bypass-list=<-loopback>" in arguments  # даже петля идёт через сторож
    assert "--headless=new" in arguments
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in arguments
    assert "--disable-extensions" in arguments


def test_headed_mode_omits_the_headless_flag():
    _, _, drivers = make(headless=False)
    assert "--headless=new" not in drivers[0].options.arguments


def test_downloads_are_restricted_and_directed_to_a_private_folder():
    _, _, drivers = make()
    prefs = drivers[0].options.experimental_options["prefs"]
    assert prefs["download_restrictions"] == 3
    assert prefs["download.prompt_for_download"] is False
    assert os.path.isdir(prefs["download.default_directory"])


def test_driver_start_failure_is_reported_and_proxy_is_stopped():
    proxy = FakeProxy()

    def broken(options):
        raise WebDriverException("Message: session not created: версия драйвера")

    with pytest.raises(BrowserUnavailableError, match="session not created"):
        SeleniumBrowser(POLICY, driver_factory=broken, proxy_factory=lambda policy: proxy)
    assert proxy.stopped == 1


# Открытие страницы


def test_open_sets_window_and_timeout_navigates_and_reports_the_page():
    browser, _, drivers = make(page_timeout=7)
    driver = drivers[0]
    driver.current_url, driver.title = "https://site.test/ru/", "Заголовок"
    info = browser.open(URL, MOBILE)
    assert driver.rect == (MOBILE.width, MOBILE.height)
    assert driver.page_load_timeout == 7
    assert driver.get_calls == [URL]
    assert (info.url, info.title) == ("https://site.test/ru/", "Заголовок")
    assert info.load_ms >= 0


def test_internal_address_is_refused_before_navigation():
    browser, _, drivers = make()
    with pytest.raises(BrowserBlockedError):
        browser.open("http://internal.test/", DESKTOP)
    assert drivers[0].get_calls == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://site.test/", "chrome://settings"])
def test_non_http_schemes_are_refused_before_navigation(url):
    browser, _, drivers = make()
    with pytest.raises(BrowserBlockedError):
        browser.open(url, DESKTOP)
    assert drivers[0].get_calls == []


def test_load_timeout_becomes_a_timeout_error():
    browser, _, drivers = make()
    drivers[0].get_error = TimeoutException("Message: timeout")
    with pytest.raises(BrowserTimeoutError):
        browser.open(URL, DESKTOP)


def test_page_that_never_becomes_ready_becomes_a_timeout_error():
    browser, _, drivers = make(page_timeout=0.3)
    drivers[0].ready_state = "loading"
    with pytest.raises(BrowserTimeoutError):
        browser.open(URL, DESKTOP)


@pytest.mark.parametrize(
    "message",
    ["unknown error: net::ERR_CONNECTION_REFUSED", "unknown error: net::ERR_NAME_NOT_RESOLVED"],
)
def test_network_failures_are_network_errors(message):
    browser, _, drivers = make()
    drivers[0].get_error = WebDriverException(f"Message: {message}")
    with pytest.raises(BrowserNetworkError):
        browser.open(URL, DESKTOP)


def test_failure_caused_by_the_guard_proxy_is_reported_as_blocked_not_retryable_network():
    browser, proxy, drivers = make()
    drivers[0].get_error = WebDriverException(
        "Message: unknown error: net::ERR_TUNNEL_CONNECTION_FAILED"
    )
    proxy.blocked.append("site.test:443")
    with pytest.raises(BrowserBlockedError):
        browser.open(URL, DESKTOP)


def test_other_driver_failures_are_plain_browser_errors_not_network_errors():
    browser, _, drivers = make()
    drivers[0].get_error = WebDriverException("Message: chrome not reachable")
    with pytest.raises(BrowserError) as caught:
        browser.open(URL, DESKTOP)
    assert not isinstance(caught.value, BrowserNetworkError)


def test_error_page_instead_of_the_site_is_a_network_error():
    browser, _, drivers = make()
    drivers[0].current_url = "chrome-error://chromewebdata/"
    with pytest.raises(BrowserNetworkError):
        browser.open(URL, DESKTOP)


# Закрытие


def test_close_quits_the_driver_stops_the_proxy_and_removes_the_download_folder():
    browser, proxy, drivers = make()
    folder = drivers[0].options.experimental_options["prefs"]["download.default_directory"]
    browser.close()
    browser.close()
    assert drivers[0].quit_calls == 1 and proxy.stopped == 1
    assert not os.path.exists(folder)


def test_driver_quit_failure_does_not_prevent_cleanup():
    browser, proxy, drivers = make()
    drivers[0].quit_error = WebDriverException("уже закрыт")
    browser.close()
    assert proxy.stopped == 1


def test_open_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError, match="закрыт"):
        browser.open(URL, DESKTOP)


# Точная ширина окна: в headless-режиме Chrome не сужает окно меньше 500 px


def test_exact_viewport_is_emulated_so_narrow_widths_are_really_narrow():
    browser, _, drivers = make()
    browser.open(URL, MOBILE)
    assert drivers[0].cdp_calls == [
        (
            "Emulation.setDeviceMetricsOverride",
            {"width": 375, "height": 812, "deviceScaleFactor": 1, "mobile": False},
        )
    ]


def test_each_open_applies_the_requested_viewport():
    browser, _, drivers = make()
    browser.open(URL, DESKTOP)
    browser.open(URL, MOBILE)
    widths = [params["width"] for _, params in drivers[0].cdp_calls]
    assert widths == [1280, 375]


# Страницы ошибок Chrome выглядят как обычная загрузка: распознаём их сами


@pytest.mark.parametrize("code", ["NET::ERR_CERT_DATE_INVALID", "NET::ERR_CERT_AUTHORITY_INVALID"])
def test_certificate_error_page_is_a_certificate_error_and_not_retryable(code):
    browser, _, drivers = make()
    drivers[0].error_code = code
    with pytest.raises(BrowserCertificateError, match=code):
        browser.open(URL, DESKTOP)
    assert not issubclass(BrowserCertificateError, BrowserNetworkError)


def test_other_chrome_error_page_is_a_network_error():
    browser, _, drivers = make()
    drivers[0].error_code = "ERR_CONNECTION_REFUSED"
    with pytest.raises(BrowserNetworkError, match="ERR_CONNECTION_REFUSED"):
        browser.open(URL, DESKTOP)


def test_normal_page_is_not_mistaken_for_an_error_page():
    browser, _, _ = make()
    assert browser.open(URL, DESKTOP).title == "Тест"
