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


def page(status=200, elapsed_ms=120, url=SITE, headers=None, redirects=None):
    return HttpResponse(
        url=url,
        status_code=status,
        headers={"content-type": "text/html"} if headers is None else headers,
        body=b"<html></html>",
        truncated=False,
        elapsed_ms=elapsed_ms,
        redirects=redirects or [],
    )


def context_for(http):
    return CheckContext(http=http, site_url=SITE)
