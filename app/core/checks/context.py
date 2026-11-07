from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from app.core.browser import (
    DESKTOP,
    BrowserPort,
    BrowserUnavailableError,
    RenderedPage,
    Viewport,
)
from app.core.browser_session import BrowserSession
from app.core.html_summary import HtmlSummary, summarize_html
from app.core.ports import HttpClient, HttpClientError, HttpResponse

T = TypeVar("T")
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
        api_expected_statuses: Mapping[str, tuple[int, ...]] | None = None,
        browser: BrowserSession | None = None,
    ) -> None:
        self.http = http
        self.site_url = site_url
        self.api_docs_url = api_docs_url
        self.api_base_url = api_base_url
        self.api_paths = api_paths
        self.api_expected_statuses = dict(api_expected_statuses or {})
        self._browser = browser
        self._cache: dict[str, Any] = {}
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

    def get_cached(self, url: str) -> HttpResponse:
        """GET с кэшем: каждый адрес запрашивается один раз, сбой тоже запоминается."""
        if url not in self._api_cache:
            try:
                self._api_cache[url] = self.http.get(url)
            except HttpClientError as error:
                self._api_cache[url] = error
        outcome = self._api_cache[url]
        if isinstance(outcome, HttpClientError):
            raise outcome
        return outcome

    def api_get(self, url: str) -> HttpResponse:
        """GET к API (общий кэш с get_cached)."""
        return self.get_cached(url)

    def api_root(self) -> HttpResponse:
        """Ответ на базовый адрес API."""
        if self.api_base_url is None:
            raise ValueError("Базовый адрес API не указан")
        return self.api_get(self.api_base_url)

    def api_unknown_path(self) -> HttpResponse:
        """Ответ API на несуществующий путь."""
        return self.api_get(self.api_url(UNKNOWN_API_PATH))

    def api_docs(self) -> HttpResponse:
        """Ответ на запрос документации API."""
        if self.api_docs_url is None:
            raise ValueError("Адрес документации API не указан")
        return self.api_get(self.api_docs_url)

    def browser_page(self, viewport: Viewport = DESKTOP) -> BrowserPort:
        """Браузер с открытой страницей сайта нужной ширины окна (запускается лениво)."""
        if self._browser is None:
            raise BrowserUnavailableError("Браузер не подключён к запуску")
        return self._browser.page(viewport)

    def cached(self, key: str, factory: Callable[[], T]) -> T:
        """Результат тяжёлой пробы на весь запуск: factory выполняется один раз для ключа."""
        if key not in self._cache:
            self._cache[key] = factory()
        value: T = self._cache[key]
        return value

    @property
    def has_browser(self) -> bool:
        return self._browser is not None

    def rendered_page(self) -> RenderedPage:
        """Снимок отрисованной страницы; общий на запуск для проверок, которые уточняют результат по HTML."""
        return self.cached("rendered", lambda: self.browser_page(DESKTOP).rendered_page())
