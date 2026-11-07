import pytest

from app.core.checks.links import MAX_INTERNAL_LINKS, InternalLinksAreReachable
from app.core.ports import NetworkError
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeHttp, context_for, page

BASE = "https://example.com"


def html(*hrefs, extra=""):
    links = "".join(f'<a href="{href}">x</a>' for href in hrefs)
    return f"<html lang='ru'><head><title>t</title></head><body>{links}{extra}</body></html>"


def run(body, routes=None, default=None):
    """Главная отдаёт body; остальные адреса берутся из routes или default (по умолчанию 200)."""
    routes = {SITE: page(body=body.encode("utf-8")), **(routes or {})}
    http = FakeHttp(response=default if default is not None else page(), routes=routes)
    return InternalLinksAreReachable().run(context_for(http)), http


# Какие ссылки считаются внутренними


def test_relative_and_same_host_absolute_links_are_checked_in_document_order():
    result, http = run(html("/a", "b", f"{BASE}/c"))
    assert http.calls == [SITE, f"{BASE}/a", f"{BASE}/b", f"{BASE}/c"]
    assert result.status is CheckStatus.PASSED


@pytest.mark.parametrize(
    "href",
    [
        "https://other.example.org/x",  # другой домен
        "//other.example.org/x",  # протокол-относительная ссылка на чужой хост
        "https://example.com:8443/x",  # другой порт
        "https://user@example.com/x",  # данные доступа в адресе
        "http://example.com.evil.example.org/x",  # похожий домен
        "mailto:a@example.com",
        "tel:+10000000",
        "javascript:void(0)",
        "#",
        "#section",
        "",
        "ftp://example.com/file",
    ],
)
def test_foreign_and_non_http_links_are_never_requested(href):
    result, http = run(html(href, "/real"))
    assert http.calls == [SITE, f"{BASE}/real"]
    assert result.status is CheckStatus.PASSED


def test_fragments_are_ignored_and_duplicates_requested_once():
    result, http = run(html("/a#x", "/a", "/a#y", f"{BASE}/a"))
    assert http.calls == [SITE, f"{BASE}/a"]
    assert "1" in result.actual


def test_link_to_the_homepage_itself_is_not_requested():
    _, http = run(html("/", f"{BASE}/#top", "/a"))
    assert http.calls == [SITE, f"{BASE}/a"]


def test_at_most_ten_links_are_requested():
    result, http = run(html(*[f"/p{number}" for number in range(15)]))
    assert len(http.calls) == 1 + MAX_INTERNAL_LINKS == 11
    assert str(MAX_INTERNAL_LINKS) in result.actual


def test_relative_links_resolve_against_the_final_homepage_address():
    final = page(url="https://example.com/ru/", body=html("page").encode("utf-8"))
    http = FakeHttp(response=page(), routes={SITE: final})
    InternalLinksAreReachable().run(context_for(http))
    assert http.calls == [SITE, "https://example.com/ru/page"]


# Статусы


def test_passes_when_all_internal_links_respond_with_2xx_or_3xx():
    routes = {f"{BASE}/a": page(status=200), f"{BASE}/b": page(status=301)}
    result, _ = run(html("/a", "/b"), routes)
    assert result.check_id == "QC-012"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.HIGH
    assert len(result.evidence) == 2


@pytest.mark.parametrize("status", [404, 410, 500, 503])
def test_broken_links_fail_and_name_the_path_and_status(status):
    routes = {f"{BASE}/gone": page(status=status)}
    result, _ = run(html("/ok", "/gone"), routes)
    assert result.status is CheckStatus.FAILED
    assert "/gone" in result.actual and str(status) in result.actual
    assert "/ok" not in result.actual


@pytest.mark.parametrize("status", [401, 403, 429])
def test_access_restrictions_are_a_warning_not_a_failure(status):
    routes = {f"{BASE}/private": page(status=status)}
    result, _ = run(html("/private"), routes)
    assert result.status is CheckStatus.WARNING
    assert "/private" in result.actual and result.reason


def test_network_error_on_one_link_is_a_warning_and_other_links_are_still_checked():
    routes = {f"{BASE}/slow": NetworkError("нет ответа"), f"{BASE}/b": page(status=200)}
    result, http = run(html("/slow", "/b"), routes)
    assert result.status is CheckStatus.WARNING
    assert "/slow" in result.actual
    assert f"{BASE}/b" in http.calls


def test_failed_wins_over_warning_and_both_are_listed():
    routes = {f"{BASE}/gone": page(status=404), f"{BASE}/private": page(status=403)}
    result, _ = run(html("/gone", "/private"), routes)
    assert result.status is CheckStatus.FAILED
    assert "/gone" in result.actual and "/private" in result.actual


# Условия пропуска и ошибки среды


def test_skipped_when_there_are_no_internal_links():
    result, http = run(html("https://other.example.org/x", "#"))
    assert result.status is CheckStatus.SKIPPED
    assert http.calls == [SITE]


def test_skipped_for_script_built_page_points_to_browser():
    body = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    result, _ = run(body)
    assert result.status is CheckStatus.SKIPPED
    assert "браузер" in result.reason


def test_skipped_for_non_html_response():
    result, http = run('{"status": "ok"}')
    assert result.status is CheckStatus.SKIPPED
    assert http.calls == [SITE]


def test_homepage_network_error_reaches_the_runner():
    context = context_for(FakeHttp(error=NetworkError("нет ответа")))
    with pytest.raises(NetworkError):
        InternalLinksAreReachable().run(context)


# Безопасность отчёта


def test_query_strings_do_not_leak_into_the_report():
    routes = {f"{BASE}/a?token=SECRET": page(status=404)}
    result, _ = run(html("/a?token=SECRET"), routes)
    text = result.actual + " ".join(result.evidence) + (result.reason or "")
    assert result.status is CheckStatus.FAILED
    assert "SECRET" not in text and "token" not in text
