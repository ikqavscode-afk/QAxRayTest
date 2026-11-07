import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class RunControl:
    """Управление одним запуском из другого потока: флаг остановки и аварийное закрытие.

    Исполнитель проверяет флаг между проверками. Аварийный обработчик (закрытие браузера)
    срабатывает сразу при остановке и прерывает долгую загрузку страницы, не дожидаясь конца
    проверки. Обработчик вызывается ровно один раз, даже при одновременных запросах остановки."""

    def __init__(self) -> None:
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._abort: Callable[[], None] | None = None
        self._abort_done = False

    def should_stop(self) -> bool:
        return self._stopped.is_set()

    def request_stop(self) -> None:
        self._stopped.set()
        self._run_abort()

    def set_abort(self, abort: Callable[[], None]) -> None:
        """Обработчик аварийной остановки; если остановка уже запрошена, вызывается сразу."""
        with self._lock:
            self._abort = abort
        if self._stopped.is_set():
            self._run_abort()

    def _run_abort(self) -> None:
        with self._lock:
            abort = self._abort
            if abort is None or self._abort_done:
                return
            self._abort_done = True
        try:
            abort()
        except Exception:
            logger.warning("Аварийная остановка завершилась ошибкой", exc_info=True)
