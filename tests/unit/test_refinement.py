"""Уточнение результатов по HTML браузером (PB-027): QC-006, 007, 008, 011, 013."""

from dataclasses import replace

import pytest

from app.core.browser import BrowserUnavailableError, RenderedPage
from app.core.browser_session import BrowserSession
from app.core.checks.context import CheckContext
from app.core.checks.links import LinksAreWellFormed, NavigationFound
from app.core.checks.structure import BodyIsNotEmpty, HtmlStructure, PageHasTitle
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp, page

NO_TITLE = '<html lang="ru"><head></head><body><p>текст</p></body></html>'
SPA = (
    '<html lang="ru"><head><title>t</title></head>'
    '<body><div id="root"></div><script src="/app.js"></script></body></html>'
)
NO_LANG = "<html><head><title>t</title></head><body>текст</body></html>"
PLACEHOLDERS = '<html lang="ru"><body><a href="#">x</a><a href="/ok">y</a>текст</body></html>'
NO_NAV = '<html lang="ru"><head><title>t</title></head><body><p>текст</p></body></html>'
GOOD = (
    '<html lang="ru"><head><title>Тест</title></head>'
    '<body><header><nav><a href="/a">A</a></nav></header><p>текст</p></body></html>'
)


def rendered(**changes):
    base = RenderedPage(
        title="Заголовок",
        lang="ru",
        text_length=120,
        visual_count=0,
        hrefs=("/a", "/b"),
        has_navigation=True,
    )
    return replace(base, **changes)


def setup(html, snapshot=None, with_browser=True, factory_error=None):
    browser = FakeBrowser()
    if snapshot is not None:
        browser.rendered_snapshot = snapshot
    session = (
        BrowserSession(FakeFactory(browser, error=factory_error), SITE) if with_browser else None
    )
    context = CheckContext(
        http=FakeHttp(response=page(body=html.encode("utf-8"))), site_url=SITE, browser=session
    )
    return context, browser


def run(check, html, snapshot=None, **kwargs):
    context, browser = setup(html, snapshot, **kwargs)
    return check().run(context), browser


def both_views(result):
    assert "В браузере" in result.actual and "исходному HTML" in result.actual


# QC-006: title


def test_qc006_title_set_by_scripts_is_found_in_the_browser():
    result, _ = run(PageHasTitle, NO_TITLE, rendered(title="Магазин"))
    assert result.status is CheckStatus.PASSED
    both_views(result)
    assert "Магазин" in result.actual


def test_qc006_stays_a_warning_when_the_browser_finds_no_title_either():
    result, _ = run(PageHasTitle, NO_TITLE, rendered(title=None))
    assert result.status is CheckStatus.WARNING
    both_views(result)
    assert result.reason


def test_qc006_html_result_without_a_warning_is_not_refined_and_browser_is_not_used():
    result, browser = run(PageHasTitle, GOOD)
    assert result.status is CheckStatus.PASSED and browser.rendered_calls == 0
    assert browser.opens == []


# QC-007: страница не пуста


@pytest.mark.parametrize(
    "snapshot",
    [rendered(text_length=500), rendered(text_length=0, visual_count=3)],
)
def test_qc007_script_built_page_with_content_in_the_browser_passes(snapshot):
    result, _ = run(BodyIsNotEmpty, SPA, snapshot)
    assert result.status is CheckStatus.PASSED
    both_views(result)


def test_qc007_page_empty_in_the_browser_as_well_fails():
    result, _ = run(BodyIsNotEmpty, SPA, rendered(text_length=0, visual_count=0))
    assert result.status is CheckStatus.FAILED
    both_views(result)


def test_qc007_html_failure_without_scripts_is_left_as_it_was():
    result, browser = run(BodyIsNotEmpty, "<html><body></body></html>")
    assert result.status is CheckStatus.FAILED and browser.rendered_calls == 0


# QC-008: структура


def test_qc008_lang_set_by_scripts_makes_the_structure_complete():
    result, _ = run(HtmlStructure, NO_LANG, rendered(lang="ru"))
    assert result.status is CheckStatus.PASSED
    both_views(result)


def test_qc008_stays_a_warning_when_the_rendered_page_has_no_lang():
    result, _ = run(HtmlStructure, NO_LANG, rendered(lang=None))
    assert result.status is CheckStatus.WARNING
    assert "lang" in result.actual


# QC-011: ссылки


def test_qc011_placeholders_replaced_by_scripts_pass_in_the_browser():
    result, _ = run(LinksAreWellFormed, PLACEHOLDERS, rendered(hrefs=("/a", "/b")))
    assert result.status is CheckStatus.PASSED
    both_views(result)


def test_qc011_placeholders_that_remain_in_the_browser_stay_a_warning():
    result, _ = run(
        LinksAreWellFormed, PLACEHOLDERS, rendered(hrefs=("#", "javascript:void(0)", "/ok"))
    )
    assert result.status is CheckStatus.WARNING
    assert "2 из 3" in result.actual


def test_qc011_links_built_by_scripts_are_checked_in_the_browser():
    result, _ = run(LinksAreWellFormed, SPA, rendered(hrefs=("/a", "/b", "/c")))
    assert result.status is CheckStatus.PASSED
    assert "3" in result.actual


def test_qc011_no_links_in_the_browser_either_is_skipped_with_a_reason():
    result, _ = run(LinksAreWellFormed, SPA, rendered(hrefs=()))
    assert result.status is CheckStatus.SKIPPED
    assert result.reason


# QC-013: навигация


def test_qc013_navigation_built_by_scripts_is_found_in_the_browser():
    result, _ = run(NavigationFound, NO_NAV, rendered(has_navigation=True))
    assert result.status is CheckStatus.PASSED
    both_views(result)


def test_qc013_stays_a_warning_without_navigation_in_the_browser():
    result, _ = run(NavigationFound, NO_NAV, rendered(has_navigation=False))
    assert result.status is CheckStatus.WARNING
    both_views(result)


# Общие свойства уточнения


def test_without_a_browser_the_html_result_is_returned_unchanged():
    result, _ = run(PageHasTitle, NO_TITLE, with_browser=False)
    assert result.status is CheckStatus.WARNING
    assert "В браузере" not in result.actual


def test_unavailable_browser_keeps_the_html_result_and_says_the_refinement_was_not_done():
    result, _ = run(
        PageHasTitle, NO_TITLE, factory_error=BrowserUnavailableError("Chrome не найден")
    )
    assert result.status is CheckStatus.WARNING
    assert "не выполнено" in result.reason and "Chrome не найден" in result.reason


def test_rendered_page_is_probed_once_for_all_five_checks():
    html = "<html><head></head><body><a href='#'>x</a></body></html>"  # много предупреждений
    context, browser = setup(html, rendered())
    for check in (PageHasTitle, BodyIsNotEmpty, HtmlStructure, LinksAreWellFormed, NavigationFound):
        check().run(context)
    assert browser.rendered_calls == 1


def test_refined_result_keeps_identity_of_the_check_and_a_valid_reason_rule():
    result, _ = run(NavigationFound, NO_NAV, rendered(has_navigation=False))
    assert result.check_id == "QC-013" and result.severity.value == "LOW"
    assert result.reason  # WARNING требует причину
