import threading
import time
from collections.abc import Callable

from app.core.browser import (
    BrowserBudgetError,
    BrowserError,
    BrowserFactory,
    BrowserNetworkError,
    BrowserPort,
    BrowserUnavailableError,
    PageInfo,
    Viewport,
)

MAX_OPENS = 6  # две ширины окна по три попытки
BUDGET_SECONDS = 90.0
NETWORK_RETRIES = 2  # ADR-0002: повтор только сетевых сбоев, не более 2 раз


class BrowserSession:
    """Жизненный цикл браузера на один запуск: ленивый старт, лимиты, закрытие.

    Браузер запускается при первом запросе страницы; запуск без браузерных проверок его не
    создаёт. Сбой запуска и сбой открытия запоминаются, поэтому браузер не перезапускается
    и страница не открывается повторно ради одного и того же отказа."""

    def __init__(
        self,
        factory: BrowserFactory,
        url: str,
        *,
        max_opens: int = MAX_OPENS,
        budget_seconds: float = BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._url = url
        self._max_opens = max_opens
        self._budget = budget_seconds
        self._clock = clock
        self._port: BrowserPort | None = None
        self._start_error: BrowserError | None = None
        self._started_at: float | None = None
        self._opens = 0
        self._current: Viewport | None = None
        self._info: PageInfo | None = None
        self._failures: dict[Viewport, BrowserError] = {}
        self._closed = False
        self._state_lock = threading.Lock()  # закрытие допускается из другого потока

    @property
    def info(self) -> PageInfo | None:
        """Сведения о текущей открытой странице (None, пока страница не открыта)."""
        return self._info

    def page(self, viewport: Viewport) -> BrowserPort:
        """Браузер с открытой страницей нужной ширины окна."""
        if self._closed:
            raise BrowserUnavailableError("Сессия браузера закрыта")
        if viewport in self._failures:
            raise self._failures[viewport]
        port = self._start()
        if self._current == viewport:
            return port
        self._check_budget()
        try:
            self._info = self._open_with_retries(port, viewport)
        except BrowserError as error:
            self._current = None
            self._failures[viewport] = error
            raise
        self._current = viewport
        return port

    def close(self) -> None:
        """Закрывает браузер. Идемпотентно; ошибка закрытия не должна скрыть результаты проверок."""
        with self._state_lock:
            self._closed = True
            port, self._port = self._port, None
        if port is None:
            return
        try:
            port.close()
        except BrowserError:
            pass

    def _start(self) -> BrowserPort:
        if self._start_error is not None:
            raise self._start_error
        if self._port is None:
            self._started_at = self._clock()
            try:
                port = self._factory()
            except BrowserError as error:
                self._start_error = error
                raise
            with self._state_lock:
                if self._closed:  # остановка пришла во время запуска браузера
                    port.close()
                    raise BrowserUnavailableError("Сессия браузера закрыта")
                self._port = port
        return self._port

    def _check_budget(self) -> None:
        if self._opens >= self._max_opens:
            raise BrowserBudgetError(f"Превышено число открытий страницы ({self._max_opens})")
        if self._started_at is not None and self._clock() - self._started_at > self._budget:
            raise BrowserBudgetError(f"Исчерпан лимит времени браузера ({self._budget:g} с)")

    def _open_with_retries(self, port: BrowserPort, viewport: Viewport) -> PageInfo:
        last: BrowserNetworkError | None = None
        for _ in range(NETWORK_RETRIES + 1):
            self._check_budget()
            self._opens += 1
            try:
                return port.open(self._url, viewport)
            except BrowserNetworkError as error:
                last = error
        assert last is not None
        raise last
