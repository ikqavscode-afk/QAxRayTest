from app.core.checks.context import CheckContext
from app.core.ports import HttpResponse

SITE = "https://example.com/"


class FakeHttp:
    """Заглушка клиента: отдаёт заранее заданный ответ или ошибку и считает вызовы."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.response


HEALTHY_HTML = (
    '<!DOCTYPE html><html lang="ru"><head><title>Тестовая страница</title></head>'
    "<body><p>Привет</p></body></html>"
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
        self.urls = []

    def run(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.report
