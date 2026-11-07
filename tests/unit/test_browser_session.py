import pytest

from app.core.browser import (
    DESKTOP,
    MOBILE,
    BrowserBlockedError,
    BrowserBudgetError,
    BrowserError,
    BrowserNetworkError,
    BrowserTimeoutError,
    BrowserUnavailableError,
    PageInfo,
    Viewport,
)
from app.core.browser_session import MAX_OPENS, BrowserSession
from app.core.checks.context import CheckContext
from tests.unit.support import SITE, FakeBrowser, FakeClock, FakeFactory, FakeHttp

URL = "https://example.com/"


def session(factory=None, clock=None, **kwargs):
    factory = factory or FakeFactory()
    return BrowserSession(factory, URL, clock=clock or FakeClock(), **kwargs), factory


# Размеры окна


def test_standard_viewports_match_the_checklist_sizes():
    assert (DESKTOP.width, MOBILE.width) == (1280, 375)


@pytest.mark.parametrize("size", [(100, 800), (1280, 100), (9999, 800), (1280, 9999), (0, 0)])
def test_unreasonable_viewport_sizes_are_rejected(size):
    with pytest.raises(ValueError, match="окн"):
        Viewport(*size)


# Ленивый запуск и открытие страницы


def test_browser_is_not_started_until_a_page_is_requested():
    _, factory = session()
    assert factory.calls == 0


def test_first_request_starts_the_browser_and_opens_the_site_address():
    sess, factory = session()
    port = sess.page(DESKTOP)
    assert factory.calls == 1
    assert factory.browser.opens == [(URL, DESKTOP)]
    assert port is factory.browser


def test_same_viewport_is_opened_once_but_changing_viewport_reopens():
    sess, factory = session()
    for viewport in (DESKTOP, DESKTOP, MOBILE, MOBILE, DESKTOP):
        sess.page(viewport)
    assert factory.calls == 1
    assert [v for _, v in factory.browser.opens] == [DESKTOP, MOBILE, DESKTOP]


def test_page_info_of_the_current_page_is_available():
    sess, factory = session()
    factory.browser.outcomes = [PageInfo(url="https://example.com/ru/", title="Ru", load_ms=5)]
    sess.page(DESKTOP)
    assert sess.info.url == "https://example.com/ru/"


# Повторы только сетевых сбоев (ADR-0002, правило 3)


def test_network_failure_is_retried_up_to_two_times_and_then_succeeds():
    sess, factory = session()
    factory.browser.outcomes = [BrowserNetworkError("обрыв"), BrowserNetworkError("обрыв")]
    sess.page(DESKTOP)
    assert len(factory.browser.opens) == 3


def test_after_three_network_failures_the_error_is_raised_and_cached():
    sess, factory = session()
    factory.browser.outcomes = [BrowserNetworkError("обрыв")] * 3
    for _ in range(2):
        with pytest.raises(BrowserNetworkError):
            sess.page(DESKTOP)
    assert len(factory.browser.opens) == 3  # повторный запрос не открывает страницу снова


def test_timeouts_and_other_failures_are_not_retried():
    sess, factory = session()
    factory.browser.outcomes = [BrowserTimeoutError("долго")]
    with pytest.raises(BrowserTimeoutError):
        sess.page(DESKTOP)
    assert len(factory.browser.opens) == 1


def test_failure_of_one_viewport_does_not_poison_the_other():
    sess, factory = session()
    factory.browser.outcomes = [BrowserTimeoutError("долго")]
    with pytest.raises(BrowserTimeoutError):
        sess.page(DESKTOP)
    assert sess.page(MOBILE) is factory.browser


def test_browser_start_failure_is_cached_and_not_retried():
    sess, factory = session(FakeFactory(error=BrowserUnavailableError("нет драйвера")))
    for _ in range(3):
        with pytest.raises(BrowserUnavailableError):
            sess.page(DESKTOP)
    assert factory.calls == 1


# Лимиты запуска


def test_number_of_navigations_is_limited():
    sess, factory = session()
    for number in range(MAX_OPENS):
        sess.page(DESKTOP if number % 2 == 0 else MOBILE)
    with pytest.raises(BrowserBudgetError, match="откры"):
        sess.page(DESKTOP if MAX_OPENS % 2 == 0 else MOBILE)
    assert len(factory.browser.opens) == MAX_OPENS


def test_time_budget_is_enforced_without_real_waiting():
    clock = FakeClock()
    sess, factory = session(clock=clock, budget_seconds=60)
    sess.page(DESKTOP)
    clock.now = 61
    with pytest.raises(BrowserBudgetError, match="врем"):
        sess.page(MOBILE)
    assert len(factory.browser.opens) == 1


# Закрытие


def test_close_without_start_does_not_create_a_browser():
    sess, factory = session()
    sess.close()
    assert factory.calls == 0


def test_close_closes_the_browser_once_and_is_idempotent():
    sess, factory = session()
    sess.page(DESKTOP)
    sess.close()
    sess.close()
    assert factory.browser.closed == 1


def test_close_error_is_swallowed_so_it_cannot_hide_check_results():
    sess, _ = session(FakeFactory(FakeBrowser(close_error=BrowserError("завис"))))
    sess.page(DESKTOP)
    sess.close()  # не бросает


def test_page_after_close_is_refused():
    sess, _ = session()
    sess.close()
    with pytest.raises(BrowserUnavailableError, match="закрыт"):
        sess.page(DESKTOP)


# Контекст запуска


def test_context_without_browser_reports_it_as_unavailable():
    context = CheckContext(http=FakeHttp(), site_url=SITE)
    with pytest.raises(BrowserUnavailableError, match="не подключён"):
        context.browser_page()


def test_context_opens_requested_viewport_through_the_session():
    sess, factory = session()
    context = CheckContext(http=FakeHttp(), site_url=SITE, browser=sess)
    assert context.browser_page(MOBILE) is factory.browser
    assert factory.browser.opens == [(URL, MOBILE)]


def test_blocked_navigation_is_not_retried_and_is_a_browser_error():
    sess, factory = session()
    factory.browser.outcomes = [BrowserBlockedError("запрещено политикой")]
    with pytest.raises(BrowserBlockedError):
        sess.page(DESKTOP)
    assert len(factory.browser.opens) == 1
    assert issubclass(BrowserBlockedError, BrowserError)
