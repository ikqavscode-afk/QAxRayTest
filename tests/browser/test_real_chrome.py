"""Проверки с настоящим Chrome: сетевой доступ, безопасность навигации, точная ширина окна.

    QAXRAY_BROWSER=1 python -m pytest tests/browser -v

Обращаются только к нейтральным открытым адресам (example.com и сервис для проверки сертификатов)."""

import socket
import threading
import time

import pytest
from selenium.webdriver.support.ui import WebDriverWait

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


GOOD_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEklEQVR4nGP4z8CAFWEXHbQSACj/P8Fu7N9hAAAAAElFTkSuQmCC"
INJECT_IMAGES = """
document.body.insertAdjacentHTML('beforeend', `
  <img id="good" src="${arguments[0]}" width="40" height="40">
  <img id="broken" src="https://example.com/qaxray-missing-image.png?token=SECRET" width="40" height="40">
  <img id="hidden" src="${arguments[0]}" width="40" height="40" style="display:none">
  <img id="pixel" src="${arguments[0]}" width="1" height="1">
  <div style="height:6000px"></div>
  <img id="lazy" loading="lazy" src="https://example.com/qaxray-lazy-missing.png" width="40" height="40">
`);
"""


def test_images_probe_tells_loaded_broken_lazy_and_ignores_hidden_and_tiny(browser):
    browser.open("https://example.com/", DESKTOP)
    driver = browser._driver
    driver.execute_script(INJECT_IMAGES, GOOD_PNG)
    WebDriverWait(driver, 15).until(
        lambda d: d.execute_script(
            "return ['good', 'broken'].every(id => document.getElementById(id).complete)"
        )
    )
    snapshot = browser.images(30)
    by_name = {item.src.rsplit("/", 1)[-1]: item for item in snapshot.items}
    assert snapshot.total == 3  # good, broken, lazy; скрытое и пиксель 1x1 не считаются
    good = next(item for item in snapshot.items if item.src.startswith("data:image/png"))
    assert good.complete and good.natural_width == 8
    broken = by_name["qaxray-missing-image.png"]
    assert broken.complete and broken.natural_width == 0
    assert "SECRET" not in broken.src  # параметры запроса в отчёт не попадают
    assert by_name["qaxray-lazy-missing.png"].lazy


def test_overflow_probe_reports_wide_content_and_ignores_scrollable_containers(browser):
    browser.open("https://example.com/", MOBILE)
    assert browser.overflow().scroll_width <= browser.overflow().viewport_width + 1

    driver = browser._driver
    driver.execute_script(
        "document.body.insertAdjacentHTML('beforeend', "
        '\'<div id="qax-scroll" style="width:300px;overflow-x:auto">'
        '<div style="width:2000px;height:10px"></div></div>\');'
    )
    contained = browser.overflow()  # широкое содержимое внутри прокручиваемого блока не в счёт
    assert contained.scroll_width <= contained.viewport_width + 1 and contained.offenders == ()

    driver.execute_script(
        "document.body.insertAdjacentHTML('beforeend', "
        '\'<div id="qax-wide" class="banner x" style="width:1200px;height:10px"></div>\');'
    )
    wide = browser.overflow()
    assert wide.viewport_width == MOBILE.width
    assert wide.scroll_width >= 1200
    assert "div#qax-wide.banner" in wide.offenders


INJECT_BUTTONS = """
document.body.insertAdjacentHTML('beforeend', `
  <button id="text">Отправить</button>
  <button id="aria" aria-label="Закрыть"></button>
  <span id="lbl">Меню</span><button id="by" aria-labelledby="lbl"></button>
  <button id="titled" title="Помощь"></button>
  <button id="svgname"><svg width="10" height="10"><title>Поиск</title><path d="M0 0h10v10z"/></svg></button>
  <button id="srtext"><span style="position:absolute;clip:rect(0 0 0 0)">Корзина</span></button>
  <button id="icon"><svg width="10" height="10"><path d="M0 0h10v10z"/></svg></button>
  <button id="empty"></button>
  <input type="submit" id="sub">
  <input type="button" id="plain">
  <input type="image" id="img" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
  <div role="button" id="role">Раскрыть</div>
  <button id="hidden" style="display:none"></button>
  <div aria-hidden="true"><button id="ariahidden"></button></div>
`);
"""


def test_buttons_probe_computes_accessible_name_sources_without_page_text(browser):
    browser.open("https://example.com/", DESKTOP)
    browser._driver.execute_script(INJECT_BUTTONS)
    snapshot = browser.buttons(50)
    by_id = {item.descriptor.split("#")[-1]: item.name_source for item in snapshot.items}
    assert by_id["text"] == "text" and by_id["aria"] == "aria-label"
    assert by_id["by"] == "aria-labelledby" and by_id["titled"] == "title"
    assert by_id["svgname"] in ("text", "svg-title")  # имя из <title> значка есть
    assert by_id["srtext"] == "text"  # текст только для программ чтения тоже имя
    assert by_id["sub"] == "default" and by_id["role"] == "text"
    for unnamed in ("icon", "empty", "plain", "img"):
        assert by_id[unnamed] is None, unnamed
    assert "hidden" not in by_id and "ariahidden" not in by_id  # скрытые не считаются
    assert not any("Отправить" in repr(item) for item in snapshot.items)  # текст не собирается


INJECT_FIELDS = """
document.body.insertAdjacentHTML('beforeend', `
  <form>
    <label for="fa">Имя</label><input id="fa">
    <label>Почта <input id="fb" type="email"></label>
    <input id="fc" aria-label="Поиск">
    <span id="fl">Город</span><input id="fd" aria-labelledby="fl">
    <input id="fe" placeholder="Телефон">
    <input id="ff" title="Фамилия">
    <input id="fg">
    <select id="fh"><option>1</option></select>
    <textarea id="fi"></textarea>
    <label for="fj"></label><input id="fj">
    <label><input id="fm" type="checkbox"> Согласен</label>
    <input type="hidden" id="fk"><input type="submit" id="fs" value="Go">
    <input id="fx" style="display:none">
  </form>
`);
"""


def test_form_fields_probe_finds_label_sources_without_page_text(browser):
    browser.open("https://example.com/", DESKTOP)
    browser._driver.execute_script(INJECT_FIELDS)
    snapshot = browser.form_fields(50)
    by_id = {item.descriptor.split("#")[-1]: item.name_source for item in snapshot.items}
    assert by_id["fa"] == "label-for" and by_id["fb"] == "label-wrap"
    assert by_id["fm"] == "label-wrap"
    assert by_id["fc"] == "aria-label" and by_id["fd"] == "aria-labelledby"
    assert by_id["fe"] == "placeholder" and by_id["ff"] == "title"
    for unlabeled in ("fg", "fh", "fi", "fj"):
        assert by_id[unlabeled] is None, unlabeled
    assert "fk" not in by_id and "fs" not in by_id and "fx" not in by_id
    assert not any("Имя" in repr(item) for item in snapshot.items)  # текст не собирается
