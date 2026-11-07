import pytest

from app.core.browser import DESKTOP, BrowserUnavailableError, ControlInfo, FieldsSnapshot
from app.core.browser_session import BrowserSession
from app.core.checks.accessibility import MAX_FIELDS, FieldsHaveLabels
from app.core.checks.context import CheckContext
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp


def field(descriptor="input#a", source="label-for", kind="input:text"):
    return ControlInfo(kind=kind, descriptor=descriptor, name_source=source)


def run(*items, total=None):
    browser = FakeBrowser()
    browser.fields_snapshot = FieldsSnapshot(
        total=len(items) if total is None else total, items=items
    )
    session = BrowserSession(FakeFactory(browser), SITE)
    context = CheckContext(http=FakeHttp(), site_url=SITE, browser=session)
    return FieldsHaveLabels().run(context), browser


def test_passes_when_every_visible_field_has_a_real_label():
    result, browser = run(field("input#a"), field("select#b", "aria-label", "select"))
    assert result.check_id == "QC-015"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM
    assert "2" in result.actual
    assert browser.opens[0][1] == DESKTOP
    assert browser.fields_limits == [MAX_FIELDS]


@pytest.mark.parametrize("source", ["label-for", "label-wrap", "aria-label", "aria-labelledby"])
def test_label_aria_label_and_aria_labelledby_count_as_labels(source):
    result, _ = run(field(source=source))
    assert result.status is CheckStatus.PASSED


def test_fields_without_any_label_are_a_warning_naming_them():
    result, _ = run(field("input#ok"), field("input#q", None), field("textarea#msg", None))
    assert result.status is CheckStatus.WARNING
    assert "2 из 3" in result.actual
    assert "input#q" in result.actual and "textarea#msg" in result.actual
    assert "input#ok" not in result.actual
    assert result.reason


@pytest.mark.parametrize("source", ["placeholder", "title"])
def test_placeholder_or_title_alone_is_not_a_label(source):
    result, _ = run(field("input#mail", source))
    assert result.status is CheckStatus.WARNING
    assert f"только {source}" in result.actual


def test_the_check_never_fails_it_is_a_heuristic():
    result, _ = run(*[field(f"input#{n}", None) for n in range(10)])
    assert result.status is not CheckStatus.FAILED


def test_only_the_first_culprits_are_listed():
    result, _ = run(*[field(f"input#f{n}", None) for n in range(12)])
    assert "input#f0" in result.actual and "input#f11" not in result.actual
    assert "и ещё" in result.actual


def test_report_says_when_only_the_first_fields_were_checked():
    result, _ = run(*[field(f"input#f{n}") for n in range(MAX_FIELDS)], total=MAX_FIELDS + 10)
    assert result.status is CheckStatus.PASSED
    assert f"{MAX_FIELDS} из {MAX_FIELDS + 10}" in result.actual


def test_no_visible_fields_is_skipped_with_a_reason():
    result, _ = run()
    assert result.status is CheckStatus.SKIPPED
    assert "форм" in result.reason


def test_without_a_connected_browser_the_error_reaches_the_runner():
    with pytest.raises(BrowserUnavailableError):
        FieldsHaveLabels().run(CheckContext(http=FakeHttp(), site_url=SITE))
