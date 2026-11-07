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
from selenium.common.exceptions import (
    NoSuchDriverException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from app.adapters.browser.chrome_locator import chrome_installed
from app.adapters.browser.guard_proxy import GuardProxy
from app.core.browser import (
    AuthForm,
    AuthSnapshot,
    BrowserBlockedError,
    BrowserCertificateError,
    BrowserError,
    BrowserFactory,
    BrowserNetworkError,
    BrowserNotInstalledError,
    BrowserTimeoutError,
    BrowserUnavailableError,
    ButtonsSnapshot,
    ControlInfo,
    FieldsSnapshot,
    FocusStep,
    ImageInfo,
    ImagesSnapshot,
    OverflowInfo,
    PageInfo,
    RenderedPage,
    TabSequence,
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
# Видимые поля форм: тип, описатель и источник метки (упрощённый расчёт). Тексты не возвращаются.
FIELDS_SCRIPT = """
const limit = arguments[0];
const visible = (el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
    && style.display !== 'none' && !el.closest('[aria-hidden="true"]');
};
const text = (node) => (node.innerText || node.textContent || '').trim();
const labelText = (label) => {
  const copy = label.cloneNode(true);
  copy.querySelectorAll('input, select, textarea, button').forEach((n) => n.remove());
  return (copy.textContent || '').trim();
};
const source = (el) => {
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {
    const joined = labelledby.split(/\\s+/).map((id) => document.getElementById(id))
      .filter(Boolean).map(text).join(' ').trim();
    if (joined) return 'aria-labelledby';
  }
  if ((el.getAttribute('aria-label') || '').trim()) return 'aria-label';
  for (const label of Array.from(el.labels || [])) {
    if (labelText(label)) return label.contains(el) && label.control === el && !label.htmlFor
      ? 'label-wrap' : 'label-for';
  }
  if ((el.getAttribute('title') || '').trim()) return 'title';
  if ((el.getAttribute('placeholder') || '').trim()) return 'placeholder';
  return null;
};
const describe = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
  + (el.classList.length ? '.' + el.classList[0] : '');
const skipped = ['hidden', 'submit', 'button', 'reset', 'image'];
const fields = Array.from(document.querySelectorAll('input, select, textarea'))
  .filter((el) => !(el.tagName === 'INPUT' && skipped.includes((el.type || '').toLowerCase())))
  .filter(visible);
return {
  total: fields.length,
  items: fields.slice(0, limit).map((el) => ({
    kind: el.tagName.toLowerCase() + (el.tagName === 'INPUT' ? ':' + (el.type || 'text') : ''),
    descriptor: describe(el),
    source: source(el),
  })),
};
"""
# Обход клавишей Tab. Отправляется ТОЛЬКО Tab (ни Enter, ни пробела), чтобы ничего не активировать.
# Перед обходом снимаем стили всех элементов, на которые можно попасть табуляцией; после каждого
# перехода сравниваем: индикатор фокуса виден, если появился контур, изменился стиль или запущена
# CSS-анимация (переход) у элемента с фокусом.
TAB_START_SCRIPT = """/*tab-start*/
if (document.activeElement && document.activeElement !== document.body) {
  document.activeElement.blur();
}
window.scrollTo(0, 0);
window.__qaxrayVisited = new WeakSet();
window.__qaxrayBase = new WeakMap();
const props = ['outlineStyle', 'outlineWidth', 'outlineColor', 'boxShadow', 'borderTopColor',
  'borderBottomColor', 'backgroundColor', 'color', 'textDecorationLine'];
window.__qaxrayProps = props;
const snap = (node) => { const s = getComputedStyle(node); return props.map((p) => s[p]).join('|'); };
const focusable = Array.from(document.querySelectorAll(
  'a[href], button, input, select, textarea, summary, [tabindex], [contenteditable]'
)).filter((el) => !el.disabled && el.tabIndex >= 0
  && !(el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'hidden'));
focusable.slice(0, 1000).forEach((el) => window.__qaxrayBase.set(el, snap(el)));
const shown = (el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
    && style.display !== 'none' && !el.closest('[aria-hidden="true"]');
};
const radios = new Set();
let total = 0;
for (const el of focusable.filter(shown)) {
  if (el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'radio' && el.name) {
    const key = (el.form && el.form.id ? el.form.id : '') + '/' + el.name;
    if (radios.has(key)) continue;
    radios.add(key);
  }
  total += 1;
}
return {interactiveTotal: total};
"""
TAB_STEP_SCRIPT = """/*tab-step*/
const el = document.activeElement;
if (!el || el === document.body || el === document.documentElement) return null;
const describe = (node) => node.tagName.toLowerCase() + (node.id ? '#' + node.id : '')
  + (node.classList.length ? '.' + node.classList[0] : '');
if (window.__qaxrayVisited.has(el)) {
  return {descriptor: describe(el), interactive: true, focusVisible: true, repeat: true};
}
window.__qaxrayVisited.add(el);
const style = getComputedStyle(el);
const props = window.__qaxrayProps;
const now = props.map((p) => style[p]).join('|');
const base = window.__qaxrayBase.get(el);
const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
const changed = base !== undefined && base !== now;
const animating = typeof el.getAnimations === 'function' && el.getAnimations().length > 0;
const tag = el.tagName.toLowerCase();
const role = (el.getAttribute('role') || '').toLowerCase();
const interactive = ['a', 'button', 'input', 'select', 'textarea', 'summary'].includes(tag)
  || ['button', 'link', 'tab', 'menuitem', 'checkbox', 'switch', 'radio', 'option'].includes(role);
return {descriptor: describe(el), interactive, focusVisible: outline || changed || animating,
  repeat: false};
"""
MAX_TAB_PRESSES = 50
# Признаки входа и регистрации. Ничего не вводится и не нажимается; значения полей не читаются.
# Образцы поиска приходят из проверки (аргументы 0 и 1) и применяются к тексту, aria-label, title и
# адресу ссылки; в ответ попадают только описатели элементов (тег, id, первый класс).
AUTH_SCRIPT = """/*auth*/
const loginRe = new RegExp(arguments[0], 'iu');
const signupRe = new RegExp(arguments[1], 'iu');
const visible = (el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
    && style.display !== 'none' && !el.closest('[aria-hidden="true"]');
};
const text = (node) => (node.innerText || node.textContent || '').trim();
const describe = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
  + (el.classList && el.classList.length ? '.' + el.classList[0] : '');
const labelText = (label) => {
  const copy = label.cloneNode(true);
  copy.querySelectorAll('input, select, textarea, button').forEach((n) => n.remove());
  return (copy.textContent || '').trim();
};
const labelSource = (el) => {
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {
    const joined = labelledby.split(/\\s+/).map((id) => document.getElementById(id))
      .filter(Boolean).map(text).join(' ').trim();
    if (joined) return 'aria-labelledby';
  }
  if ((el.getAttribute('aria-label') || '').trim()) return 'aria-label';
  for (const label of Array.from(el.labels || [])) {
    if (labelText(label)) return label.contains(el) && !label.htmlFor ? 'label-wrap' : 'label-for';
  }
  if ((el.getAttribute('title') || '').trim()) return 'title';
  if ((el.getAttribute('placeholder') || '').trim()) return 'placeholder';
  return null;
};
const haystack = (el) => [text(el).slice(0, 80), el.getAttribute('aria-label') || '',
  el.getAttribute('title') || '', (el.getAttribute('href') || '').slice(0, 200)].join(' ');
const loginLinks = [];
const signupLinks = [];
const clickable = Array.from(document.querySelectorAll('a[href], button, [role=button], [role=link]'))
  .filter(visible).slice(0, 400);
for (const el of clickable) {
  const h = haystack(el);
  if (loginRe.test(h)) loginLinks.push(describe(el));
  if (signupRe.test(h)) signupLinks.push(describe(el));
}
const passwordInputs = Array.from(document.querySelectorAll('input[type=password]')).filter(visible);
const containerOf = (el) => {
  if (el.form) return el.form;
  let node = el.parentElement;
  for (let i = 0; i < 6 && node && node !== document.body; i += 1, node = node.parentElement) {
    if (node.querySelectorAll('input').length >= 2
        || node.querySelector('input[type=submit], button[type=submit]')) return node;
  }
  return el.parentElement || document.body;
};
const containers = [];
for (const el of passwordInputs) {
  const container = containerOf(el);
  if (!containers.includes(container)) containers.push(container);
}
const textTypes = ['text', 'email', 'tel', 'number', 'password', 'search', 'url', ''];
const forms = containers.slice(0, 5).map((container) => {
  const inputs = Array.from(container.querySelectorAll('input'))
    .filter((i) => textTypes.includes((i.type || '').toLowerCase()) && visible(i));
  const passwords = inputs.filter((i) => (i.type || '').toLowerCase() === 'password');
  const attrs = [container.id, String(container.className || ''), container.getAttribute('name') || '',
    container.getAttribute('action') || '', container.getAttribute('aria-label') || ''].join(' ');
  const submits = Array.from(container.querySelectorAll(
    'button:not([type=button]):not([type=reset]), input[type=submit], input[type=image]'
  )).filter(visible);
  return {
    descriptor: describe(container),
    passwordCount: passwords.length,
    newPassword: passwords.some((i) => (i.getAttribute('autocomplete') || '')
      .toLowerCase().includes('new-password')),
    signupHint: signupRe.test(attrs),
    submitCount: submits.length,
    fields: inputs.slice(0, 10).map((i) => ({
      kind: 'input:' + ((i.type || 'text').toLowerCase()),
      descriptor: describe(i),
      source: labelSource(i),
    })),
  };
});
return {loginLinks: loginLinks.slice(0, 10), signupLinks: signupLinks.slice(0, 10), forms};
"""
# Снимок отрисованной страницы после выполнения скриптов. Ожидание «страница успокоилась»: есть
# заголовок и видимый текст (явное ожидание с ограничением, без sleep).
RENDERED_READY_SCRIPT = """/*rendered-ready*/
return document.readyState === 'complete' && document.title.trim().length > 0
  && ((document.body && document.body.innerText) || '').trim().length > 0;
"""
RENDERED_SCRIPT = """/*rendered*/
const text = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim();
const shown = (el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
    && style.display !== 'none';
};
const visual = Array.from(document.querySelectorAll('img, svg, canvas, video, picture, iframe'))
  .filter(shown).length;
const hrefs = Array.from(document.querySelectorAll('a[href]')).slice(0, 50)
  .map((a) => (a.getAttribute('href') || '').trim().slice(0, 300));
const navigation = !!document.querySelector('nav, [role=navigation]')
  || document.querySelectorAll('header a[href]').length > 0;
return {
  title: (document.title || '').trim().slice(0, 200),
  lang: (document.documentElement.getAttribute('lang') || '').trim().slice(0, 20),
  textLength: text.length,
  visualCount: visual,
  hrefs,
  hasNavigation: navigation,
};
"""
MAX_RENDERED_HREFS = 50
MAX_HREF_LENGTH = 300
MAX_TITLE_LENGTH = 200
MAX_AUTH_LINKS = 10
MAX_AUTH_FORMS = 5
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


def _controls(raw: Any, default_kind: str) -> tuple[int, tuple[ControlInfo, ...]]:
    """Общий разбор списка элементов из пробы: описатели очищаются, мусор отбрасывается."""
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
                kind=UNSAFE_NAME_CHARS.sub("", kind)[:20]
                if isinstance(kind, str)
                else default_kind,
                descriptor=descriptor,
                name_source=UNSAFE_NAME_CHARS.sub("", source)[:20]
                if isinstance(source, str) and source
                else None,
            )
        )
    total = raw.get("total") if isinstance(raw, dict) else None
    count = total if isinstance(total, int) and not isinstance(total, bool) else len(items)
    return max(count, len(items)), tuple(items)


def _buttons_snapshot(raw: Any) -> ButtonsSnapshot:
    total, items = _controls(raw, "button")
    return ButtonsSnapshot(total=total, items=items)


def _fields_snapshot(raw: Any) -> FieldsSnapshot:
    total, items = _controls(raw, "input")
    return FieldsSnapshot(total=total, items=items)


def _clean_names(raw: Any, limit: int) -> tuple[str, ...]:
    names = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            cleaned = UNSAFE_NAME_CHARS.sub("", item)[:MAX_NAME_LENGTH]
            if cleaned:
                names.append(cleaned)
    return tuple(names[:limit])


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _auth_form(raw: Any) -> AuthForm | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("descriptor"), str):
        return None
    descriptor = UNSAFE_NAME_CHARS.sub("", raw["descriptor"])[:MAX_NAME_LENGTH]
    if not descriptor:
        return None
    _, fields = _controls({"items": raw.get("fields")}, "input")
    return AuthForm(
        descriptor=descriptor,
        password_count=_count(raw.get("passwordCount")),
        new_password=raw.get("newPassword") is True,
        signup_hint=raw.get("signupHint") is True,
        submit_count=_count(raw.get("submitCount")),
        fields=fields[:10],
    )


def _auth_snapshot(raw: Any) -> AuthSnapshot:
    data = raw if isinstance(raw, dict) else {}
    forms = [
        form
        for form in (
            _auth_form(entry)
            for entry in (data["forms"] if isinstance(data.get("forms"), list) else [])
        )
        if form is not None
    ]
    return AuthSnapshot(
        login_links=_clean_names(data.get("loginLinks"), MAX_AUTH_LINKS),
        signup_links=_clean_names(data.get("signupLinks"), MAX_AUTH_LINKS),
        forms=tuple(forms[:MAX_AUTH_FORMS]),
    )


def _rendered_page(raw: Any) -> RenderedPage:
    data = raw if isinstance(raw, dict) else {}
    title = data.get("title")
    lang = data.get("lang")
    hrefs = data.get("hrefs")
    return RenderedPage(
        title=title.strip()[:MAX_TITLE_LENGTH] or None if isinstance(title, str) else None,
        lang=re.sub(r"[^A-Za-z0-9-]", "", lang)[:20] or None if isinstance(lang, str) else None,
        text_length=_count(data.get("textLength")),
        visual_count=_count(data.get("visualCount")),
        hrefs=tuple(
            href.strip()[:MAX_HREF_LENGTH]
            for href in (hrefs if isinstance(hrefs, list) else [])
            if isinstance(href, str)
        )[:MAX_RENDERED_HREFS],
        has_navigation=data.get("hasNavigation") is True,
    )


def _tab_step(raw: Any) -> tuple[FocusStep | None, bool]:
    """Один шаг фокуса: (шаг, продолжать ли). Неожиданный или повторный результат останавливает обход."""
    if not isinstance(raw, dict) or raw.get("repeat") is True:
        return None, False
    descriptor = raw.get("descriptor")
    if not isinstance(descriptor, str):
        return None, False
    cleaned = UNSAFE_NAME_CHARS.sub("", descriptor)[:MAX_NAME_LENGTH]
    if not cleaned:
        return None, False
    return (
        FocusStep(
            descriptor=cleaned,
            interactive=bool(raw.get("interactive")),
            focus_visible=bool(raw.get("focusVisible")),
        ),
        True,
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
        no_sandbox: bool = False,
        render_wait: float = 3.0,
        chrome_check: Callable[[], bool] | None = None,
        driver_factory: DriverFactory | None = None,
        proxy_factory: ProxyFactory = GuardProxy,
    ) -> None:
        self._policy = policy
        self._page_timeout = page_timeout
        self._render_wait = render_wait
        self._closed = False
        self._downloads = tempfile.mkdtemp(prefix="qaxray-downloads-")
        self._proxy = proxy_factory(policy)
        try:
            port = self._proxy.start()
            self._driver = (driver_factory or _chrome_driver)(
                self._options(port, headless, no_sandbox)
            )
        except (WebDriverException, OSError) as error:
            self._cleanup()
            # «Драйвер не получен» бывает и без Chrome, и без сети: Chrome не найден, только если
            # его нет и в стандартных местах установки.
            driver_missing = isinstance(error, NoSuchDriverException | FileNotFoundError)
            if driver_missing and not (chrome_check or chrome_installed)():
                raise BrowserNotInstalledError(
                    "Chrome не найден: установите Google Chrome"
                ) from error
            raise BrowserUnavailableError(f"Браузер не запустился: {_short(error)}") from error

    def _options(self, proxy_port: int, headless: bool, no_sandbox: bool) -> ChromeOptions:
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument(f"--proxy-server=http://127.0.0.1:{proxy_port}")
        options.add_argument("--proxy-bypass-list=<-loopback>")  # даже петля идёт через сторож
        for argument in HARDENING_ARGUMENTS:
            options.add_argument(argument)
        if no_sandbox:  # только для одноразовых машин CI, где песочница Chrome недоступна
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
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

    def form_fields(self, limit: int) -> FieldsSnapshot:
        """Видимые поля форм текущей страницы и источник их метки."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        try:
            raw = self._driver.execute_script(FIELDS_SCRIPT, max(1, min(limit, MAX_PROBE_LIMIT)))
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return _fields_snapshot(raw)

    def tab_sequence(self, max_presses: int) -> TabSequence:
        """Обход страницы клавишей Tab: интерактивные элементы и порядок фокуса.

        Нажимается только Tab, не более max_presses раз (и не более жёсткого предела);
        обход заканчивается, когда фокус покидает страницу или элемент повторяется."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        presses = max(1, min(max_presses, MAX_TAB_PRESSES))
        try:
            start = self._driver.execute_script(TAB_START_SCRIPT)
            total = start.get("interactiveTotal") if isinstance(start, dict) else None
            interactive_total = (
                total if isinstance(total, int) and not isinstance(total, bool) else 0
            )
            steps: list[FocusStep] = []
            for _ in range(presses if interactive_total > 0 else 0):
                self._driver.switch_to.active_element.send_keys(Keys.TAB)
                step, keep_going = _tab_step(self._driver.execute_script(TAB_STEP_SCRIPT))
                if step is not None:
                    steps.append(step)
                if not keep_going:
                    break
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return TabSequence(interactive_total=max(interactive_total, 0), steps=tuple(steps))

    def auth_signals(self, login_pattern: str, signup_pattern: str) -> AuthSnapshot:
        """Ссылки входа и регистрации и формы с полем пароля (только структура, без ввода)."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        try:
            raw = self._driver.execute_script(AUTH_SCRIPT, login_pattern, signup_pattern)
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return _auth_snapshot(raw)

    def rendered_page(self) -> RenderedPage:
        """Снимок страницы после скриптов: заголовок, язык, видимый текст, ссылки, навигация."""
        if self._closed:
            raise BrowserUnavailableError("Браузер закрыт")
        try:
            try:
                WebDriverWait(self._driver, self._render_wait, poll_frequency=POLL_SECONDS).until(
                    lambda driver: driver.execute_script(RENDERED_READY_SCRIPT)
                )
            except TimeoutException:
                pass  # страница так и не «успокоилась»: берём то, что есть
            raw = self._driver.execute_script(RENDERED_SCRIPT)
        except WebDriverException as error:
            raise BrowserError(f"Сбой браузера: {_short(error)}") from error
        return _rendered_page(raw)

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
    policy: TargetPolicy, *, page_timeout: float, headless: bool, no_sandbox: bool = False
) -> BrowserFactory:
    """Фабрика для сессии запуска: браузер создаётся лениво, при первой браузерной проверке."""

    def create() -> SeleniumBrowser:
        return SeleniumBrowser(
            policy, page_timeout=page_timeout, headless=headless, no_sandbox=no_sandbox
        )

    return create
