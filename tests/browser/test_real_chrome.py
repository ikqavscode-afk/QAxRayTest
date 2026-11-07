"""Проверки с настоящим Chrome: сетевой доступ, безопасность навигации, точная ширина окна.

    QAXRAY_BROWSER=1 python -m pytest tests/browser -v

Обращаются только к нейтральным открытым адресам (example.com и сервис для проверки сертификатов)."""

import socket
import threading
import time

import pytest

from app.adapters.browser.selenium_browser import SeleniumBrowser
from app.core.browser import (
    DESKTOP,
    MOBILE,
    BrowserBlockedError,
    BrowserCertificateError,
)
from app.core.security.policy import TargetPolicy

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser():
    instance = SeleniumBrowser(TargetPolicy(), page_timeout=30, headless=True)
    yield instance
    instance.close()


def script(browser, code):
    return browser._driver.execute_script(code)  # доступ к драйверу только в этом тесте


@pytest.mark.parametrize("viewport", [DESKTOP, MOBILE])
def test_page_opens_with_the_exact_requested_width(browser, viewport):
    info = browser.open("https://example.com/", viewport)
    assert info.url.startswith("https://example.com")
    assert script(browser, "return innerWidth") == viewport.width


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localtest.me/", "file:///etc/passwd", "http://169.254.169.254/"],
)
def test_internal_and_non_http_addresses_never_reach_the_browser(browser, url):
    with pytest.raises(BrowserBlockedError):
        browser.open(url, DESKTOP)


def test_page_script_cannot_reach_a_local_service_through_the_browser(browser):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(0.2)
    hits = []

    def accept():
        end = time.time() + 15
        while time.time() < end:
            try:
                connection, _ = server.accept()
                hits.append(1)
                connection.close()
            except OSError:
                pass

    threading.Thread(target=accept, daemon=True).start()
    try:
        browser.open("http://example.com/", DESKTOP)
        port = server.getsockname()[1]
        browser._driver.set_script_timeout(10)
        browser._driver.execute_async_script(
            "const done = arguments[arguments.length - 1];"
            f"fetch('http://127.0.0.1:{port}/', {{mode: 'no-cors'}}).then(done, done);"
        )
        time.sleep(1)
        assert hits == []  # сторож не пропустил соединение
        assert any(entry.startswith("127.0.0.1:") for entry in browser._proxy.blocked)
    finally:
        server.close()


def test_expired_certificate_is_reported_as_a_certificate_error(browser):
    with pytest.raises(BrowserCertificateError, match="CERT"):
        browser.open("https://expired.badssl.com/", DESKTOP)
