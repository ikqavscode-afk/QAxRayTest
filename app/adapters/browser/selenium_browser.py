"""Адаптер порта браузера на Selenium и Chrome.

Защита: Chrome запускается через прокси-сторож (guard_proxy), поэтому все запросы страницы
проходят политику адресов. Скачивание файлов запрещено, профиль браузера временный, ожидания
явные (WebDriverWait), расширения и фоновые обращения выключены."""

import logging
import os
import re
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
    ButtonsSnapshot,
    ControlInfo,
    ImageInfo,
    ImagesSnapshot,
    OverflowInfo,
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


# Видимые изображения (не менее 2x2 px, не скрытые): адрес, загружено ли, ширина, ленивое ли.
IMAGES_SCRIPT = """
const limit = arguments[0];
const visible = Array.from(document.images).filter((img) => {
  const rect = img.getBoundingClientRect();
  const style = getComputedStyle(img);
  return rect.width >= 2 && rect.height >= 2
    && style.visibility !== 'hidden' && style.display !== 'none';
});
return {
  total: visible.length,
  items: visible.slice(0, limit).map((img) => ({
    src: img.currentSrc || img.src || '',
    complete: img.complete,
    naturalWidth: img.naturalWidth,
    lazy: img.loading === 'lazy',
  })),
};
"""
# Ширина окна и содержимого; если страница шире окна, ищем самые внешние элементы за правым краем
# (кроме лежащих внутри прокручиваемых или обрезающих предков). Текст страницы не собирается.
OVERFLOW_SCRIPT = """
const viewportWidth = window.innerWidth;
const scrollWidth = document.documentElement.scrollWidth;
const offenders = [];
if (scrollWidth > viewportWidth + 1 && document.body) {
  const seen = new Set();
  const nodes = Array.from(document.body.querySelectorAll('*')).slice(0, 3000);
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.right <= viewportWidth + 1) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.position === 'fixed') continue;
    let clipped = false;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== 'visible'
          && p.getBoundingClientRect().right <= viewportWidth + 1) { clipped = true; break; }
    }
    if (clipped) continue;
    const nested = seen.has(el.parentElement);
    seen.add(el);
    if (nested) continue;
    const name = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
      + (el.classList.length ? '.' + el.classList[0] : '');
    offenders.push({name, right: rect.right});
  }
  offenders.sort((a, b) => b.right - a.right);
}
return {viewportWidth, scrollWidth, offenders: offenders.slice(0, 5).map((o) => o.name)};
"""
# Видимые кнопки: тип, описатель и откуда взято доступное имя (упрощённый расчёт имени).
# Текст кнопок не возвращается.
BUTTONS_SCRIPT = """
const limit = arguments[0];
const visible = (el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
    && style.display !== 'none' && !el.closest('[aria-hidden="true"]');
};
const text = (node) => (node.innerText || node.textContent || '').trim();
const source = (el) => {
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {
    const joined = labelledby.split(/\\s+/).map((id) => document.getElementById(id))
      .filter(Boolean).map(text).join(' ').trim();
    if (joined) return 'aria-labelledby';
  }
  if ((el.getAttribute('aria-label') || '').trim()) return 'aria-label';
  if (el.tagName === 'INPUT') {
    const type = (el.type || '').toLowerCase();
    if (type === 'image') { if ((el.alt || '').trim()) return 'alt'; }
    else if ((el.value || '').trim()) return 'value';
    else if (type === 'submit' || type === 'reset') return 'default';
  } else {
    if (text(el)) return 'text';
    const img = el.querySelector('img[alt]');
    if (img && img.alt.trim()) return 'alt';
    const svgTitle = el.querySelector('svg title');
    if (svgTitle && text(svgTitle)) return 'svg-title';
    const inner = el.querySelector('[aria-label]');
    if (inner && inner.getAttribute('aria-label').trim()) return 'aria-label';
  }
  if ((el.getAttribute('title') || '').trim()) return 'title';
  return null;
};
const describe = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
  + (el.classList.length ? '.' + el.classList[0] : '');
const controls = Array.from(document.querySelectorAll(
  'button, input[type=button], input[type=submit], input[type=reset], input[type=image], [role=button]'
)).filter(visible);
return {
  total: controls.length,
  items: controls.slice(0, limit).map((el) => ({
    kind: el.tagName === 'INPUT' ? 'input' : (el.tagName === 'BUTTON' ? 'button' : 'role-button'),
    descriptor: describe(el),
    source: source(el),
  })),
};
"""
MAX_OFFENDERS = 5
MAX_NAME_LENGTH = 40
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_\-:.#]")
MAX_SRC_LENGTH = 300
MAX_PROBE_LIMIT = 100


def _clean_src(src: str) -> str:
    """Адрес изображения для отчёта: без параметров запроса, данных и логина в адресе."""
    if src.startswith("data:"):
        return src.split(";", 1)[0].split(",", 1)[0][:40]  # только тип содержимого
    try:
        parts = urlsplit(src)
        host = parts.hostname
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "invalid-url"
    if parts.scheme in ALLOWED_SCHEMES and host:
        return f"{parts.scheme}://{host}{port}{parts.path}"[:MAX_SRC_LENGTH]
    return f"{parts.scheme}:" if parts.scheme else src[:MAX_SRC_LENGTH]


def _snapshot(raw: Any) -> ImagesSnapshot:
    entries = raw.get("items") if isinstance(raw, dict) else None
    items: list[ImageInfo] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("src"), str) or not entry["src"]:
            continue
        width = entry.get("naturalWidth")
        items.append(
            ImageInfo(
                src=_clean_src(entry["src"]),
                complete=bool(entry.get("complete")),
                natural_width=int(width) if isinstance(width, int | float) else 0,
                lazy=bool(entry.get("lazy")),
            )
        )
    total = raw.get("total") if isinstance(raw, dict) else None
    count = total if isinstance(total, int) and not isinstance(total, bool) else len(items)
    return ImagesSnapshot(total=max(count, len(items)), items=tuple(items))


def _overflow_info(raw: Any) -> OverflowInfo:
    if not isinstance(raw, dict):
        raise BrowserError("Сбой браузера: проба переполнения вернула неожиданный результат")
    widths = []
    for key in ("viewportWidth", "scrollWidth"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise BrowserError("Сбой браузера: проба переполнения вернула неожиданный результат")
        widths.append(int(value))
    names = raw.get("offenders")
    offenders = [
        UNSAFE_NAME_CHARS.sub("", name)[:MAX_NAME_LENGTH]
        for name in (names if isinstance(names, list) else [])
        if isinstance(name, str)
    ]
    return OverflowInfo(
        viewport_width=widths[0],
        scroll_width=widths[1],
        offenders=tuple(name for name in offenders if name)[:MAX_OFFENDERS],
    )


def _buttons_snapshot(raw: Any) -> ButtonsSnapshot:
    entries = raw.get("items") if isinstance(raw, dict) else None
    items: list[ControlInfo] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("descriptor"), str):
            continue
        descriptor = UNSAFE_NAME_CHARS.sub("", entry["descriptor"])[:MAX_NAME_LENGTH]
        if not descriptor:
            continue
        source = entry.get("source")
        kind = entry.get("kind")
        items.append(
            ControlInfo(
                kind=UNSAFE_NAME_CHARS.sub("", kind)[:20] if isinstance(kind, str) else "button",
                descriptor=descriptor,
                name_source=UNSAFE_NAME_CHARS.sub("", source)[:20]
                if isinstance(source, str) and source
                else None,
            )
        )
    total = raw.get("total") if isinstance(raw, dict) else None
    count = total if isinstance(total, int) and not isinstance(total, bool) else len(items)
    return ButtonsSnapshot(total=max(count, len(items)), items=tuple(items))


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

    def images(self, limit: int) -> ImagesSnapshot:
        """Видимые изображения текущей страницы (первые `limit`)."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        try:
            raw = self._driver.execute_script(IMAGES_SCRIPT, max(1, min(limit, MAX_PROBE_LIMIT)))
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return _snapshot(raw)

    def overflow(self) -> OverflowInfo:
        """Ширина окна и содержимого текущей страницы (и что выходит за правый край)."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        try:
            raw = self._driver.execute_script(OVERFLOW_SCRIPT)
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return _overflow_info(raw)

    def buttons(self, limit: int) -> ButtonsSnapshot:
        """Видимые кнопки текущей страницы и источник их доступного имени."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        try:
            raw = self._driver.execute_script(BUTTONS_SCRIPT, max(1, min(limit, MAX_PROBE_LIMIT)))
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return _buttons_snapshot(raw)

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
