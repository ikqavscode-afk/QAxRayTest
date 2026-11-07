from app.core.browser import (
    AuthSnapshot,
    ButtonsSnapshot,
    FieldsSnapshot,
    ImagesSnapshot,
    OverflowInfo,
    PageInfo,
    TabSequence,
)
from app.core.checks.context import CheckContext
from app.core.ports import HttpResponse

SITE = "https://example.com/"


class FakeHttp:
    """Заглушка клиента: отдаёт заранее заданный ответ или ошибку и считает вызовы.

    routes: ответы или ошибки по конкретным адресам (для проверок API с несколькими запросами)."""

    def __init__(self, response=None, error=None, routes=None):
        self.response = response
        self.error = error
        self.routes = routes or {}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url in self.routes:
            outcome = self.routes[url]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self.error:
            raise self.error
        return self.response


HEALTHY_HTML = (
    '<!DOCTYPE html><html lang="ru"><head><title>Тестовая страница</title></head>'
    '<body><header><nav><a href="/about">О нас</a></nav></header><p>Привет</p></body></html>'
)


def page(status=200, elapsed_ms=120, url=SITE, headers=None, redirects=None, body=None):
    return HttpResponse(
        url=url,
        status_code=status,
        headers={"content-type": "text/html"} if headers is None else headers,
        body=HEALTHY_HTML.encode("utf-8") if body is None else body,
        truncated=False,
        elapsed_ms=elapsed_ms,
        redirects=redirects or [],
    )


def context_for(http):
    return CheckContext(http=http, site_url=SITE)


class StubService:
    """Заглушка RunService для веб-тестов: возвращает отчёт или бросает ошибку."""

    def __init__(self, report=None, error=None):
        self.report = report
        self.error = error
        self.targets = []

    def run(self, target):
        self.targets.append(target)
        if self.error:
            raise self.error
        return self.report


class FakeBrowser:
    """Заглушка порта браузера: записывает открытия и закрытия, исходы задаются очередью.

    outcomes: по одному на каждый вызов open: исключение (будет брошено) или PageInfo;
    когда очередь пуста, открытие успешно."""

    def __init__(self, outcomes=None, close_error=None):
        self.outcomes = list(outcomes or [])
        self.close_error = close_error
        self.opens = []
        self.closed = 0
        self.images_snapshot = ImagesSnapshot(total=0, items=())
        self.images_limits = []
        self.buttons_snapshot = ButtonsSnapshot(total=0, items=())
        self.buttons_limits = []
        self.fields_snapshot = FieldsSnapshot(total=0, items=())
        self.fields_limits = []
        self.tab_result = TabSequence(interactive_total=0, steps=())
        self.tab_limits = []
        self.auth_snapshot = AuthSnapshot(login_links=(), signup_links=(), forms=())
        self.auth_calls = []
        self.current = None
        self.overflow_results = {}  # окно -> OverflowInfo; по умолчанию переполнения нет

    def open(self, url, viewport):
        self.opens.append((url, viewport))
        self.current = viewport
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return PageInfo(url=url, title="Тест", load_ms=10)

    def auth_signals(self, login_pattern, signup_pattern):
        self.auth_calls.append((login_pattern, signup_pattern))
        return self.auth_snapshot

    def tab_sequence(self, max_presses):
        self.tab_limits.append(max_presses)
        return self.tab_result

    def form_fields(self, limit):
        self.fields_limits.append(limit)
        return self.fields_snapshot

    def buttons(self, limit):
        self.buttons_limits.append(limit)
        return self.buttons_snapshot

    def overflow(self):
        default = OverflowInfo(self.current.width, self.current.width, ())
        return self.overflow_results.get(self.current, default)

    def images(self, limit):
        self.images_limits.append(limit)
        return self.images_snapshot

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


class FakeFactory:
    """Фабрика браузеров: считает запуски, может отказать при старте."""

    def __init__(self, browser=None, error=None):
        self.browser = browser or FakeBrowser()
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.browser


class FakeClock:
    """Управляемые часы для проверки лимитов времени без ожидания."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now
