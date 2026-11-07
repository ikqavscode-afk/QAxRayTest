import pytest

from app.core.checks.links import LinksAreWellFormed, NavigationFound
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import FakeHttp, context_for, page

NOT_HTML = b'{"status": "ok"}'
SPA = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'


def run(check, html):
    body = html.encode("utf-8") if isinstance(html, str) else html
    return check.run(context_for(FakeHttp(response=page(body=body))))


# QC-011: корректные href


def test_qc011_passes_when_all_links_are_real():
    html = '<body><a href="/a">A</a><a href="https://example.org/b">B</a></body>'
    result = run(LinksAreWellFormed(), html)
    assert result.check_id == "QC-011"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM
    assert "2" in result.actual


@pytest.mark.parametrize(
    "href", ["", "   ", "#", "javascript:void(0)", "JavaScript:alert(1)", " javascript:;"]
)
def test_qc011_warns_for_placeholder_links(href):
    result = run(LinksAreWellFormed(), f'<body><a href="{href}">x</a><a href="/ok">y</a></body>')
    assert result.status is CheckStatus.WARNING
    assert "1" in result.reason


def test_qc011_counts_bad_links_and_stays_warning_not_failed():
    html = '<body><a href="#">1</a><a href="">2</a><a href="javascript:;">3</a></body>'
    result = run(LinksAreWellFormed(), html)
    assert result.status is CheckStatus.WARNING
    assert "3 из 3" in result.actual


def test_qc011_anchor_to_section_is_not_a_placeholder():
    result = run(LinksAreWellFormed(), '<body><a href="#contacts">Контакты</a></body>')
    assert result.status is CheckStatus.PASSED


def test_qc011_checks_at_most_fifty_links():
    html = "<body>" + '<a href="#">x</a>' * 40 + '<a href="/ok">y</a>' * 30 + "</body>"
    result = run(LinksAreWellFormed(), html)
    assert "50" in result.actual
    assert "40 из 50" in result.actual


def test_qc011_warns_about_browser_when_page_built_by_scripts():
    result = run(LinksAreWellFormed(), SPA)
    assert result.status is CheckStatus.WARNING
    assert "браузер" in result.reason


def test_qc011_is_skipped_when_no_links_and_no_scripts():
    assert run(LinksAreWellFormed(), "<html><body>Текст</body></html>").status is (
        CheckStatus.SKIPPED
    )


def test_qc011_is_skipped_for_non_html():
    assert run(LinksAreWellFormed(), NOT_HTML).status is CheckStatus.SKIPPED


# QC-013: навигация


def test_qc013_passes_with_nav():
    result = run(NavigationFound(), "<html><body><nav><a href='/'>x</a></nav></body></html>")
    assert result.check_id == "QC-013"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW


def test_qc013_passes_with_links_in_header():
    result = run(NavigationFound(), "<body><header><a href='/'>Главная</a></header></body>")
    assert result.status is CheckStatus.PASSED


def test_qc013_warns_without_navigation():
    result = run(NavigationFound(), "<html><body><p>Текст</p></body></html>")
    assert result.status is CheckStatus.WARNING
    assert result.reason


def test_qc013_mentions_browser_for_script_built_page():
    result = run(NavigationFound(), SPA)
    assert result.status is CheckStatus.WARNING
    assert "браузер" in result.reason


def test_qc013_is_skipped_for_non_html():
    assert run(NavigationFound(), NOT_HTML).status is CheckStatus.SKIPPED
