"""Адаптер Selenium проверяется на поддельном драйвере: настоящий браузер здесь не нужен."""

import os

import pytest
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.keys import Keys

from app.adapters.browser.selenium_browser import SeleniumBrowser
from app.core.browser import (
    DESKTOP,
    MOBILE,
    BrowserBlockedError,
    BrowserCertificateError,
    BrowserError,
    BrowserNetworkError,
    BrowserNotInstalledError,
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


class FakeActiveElement:
    def __init__(self, driver):
        self.driver = driver

    def send_keys(self, *keys):
        self.driver.sent_keys.extend(keys)


class FakeSwitchTo:
    def __init__(self, driver):
        self.active_element = FakeActiveElement(driver)


class FakeDriver:
    def __init__(self, options=None):
        self.switch_to = FakeSwitchTo(self)
        self.sent_keys = []
        self.tab_start = {"interactiveTotal": 0}
        self.tab_steps = []
        self.tab_error = None
        self.rendered_result = None
        self.rendered_error = None
        self.rendered_ready = True
        self.rendered_waits = 0
        self.auth_result = None
        self.auth_args = None
        self.auth_error = None
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
        self.images_result = None
        self.images_args = None
        self.images_error = None
        self.overflow_result = None
        self.fields_result = None
        self.fields_args = None
        self.fields_error = None
        self.buttons_result = None
        self.buttons_args = None
        self.buttons_error = None
        self.overflow_error = None

    def get(self, url):
        self.get_calls.append(url)
        if self.get_error:
            raise self.get_error

    def set_window_rect(self, width=None, height=None, x=None, y=None):
        self.rect = (width, height)

    def set_page_load_timeout(self, seconds):
        self.page_load_timeout = seconds

    def execute_script(self, script, *args):
        if "/*rendered-ready*/" in script:
            self.rendered_waits += 1
            return self.rendered_ready
        if "/*rendered*/" in script:
            if self.rendered_error:
                raise self.rendered_error
            return self.rendered_result
        if "/*auth*/" in script:
            if self.auth_error:
                raise self.auth_error
            self.auth_args = args
            return self.auth_result
        if "/*tab-start*/" in script:
            if self.tab_error:
                raise self.tab_error
            return self.tab_start
        if "/*tab-step*/" in script:
            return self.tab_steps.pop(0) if self.tab_steps else None
        if "el.labels" in script:
            if self.fields_error:
                raise self.fields_error
            self.fields_args = args
            return self.fields_result
        if "aria-labelledby" in script:
            if self.buttons_error:
                raise self.buttons_error
            self.buttons_args = args
            return self.buttons_result
        if "scrollWidth" in script:
            if self.overflow_error:
                raise self.overflow_error
            return self.overflow_result
        if "document.images" in script:
            if self.images_error:
                raise self.images_error
            self.images_args = args
            return self.images_result
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


# Проба images: факты о видимых изображениях без секретов


def probe_result(*items, total=None):
    return {"total": len(items) if total is None else total, "items": list(items)}


def raw(src, complete=True, width=100, lazy=False):
    return {"src": src, "complete": complete, "naturalWidth": width, "lazy": lazy}


def test_images_probe_returns_typed_facts_and_passes_the_limit_to_the_script():
    browser, _, drivers = make()
    drivers[0].images_result = probe_result(raw("https://cdn.example.com/a.png"), total=40)
    snapshot = browser.images(30)
    assert drivers[0].images_args == (30,)
    assert snapshot.total == 40
    assert snapshot.items[0].src == "https://cdn.example.com/a.png"
    assert snapshot.items[0].natural_width == 100 and snapshot.items[0].complete


def test_images_probe_drops_query_strings_fragments_and_data_payloads():
    browser, _, drivers = make()
    drivers[0].images_result = probe_result(
        raw("https://cdn.example.com/a.png?token=SECRET#frag"),
        raw("data:image/png;base64,AAAA" + "B" * 5000),
    )
    sources = [item.src for item in browser.images(30).items]
    assert sources == ["https://cdn.example.com/a.png", "data:image/png"]
    assert all("SECRET" not in source for source in sources)


def test_images_probe_tolerates_malformed_results():
    browser, _, drivers = make()
    for bad in (None, "text", {"total": "x", "items": "y"}, {"items": [1, None, {"src": 5}]}):
        drivers[0].images_result = bad
        snapshot = browser.images(30)
        assert snapshot.items == () or all(item.src for item in snapshot.items)


def test_images_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].images_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.images(30)


def test_images_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.images(30)


# Проба overflow: ширина окна, ширина содержимого и «виновники» без текста страницы


def test_overflow_probe_returns_typed_facts():
    browser, _, drivers = make()
    drivers[0].overflow_result = {
        "viewportWidth": 375,
        "scrollWidth": 595,
        "offenders": ["div#hero", "table.data"],
    }
    info = browser.overflow()
    assert (info.viewport_width, info.scroll_width) == (375, 595)
    assert info.offenders == ("div#hero", "table.data")


def test_overflow_offenders_are_cleaned_and_limited():
    browser, _, drivers = make()
    drivers[0].overflow_result = {
        "viewportWidth": 375,
        "scrollWidth": 600,
        "offenders": ["div#a b<script>", "x" * 200, 5, None] + [f"p.c{n}" for n in range(20)],
    }
    offenders = browser.overflow().offenders
    assert len(offenders) <= 5
    assert all(len(name) <= 40 for name in offenders)
    assert all("<" not in name and " " not in name for name in offenders)


@pytest.mark.parametrize("bad", [None, "text", {"viewportWidth": "x"}, {"scrollWidth": 5}, []])
def test_overflow_probe_rejects_unusable_results(bad):
    browser, _, drivers = make()
    drivers[0].overflow_result = bad
    with pytest.raises(BrowserError, match="проба"):
        browser.overflow()


def test_overflow_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].overflow_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.overflow()


def test_overflow_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.overflow()


# Проба buttons: тип, описатель и источник имени, без текста кнопок


def raw_button(descriptor="button#a", kind="button", source="text"):
    return {"kind": kind, "descriptor": descriptor, "source": source}


def test_buttons_probe_returns_typed_facts_and_passes_the_limit():
    browser, _, drivers = make()
    drivers[0].buttons_result = {
        "total": 60,
        "items": [raw_button("button#a"), raw_button("input.go", "input", None)],
    }
    snapshot = browser.buttons(50)
    assert drivers[0].buttons_args == (50,)
    assert snapshot.total == 60
    assert snapshot.items[0].descriptor == "button#a" and snapshot.items[0].name_source == "text"
    assert snapshot.items[1].kind == "input" and snapshot.items[1].name_source is None


def test_buttons_probe_cleans_descriptors_and_ignores_malformed_entries():
    browser, _, drivers = make()
    drivers[0].buttons_result = {
        "total": 3,
        "items": [
            raw_button("button#a b<script>alert(1)</script>"),
            raw_button("x" * 200),
            raw_button(""),
            5,
            None,
            {"descriptor": 7},
        ],
    }
    descriptors = [item.descriptor for item in browser.buttons(50).items]
    assert all(len(name) <= 40 for name in descriptors)
    assert all("<" not in name and " " not in name and "(" not in name for name in descriptors)
    assert "" not in descriptors


@pytest.mark.parametrize("bad", [None, "text", [], {"items": "y"}])
def test_buttons_probe_tolerates_malformed_results(bad):
    browser, _, drivers = make()
    drivers[0].buttons_result = bad
    snapshot = browser.buttons(50)
    assert snapshot.items == () and snapshot.total == 0


def test_buttons_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].buttons_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.buttons(50)


def test_buttons_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.buttons(50)


# Проба form_fields: тип поля, описатель и источник метки, без текстов страницы


def raw_field(descriptor="input#a", kind="input:text", source="label-for"):
    return {"kind": kind, "descriptor": descriptor, "source": source}


def test_form_fields_probe_returns_typed_facts_and_passes_the_limit():
    browser, _, drivers = make()
    drivers[0].fields_result = {
        "total": 70,
        "items": [raw_field("input#a"), raw_field("select#s", "select", None)],
    }
    snapshot = browser.form_fields(50)
    assert drivers[0].fields_args == (50,)
    assert snapshot.total == 70
    assert snapshot.items[0].name_source == "label-for"
    assert snapshot.items[1].kind == "select" and snapshot.items[1].name_source is None


def test_form_fields_probe_cleans_descriptors_and_ignores_malformed_entries():
    browser, _, drivers = make()
    drivers[0].fields_result = {
        "total": 4,
        "items": [raw_field("input#a b<script>"), raw_field("x" * 200), raw_field(""), 5, None],
    }
    descriptors = [item.descriptor for item in browser.form_fields(50).items]
    assert all(len(name) <= 40 and "<" not in name and " " not in name for name in descriptors)
    assert "" not in descriptors


@pytest.mark.parametrize("bad", [None, "text", [], {"items": "y"}])
def test_form_fields_probe_tolerates_malformed_results(bad):
    browser, _, drivers = make()
    drivers[0].fields_result = bad
    snapshot = browser.form_fields(50)
    assert snapshot.items == () and snapshot.total == 0


def test_form_fields_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].fields_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.form_fields(50)


def test_form_fields_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.form_fields(50)


# Проба tab_sequence: только Tab, ограничение числа нажатий, остановка при выходе или повторе


def raw_step(descriptor="a#a", interactive=True, visible=True, repeat=False):
    return {
        "descriptor": descriptor,
        "interactive": interactive,
        "focusVisible": visible,
        "repeat": repeat,
    }


def test_tab_probe_presses_only_tab_and_returns_typed_steps():
    browser, _, drivers = make()
    drivers[0].tab_start = {"interactiveTotal": 4}
    drivers[0].tab_steps = [raw_step("a#a"), raw_step("button#b", visible=False), None]
    sequence = browser.tab_sequence(20)
    assert sequence.interactive_total == 4
    assert [step.descriptor for step in sequence.steps] == ["a#a", "button#b"]
    assert [step.focus_visible for step in sequence.steps] == [True, False]
    assert drivers[0].sent_keys == [Keys.TAB] * 3  # третье нажатие вывело фокус со страницы
    assert all(key == Keys.TAB for key in drivers[0].sent_keys)  # ни Enter, ни пробела


def test_tab_probe_never_presses_more_than_the_limit():
    browser, _, drivers = make()
    drivers[0].tab_start = {"interactiveTotal": 50}
    drivers[0].tab_steps = [raw_step(f"a#{n}") for n in range(50)]
    sequence = browser.tab_sequence(5)
    assert len(drivers[0].sent_keys) == 5 and len(sequence.steps) == 5


def test_tab_probe_hard_caps_the_limit_for_safety():
    browser, _, drivers = make()
    drivers[0].tab_start = {"interactiveTotal": 500}
    drivers[0].tab_steps = [raw_step(f"a#{n}") for n in range(500)]
    browser.tab_sequence(10_000)
    assert len(drivers[0].sent_keys) <= 50


def test_tab_probe_stops_when_the_focus_cycle_repeats():
    browser, _, drivers = make()
    drivers[0].tab_start = {"interactiveTotal": 2}
    drivers[0].tab_steps = [raw_step("a#a"), raw_step("a#b"), raw_step("a#a", repeat=True)]
    sequence = browser.tab_sequence(20)
    assert [step.descriptor for step in sequence.steps] == ["a#a", "a#b"]
    assert len(drivers[0].sent_keys) == 3


def test_tab_probe_cleans_descriptors_and_tolerates_malformed_steps():
    browser, _, drivers = make()
    drivers[0].tab_start = {"interactiveTotal": 3}
    drivers[0].tab_steps = [raw_step("a#x y<script>" + "z" * 100), "text", raw_step("a#after")]
    sequence = browser.tab_sequence(20)
    assert len(sequence.steps) == 1  # на неожиданном результате останавливаемся
    assert "<" not in sequence.steps[0].descriptor and len(sequence.steps[0].descriptor) <= 40


@pytest.mark.parametrize("bad", [None, "text", [], {"interactiveTotal": "x"}])
def test_tab_probe_tolerates_malformed_start_results(bad):
    browser, _, drivers = make()
    drivers[0].tab_start = bad
    drivers[0].tab_steps = [raw_step("a#a")]
    assert browser.tab_sequence(20).interactive_total == 0


def test_tab_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].tab_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.tab_sequence(20)


def test_tab_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.tab_sequence(20)


# Режим без песочницы: только по явной просьбе (одноразовые машины CI)


def test_chrome_sandbox_stays_on_by_default():
    _, _, drivers = make()
    arguments = drivers[0].options.arguments
    assert "--no-sandbox" not in arguments and "--disable-dev-shm-usage" not in arguments


def test_no_sandbox_mode_adds_the_flags_needed_on_throwaway_ci_machines():
    _, _, drivers = make(no_sandbox=True)
    arguments = drivers[0].options.arguments
    assert "--no-sandbox" in arguments and "--disable-dev-shm-usage" in arguments


# Проба auth_signals: ссылки входа и регистрации, формы с паролем. Ничего не вводится и не нажимается


def raw_form(descriptor="form#login", **changes):
    data = {
        "descriptor": descriptor,
        "passwordCount": 1,
        "newPassword": False,
        "signupHint": False,
        "submitCount": 1,
        "fields": [
            {"kind": "input:text", "descriptor": "input#user", "source": "label-for"},
            {"kind": "input:password", "descriptor": "input#pass", "source": None},
        ],
    }
    data.update(changes)
    return data


def test_auth_probe_returns_typed_facts_and_passes_the_patterns():
    browser, _, drivers = make()
    drivers[0].auth_result = {
        "loginLinks": ["a#login"],
        "signupLinks": ["a.join", "button#reg"],
        "forms": [raw_form(passwordCount=2, newPassword=True)],
    }
    snapshot = browser.auth_signals("вход|log in", "регистрация|sign up")
    assert drivers[0].auth_args == ("вход|log in", "регистрация|sign up")
    assert snapshot.login_links == ("a#login",) and snapshot.signup_links == (
        "a.join",
        "button#reg",
    )
    form = snapshot.forms[0]
    assert form.password_count == 2 and form.new_password and form.submit_count == 1
    assert form.fields[0].descriptor == "input#user" and form.fields[1].name_source is None


def test_auth_probe_cleans_descriptors_and_limits_the_amount_of_data():
    browser, _, drivers = make()
    drivers[0].auth_result = {
        "loginLinks": ["a#x y<script>" + "z" * 100] + [f"a#l{n}" for n in range(50)],
        "signupLinks": [],
        "forms": [raw_form(f"form#f{n}") for n in range(20)],
    }
    snapshot = browser.auth_signals("a", "b")
    assert len(snapshot.login_links) <= 10 and len(snapshot.forms) <= 5
    assert all(
        len(name) <= 40 and "<" not in name and " " not in name for name in snapshot.login_links
    )


@pytest.mark.parametrize(
    "bad", [None, "text", [], {"forms": "y", "loginLinks": 5}, {"forms": [1, None]}]
)
def test_auth_probe_tolerates_malformed_results(bad):
    browser, _, drivers = make()
    drivers[0].auth_result = bad
    snapshot = browser.auth_signals("a", "b")
    assert snapshot.login_links == () and snapshot.signup_links == () and snapshot.forms == ()


def test_auth_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].auth_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.auth_signals("a", "b")


def test_auth_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.auth_signals("a", "b")


# Проба rendered_page: снимок отрисованной страницы (после скриптов)


def raw_rendered(**changes):
    data = {
        "title": "Магазин",
        "lang": "ru",
        "textLength": 420,
        "visualCount": 2,
        "hrefs": ["/a", "/b#x"],
        "hasNavigation": True,
    }
    data.update(changes)
    return data


def test_rendered_probe_returns_typed_facts():
    browser, _, drivers = make()
    drivers[0].rendered_result = raw_rendered()
    page = browser.rendered_page()
    assert page.title == "Магазин" and page.lang == "ru"
    assert page.text_length == 420 and page.visual_count == 2
    assert page.hrefs == ("/a", "/b#x") and page.has_navigation is True


def test_rendered_probe_waits_with_an_explicit_wait_and_still_answers_when_it_times_out():
    browser, _, drivers = make(render_wait=0.2)
    drivers[0].rendered_ready = False  # страница так и не «успокоилась»
    drivers[0].rendered_result = raw_rendered(title="")
    page = browser.rendered_page()
    assert page.title is None  # пустой заголовок считается отсутствующим
    assert drivers[0].rendered_waits >= 1


def test_rendered_probe_cleans_and_limits_the_data():
    browser, _, drivers = make()
    drivers[0].rendered_result = raw_rendered(
        title="T" * 500,
        lang="ru" + "x" * 50,
        hrefs=[f"/p{n}" for n in range(100)] + ["h" * 1000, 5, None],
    )
    page = browser.rendered_page()
    assert len(page.title) <= 200 and len(page.lang) <= 20
    assert len(page.hrefs) <= 50 and all(len(href) <= 300 for href in page.hrefs)


@pytest.mark.parametrize("bad", [None, "text", [], {"textLength": "x", "hrefs": "y"}])
def test_rendered_probe_tolerates_malformed_results(bad):
    browser, _, drivers = make()
    drivers[0].rendered_result = bad
    page = browser.rendered_page()
    assert page.title is None and page.text_length == 0 and page.hrefs == ()
    assert page.has_navigation is False


def test_rendered_probe_failure_is_a_browser_error():
    browser, _, drivers = make()
    drivers[0].rendered_error = WebDriverException("Message: javascript error")
    with pytest.raises(BrowserError):
        browser.rendered_page()


def test_rendered_probe_after_close_is_refused():
    browser, _, _ = make()
    browser.close()
    with pytest.raises(BrowserUnavailableError):
        browser.rendered_page()


# Chrome не установлен: отличается от «браузер не запустился»


def failing_factory(error):
    def factory(options):
        raise error

    return factory


def no_driver():
    from selenium.common.exceptions import NoSuchDriverException

    return NoSuchDriverException("Message: Unable to obtain driver for chrome")


def start(error, chrome_present):
    proxy = FakeProxy()
    return SeleniumBrowser(
        POLICY,
        driver_factory=failing_factory(error),
        proxy_factory=lambda policy: proxy,
        chrome_check=lambda: chrome_present,
    ), proxy


def test_missing_driver_and_no_chrome_installed_means_chrome_not_found():
    proxy = FakeProxy()
    with pytest.raises(BrowserNotInstalledError, match="Chrome не найден"):
        SeleniumBrowser(
            POLICY,
            driver_factory=failing_factory(no_driver()),
            proxy_factory=lambda policy: proxy,
            chrome_check=lambda: False,
        )
    assert proxy.stopped == 1  # сторож остановлен и в этом случае


def test_missing_driver_with_chrome_installed_is_a_plain_launch_failure_not_a_missing_browser():
    proxy = FakeProxy()
    with pytest.raises(BrowserUnavailableError) as caught:
        SeleniumBrowser(
            POLICY,
            driver_factory=failing_factory(no_driver()),
            proxy_factory=lambda policy: proxy,
            chrome_check=lambda: True,
        )
    assert not isinstance(caught.value, BrowserNotInstalledError)  # например, нет сети для драйвера


def test_other_launch_failures_are_never_reported_as_a_missing_browser():
    proxy = FakeProxy()
    error = WebDriverException("Message: session not created: version mismatch")
    with pytest.raises(BrowserUnavailableError) as caught:
        SeleniumBrowser(
            POLICY,
            driver_factory=failing_factory(error),
            proxy_factory=lambda policy: proxy,
            chrome_check=lambda: False,
        )
    assert not isinstance(caught.value, BrowserNotInstalledError)


def test_missing_executable_without_chrome_installed_means_chrome_not_found():
    proxy = FakeProxy()
    with pytest.raises(BrowserNotInstalledError):
        SeleniumBrowser(
            POLICY,
            driver_factory=failing_factory(FileNotFoundError("chromedriver")),
            proxy_factory=lambda policy: proxy,
            chrome_check=lambda: False,
        )


def test_not_installed_is_a_kind_of_unavailable_browser():
    assert issubclass(BrowserNotInstalledError, BrowserUnavailableError)
