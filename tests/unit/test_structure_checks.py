import pytest

from app.core.checks.structure import BodyIsNotEmpty, HtmlStructure, PageHasTitle
from app.core.ports import NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import FakeHttp, context_for, page

NOT_HTML = b'{"status": "ok"}'


def run(check, html):
    body = html.encode("utf-8") if isinstance(html, str) else html
    return check.run(context_for(FakeHttp(response=page(body=body))))


def test_context_parses_homepage_once_and_caches_the_summary():
    http = FakeHttp(response=page())
    context = context_for(http)
    assert context.homepage_html() is context.homepage_html()
    assert len(http.calls) == 1


def test_context_propagates_network_errors_from_homepage_html():
    context = context_for(FakeHttp(error=NetworkError("нет ответа")))
    with pytest.raises(NetworkError):
        context.homepage_html()


# QC-006: title


def test_qc006_passes_when_title_exists():
    result = run(PageHasTitle(), "<html><head><title>Мой сайт</title></head><body>x</body></html>")
    assert result.check_id == "QC-006"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM
    assert "Мой сайт" in result.actual


@pytest.mark.parametrize(
    "html",
    [
        "<html><head></head><body>x</body></html>",
        "<html><head><title>   </title></head><body>x</body></html>",
    ],
)
def test_qc006_warns_when_title_missing_and_mentions_scripts(html):
    result = run(PageHasTitle(), html)
    assert result.status is CheckStatus.WARNING
    assert "скрипт" in result.reason


def test_qc006_is_skipped_for_non_html():
    result = run(PageHasTitle(), NOT_HTML)
    assert result.status is CheckStatus.SKIPPED
    assert "HTML" in result.reason


# QC-007: непустое содержимое


def test_qc007_passes_when_there_is_visible_text():
    result = run(BodyIsNotEmpty(), "<html><body><p>Привет, мир</p></body></html>")
    assert result.check_id == "QC-007"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.HIGH
    assert "11" in result.actual


def test_qc007_warns_for_script_built_page_and_points_to_browser():
    html = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    result = run(BodyIsNotEmpty(), html)
    assert result.status is CheckStatus.WARNING
    assert "браузер" in result.reason


def test_qc007_fails_when_no_text_and_no_scripts():
    result = run(BodyIsNotEmpty(), "<html><body></body></html>")
    assert result.status is CheckStatus.FAILED


def test_qc007_is_skipped_for_non_html():
    result = run(BodyIsNotEmpty(), NOT_HTML)
    assert result.status is CheckStatus.SKIPPED


# QC-008: базовая структура


def test_qc008_passes_for_complete_structure():
    html = '<!DOCTYPE html><html lang="ru"><head></head><body>x</body></html>'
    result = run(HtmlStructure(), html)
    assert result.check_id == "QC-008"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW


@pytest.mark.parametrize(
    ("html", "missing"),
    [
        ("<html><head></head><body>x</body></html>", "lang"),
        ('<html lang="ru"><body>x</body></html>', "head"),
        ('<html lang="ru"><head></head><p>x</p></html>', "body"),
    ],
)
def test_qc008_warns_and_names_what_is_missing(html, missing):
    result = run(HtmlStructure(), html)
    assert result.status is CheckStatus.WARNING
    assert missing in result.reason


def test_qc008_fails_when_response_is_not_an_html_document():
    result = run(HtmlStructure(), NOT_HTML)
    assert result.status is CheckStatus.FAILED
    assert "html" in result.actual.lower()
