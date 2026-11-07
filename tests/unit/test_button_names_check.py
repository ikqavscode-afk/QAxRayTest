import pytest

from app.core.browser import DESKTOP, BrowserUnavailableError, ButtonsSnapshot, ControlInfo
from app.core.browser_session import BrowserSession
from app.core.checks.accessibility import MAX_BUTTONS, ButtonsHaveNames
from app.core.checks.context import CheckContext
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp


def control(descriptor="button", source="text", kind="button"):
    return ControlInfo(kind=kind, descriptor=descriptor, name_source=source)


def run(*items, total=None):
    browser = FakeBrowser()
    browser.buttons_snapshot = ButtonsSnapshot(
        total=len(items) if total is None else total, items=items
    )
    session = BrowserSession(FakeFactory(browser), SITE)
    context = CheckContext(http=FakeHttp(), site_url=SITE, browser=session)
    return ButtonsHaveNames().run(context), browser


def test_passes_when_every_visible_button_has_a_name():
    result, browser = run(control("button#a"), control("button#b", "aria-label"))
    assert result.check_id == "QC-014"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM
    assert "2" in result.actual
    assert browser.opens[0][1] == DESKTOP
    assert browser.buttons_limits == [MAX_BUTTONS]


def test_unnamed_buttons_are_a_warning_naming_them():
    result, _ = run(control("button#ok"), control("button#icon", None), control("a.btn", None))
    assert result.status is CheckStatus.WARNING
    assert "2 из 3" in result.actual
    assert "button#icon" in result.actual and "a.btn" in result.actual
    assert "button#ok" not in result.actual
    assert result.reason


def test_the_check_never_fails_it_is_a_heuristic():
    result, _ = run(*[control(f"button#{n}", None) for n in range(10)])
    assert result.status is not CheckStatus.FAILED


def test_only_the_first_culprits_are_listed():
    result, _ = run(*[control(f"button#b{n}", None) for n in range(12)])
    assert "button#b0" in result.actual and "button#b11" not in result.actual
    assert "и ещё" in result.actual


@pytest.mark.parametrize(
    "source", ["text", "aria-label", "aria-labelledby", "alt", "value", "default"]
)
def test_every_normal_name_source_counts_as_named(source):
    result, _ = run(control(source=source))
    assert result.status is CheckStatus.PASSED


def test_name_only_from_title_counts_as_named_but_is_mentioned():
    result, _ = run(control("button#help", "title"), control("button#ok"))
    assert result.status is CheckStatus.PASSED
    assert "title" in result.actual


def test_report_says_when_only_the_first_buttons_were_checked():
    result, _ = run(*[control(f"button#b{n}") for n in range(MAX_BUTTONS)], total=MAX_BUTTONS + 20)
    assert result.status is CheckStatus.PASSED
    assert f"{MAX_BUTTONS} из {MAX_BUTTONS + 20}" in result.actual


def test_no_visible_buttons_is_skipped_with_a_reason():
    result, _ = run()
    assert result.status is CheckStatus.SKIPPED
    assert "кноп" in result.reason


def test_without_a_connected_browser_the_error_reaches_the_runner():
    with pytest.raises(BrowserUnavailableError):
        ButtonsHaveNames().run(CheckContext(http=FakeHttp(), site_url=SITE))
