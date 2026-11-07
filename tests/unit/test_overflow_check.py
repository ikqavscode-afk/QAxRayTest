import pytest

from app.core.browser import (
    DESKTOP,
    MOBILE,
    BrowserUnavailableError,
    OverflowInfo,
)
from app.core.browser_session import BrowserSession
from app.core.checks.context import CheckContext
from app.core.checks.layout import NoHorizontalOverflow
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp


def run(results=None):
    browser = FakeBrowser()
    browser.overflow_results = results or {}
    session = BrowserSession(FakeFactory(browser), SITE)
    context = CheckContext(http=FakeHttp(), site_url=SITE, browser=session)
    return NoHorizontalOverflow().run(context), browser


def test_passes_when_content_fits_both_window_sizes_and_both_are_opened():
    result, browser = run()
    assert result.check_id == "QC-010"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW
    assert [viewport for _, viewport in browser.opens] == [DESKTOP, MOBILE]
    assert "1280" in result.actual and "375" in result.actual


def test_overflow_on_mobile_is_a_warning_with_size_and_culprits():
    wide = OverflowInfo(viewport_width=375, scroll_width=595, offenders=("div#hero", "table.data"))
    result, _ = run({MOBILE: wide})
    assert result.status is CheckStatus.WARNING
    assert "375" in result.actual and "220" in result.actual
    assert "div#hero" in result.actual and "table.data" in result.actual
    assert "1280" not in result.actual.split("375")[0]  # про рабочий стол ничего лишнего
    assert result.reason


def test_overflow_on_both_sizes_lists_both():
    results = {
        DESKTOP: OverflowInfo(1280, 1500, ()),
        MOBILE: OverflowInfo(375, 400, ("img.banner",)),
    }
    result, _ = run(results)
    assert result.status is CheckStatus.WARNING
    assert "1280" in result.actual and "375" in result.actual


@pytest.mark.parametrize(
    ("excess", "expected"),
    [(0, CheckStatus.PASSED), (1, CheckStatus.PASSED), (2, CheckStatus.WARNING)],
)
def test_one_pixel_of_subpixel_rounding_is_tolerated(excess, expected):
    result, _ = run({MOBILE: OverflowInfo(375, 375 + excess, ())})
    assert result.status is expected


def test_the_check_never_fails_it_is_a_heuristic():
    result, _ = run({DESKTOP: OverflowInfo(1280, 9000, ()), MOBILE: OverflowInfo(375, 9000, ())})
    assert result.status is not CheckStatus.FAILED


def test_real_window_width_from_the_probe_is_used_not_the_requested_one():
    # окно не сузилось до 375: оценивается по тому, что показал браузер
    fits = OverflowInfo(viewport_width=500, scroll_width=500, offenders=())
    assert run({MOBILE: fits})[0].status is CheckStatus.PASSED


def test_only_the_first_culprits_are_listed():
    many = tuple(f"div.c{n}" for n in range(10))
    result, _ = run({MOBILE: OverflowInfo(375, 600, many)})
    assert "div.c0" in result.actual and "div.c9" not in result.actual


def test_without_a_connected_browser_the_error_reaches_the_runner():
    with pytest.raises(BrowserUnavailableError):
        NoHorizontalOverflow().run(CheckContext(http=FakeHttp(), site_url=SITE))
