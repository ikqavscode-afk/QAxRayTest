from app.core.html_summary import HtmlSummary, summarize_html
from app.core.ports import HttpClient, HttpClientError, HttpResponse

UNKNOWN_API_PATH = "/qaxray-unknown-path-check"  # заведомо несуществующий путь для QC-025


class CheckContext:
    """Общие данные запуска. Ответ главной страницы запрашивается один раз для всех проверок."""

    def __init__(
        self,
        http: HttpClient,
        site_url: str,
        *,
        api_docs_url: str | None = None,
        api_base_url: str | None = None,
        api_paths: tuple[str, ...] = (),
    ) -> None:
        self.http = http
        self.site_url = site_url
        self.api_docs_url = api_docs_url
        self.api_base_url = api_base_url
        self.api_paths = api_paths
        self._homepage: HttpResponse | None = None
        self._homepage_error: HttpClientError | None = None
        self._homepage_html: HtmlSummary | None = None
        self._api_cache: dict[str, HttpResponse | HttpClientError] = {}

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

    def api_url(self, path: str) -> str:
        """Полный адрес запроса к API: базовый адрес + путь (путь проверен в RunTarget)."""
        if self.api_base_url is None:
            raise ValueError("Базовый адрес API не указан")
        return self.api_base_url.rstrip("/") + path

    def api_get(self, url: str) -> HttpResponse:
        """GET к API. Каждый адрес запрашивается один раз, сбой тоже запоминается."""
        if url not in self._api_cache:
            try:
                self._api_cache[url] = self.http.get(url)
            except HttpClientError as error:
                self._api_cache[url] = error
        outcome = self._api_cache[url]
        if isinstance(outcome, HttpClientError):
            raise outcome
        return outcome

    def api_root(self) -> HttpResponse:
        """Ответ на базовый адрес API."""
        if self.api_base_url is None:
            raise ValueError("Базовый адрес API не указан")
        return self.api_get(self.api_base_url)

    def api_unknown_path(self) -> HttpResponse:
        """Ответ API на несуществующий путь."""
        return self.api_get(self.api_url(UNKNOWN_API_PATH))
