from app.core.browser import ImagesSnapshot, PageInfo
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

    def open(self, url, viewport):
        self.opens.append((url, viewport))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return PageInfo(url=url, title="Тест", load_ms=10)

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
