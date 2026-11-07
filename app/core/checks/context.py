from app.core.html_summary import HtmlSummary, summarize_html
from app.core.ports import HttpClient, HttpClientError, HttpResponse


class CheckContext:
    """Общие данные запуска. Ответ главной страницы запрашивается один раз для всех проверок."""

    def __init__(self, http: HttpClient, site_url: str) -> None:
        self.http = http
        self.site_url = site_url
        self._homepage: HttpResponse | None = None
        self._homepage_error: HttpClientError | None = None
        self._homepage_html: HtmlSummary | None = None

    def homepage(self) -> HttpResponse:
        """Ответ главной страницы. Сбой тоже запоминается и повторно не запрашивается."""
        if self._homepage_error is not None:
            raise self._homepage_error
        if self._homepage is None:
            try:
                self._homepage = self.http.get(self.site_url)
            except HttpClientError as error:
                self._homepage_error = error
                raise
        return self._homepage

    def homepage_html(self) -> HtmlSummary:
        """Сводка по HTML главной страницы. Разбор выполняется один раз."""
        if self._homepage_html is None:
            self._homepage_html = summarize_html(self.homepage().body)
        return self._homepage_html
