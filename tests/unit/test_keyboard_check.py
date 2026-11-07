import pytest

from app.core.browser import DESKTOP, BrowserUnavailableError, FocusStep, TabSequence
from app.core.browser_session import BrowserSession
from app.core.checks.accessibility import MAX_TABS, KeyboardNavigation
from app.core.checks.context import CheckContext
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp


def step(descriptor="a#x", interactive=True, visible=True):
    return FocusStep(descriptor=descriptor, interactive=interactive, focus_visible=visible)


def run(total, *steps):
    browser = FakeBrowser()
    browser.tab_result = TabSequence(interactive_total=total, steps=steps)
    session = BrowserSession(FakeFactory(browser), SITE)
    context = CheckContext(http=FakeHttp(), site_url=SITE, browser=session)
    return KeyboardNavigation().run(context), browser


def steps(count, **kwargs):
    return [step(f"a#l{n}", **kwargs) for n in range(count)]


def test_passes_when_tab_reaches_the_elements_and_focus_is_visible():
    result, browser = run(5, *steps(5))
    assert result.check_id == "QC-016"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW
    assert "5 из 5" in result.actual
    assert browser.opens[0][1] == DESKTOP
    assert browser.tab_limits == [MAX_TABS] == [20]


def test_page_without_interactive_elements_is_skipped():
    result, _ = run(0)
    assert result.status is CheckStatus.SKIPPED
    assert "интерактив" in result.reason


def test_tab_that_moves_focus_nowhere_is_a_warning():
    result, _ = run(3)
    assert result.status is CheckStatus.WARNING
    assert "Tab" in result.actual and result.reason


@pytest.mark.parametrize(
    ("reached", "expected"), [(5, CheckStatus.PASSED), (4, CheckStatus.WARNING)]
)
def test_at_least_half_of_the_elements_must_be_reachable(reached, expected):
    result, _ = run(10, *steps(reached))
    assert result.status is expected
    if expected is CheckStatus.WARNING:
        assert "4 из 10" in result.actual


def test_expected_count_is_capped_by_the_number_of_tab_presses():
    assert run(100, *steps(20))[0].status is CheckStatus.PASSED
    result, _ = run(100, *steps(9))
    assert result.status is CheckStatus.WARNING
    assert "9 из 20" in result.actual


def test_elements_without_visible_focus_are_named_in_a_warning():
    items = [step("a#ok"), step("button#b1", visible=False), step("a#nav", visible=False)]
    result, _ = run(3, *items)
    assert result.status is CheckStatus.WARNING
    assert "button#b1" in result.actual and "a#nav" in result.actual
    assert "a#ok" not in result.actual


def test_only_the_first_elements_without_focus_indicator_are_listed():
    items = [step(f"a#n{n}", visible=False) for n in range(10)]
    result, _ = run(10, *items)
    assert "a#n0" in result.actual and "a#n9" not in result.actual
    assert "и ещё" in result.actual


def test_non_interactive_focus_stops_do_not_count_as_reached_or_need_an_indicator():
    items = [step("a#x"), step("div#panel", interactive=False, visible=False), step("a#y")]
    result, _ = run(2, *items)
    assert result.status is CheckStatus.PASSED
    assert "2 из 2" in result.actual


def test_the_check_never_fails_it_is_a_heuristic():
    result, _ = run(10, *steps(10, visible=False))
    assert result.status is not CheckStatus.FAILED


def test_without_a_connected_browser_the_error_reaches_the_runner():
    with pytest.raises(BrowserUnavailableError):
        KeyboardNavigation().run(CheckContext(http=FakeHttp(), site_url=SITE))
