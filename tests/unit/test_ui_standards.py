"""Автоматические стражи стандартов интерфейса (docs/standards/ui-ux-standards.md).

Проверяют таблицу стилей и разметку самого приложения: контраст WCAG 2.2 AA, зоны нажатия,
сетку отступов 4/8 px, видимый фокус, масштабирование."""

import re
from pathlib import Path

import pytest

from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from app.web import create_app
from tests.unit.support import StubService

CSS = (Path(__file__).resolve().parents[2] / "app/web/static/style.css").read_text(encoding="utf-8")
ROOT_FONT_PX = 16


def luminance(color: str) -> float:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(char * 2 for char in color)
    red, green, blue = (int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def contrast(first: str, second: str) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def variables() -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+)\s*:\s*(#[0-9a-fA-F]{3,6})", CSS))


def rule(selector: str) -> str:
    found = re.search(rf"(?:^|\}})\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", CSS, re.MULTILINE)
    assert found, f"в style.css нет правила {selector}"
    return found.group(1)


def declared(selector: str, prop: str) -> str:
    found = re.search(rf"(?:^|;)\s*{prop}\s*:\s*([^;]+)", rule(selector))
    assert found, f"у {selector} не задано {prop}"
    return found.group(1).strip()


def to_px(value: str) -> float:
    number = float(re.match(r"-?[\d.]+", value).group())  # type: ignore[union-attr]
    return number * ROOT_FONT_PX if value.endswith("rem") else number


# WCAG 1.4.3 / 1.4.11: контраст


@pytest.mark.parametrize("text", ["text", "muted", "accent", "bad", "warn"])
@pytest.mark.parametrize("surface", ["bg", "panel"])
def test_text_colors_have_aa_contrast_on_both_surfaces(text, surface):
    colors = variables()
    assert contrast(colors[text], colors[surface]) >= 4.5


def test_primary_button_text_has_aa_contrast():
    text = declared("button.primary", "color")
    assert contrast(text, variables()["accent"]) >= 4.5


def test_form_control_borders_have_3_to_1_contrast_on_adjacent_surfaces():
    colors = variables()
    border = colors["border-control"]
    assert contrast(border, colors["bg"]) >= 3
    assert contrast(border, colors["panel"]) >= 3
    assert "var(--border-control)" in rule("input[type=url]")
    assert "var(--border-control)" in rule("textarea")


def test_placeholder_text_has_aa_contrast_not_the_browser_default():
    placeholder = rule("input::placeholder, textarea::placeholder")
    assert "var(--muted)" in placeholder and "opacity: 1" in placeholder
    assert contrast(variables()["muted"], variables()["bg"]) >= 4.5


def test_focus_ring_is_visible_on_both_surfaces():
    colors = variables()
    assert contrast(colors["warn"], colors["panel"]) >= 3
    assert contrast(colors["warn"], colors["bg"]) >= 3


# WCAG 2.4.7 / 2.5.8: фокус и размер целей


def test_focus_indicator_is_defined_and_never_removed():
    ring = rule(":focus-visible")
    assert "outline" in ring and to_px(re.search(r"outline:\s*([\d.]+px)", ring).group(1)) >= 2  # type: ignore[union-attr]
    assert not re.search(r"outline\s*:\s*(none|0)\b", CSS)


@pytest.mark.parametrize(
    "selector", ["button, .button", "label.check", "details summary", ".brand"]
)
def test_interactive_elements_have_at_least_44px_touch_target(selector):
    assert to_px(declared(selector, "min-height")) >= 44


def test_checkbox_is_at_least_24px():
    assert to_px(declared("input[type=checkbox]", "width")) >= 24
    assert to_px(declared("input[type=checkbox]", "height")) >= 24


# Сетка отступов: кратно 4 px (основной шаг 8 px)


def test_spacing_follows_the_4px_grid():
    offenders = []
    for prop, value in re.findall(r"(padding[a-z-]*|margin[a-z-]*|gap)\s*:\s*([^;}]+)", CSS):
        for token in value.split():
            if token in {"0", "auto"} or not re.match(r"-?[\d.]+(rem|px)$", token):
                continue
            if to_px(token) % 4:
                offenders.append(f"{prop}: {token} = {to_px(token):g}px")
    assert not offenders, offenders


# Читаемость и масштабирование


def test_base_font_is_at_least_16px_and_hints_not_below_14px():
    assert to_px(re.search(r"body\s*\{[^}]*font:\s*(\d+px)", CSS).group(1)) >= 16  # type: ignore[union-attr]
    assert to_px(declared(".hint", "font-size")) >= 14


def test_page_allows_zoom_and_scales_to_the_device():
    app = create_app(service=StubService())
    html = app.test_client().get("/").get_data(as_text=True)
    viewport = re.search(r'<meta name="viewport" content="([^"]+)"', html)
    assert viewport and "width=device-width" in viewport.group(1)
    assert "user-scalable=no" not in viewport.group(1)
    assert "maximum-scale" not in viewport.group(1)


def test_scrollable_results_table_is_reachable_and_named_for_keyboard_users():
    result = CheckResult(
        check_id="QC-001",
        title="Проверка",
        status=CheckStatus.PASSED,
        severity=Severity.LOW,
        expected="-",
        actual="-",
    )
    app = create_app(service=StubService(RunReport(results=[result])))
    html = (
        app.test_client()
        .post("/check", data={"site_url": "https://example.com", "consent": "on"})
        .get_data(as_text=True)
    )
    wrapper = re.search(r'<div class="table-wrap"[^>]*>', html)
    assert wrapper, "обёртка таблицы не найдена"
    assert 'tabindex="0"' in wrapper.group(0)
    assert 'role="region"' in wrapper.group(0)
    assert "aria-label=" in wrapper.group(0)
