import logging
import threading
from collections.abc import Callable
from typing import Protocol

from app.core.execution.runner import ProgressCallback
from app.core.security.redactor import Redactor
from app.schemas.report import RunReport
from app.schemas.run import RunRecord
from app.schemas.target import RunTarget
from app.services.run_control import RunControl
from app.services.run_store import RunStore

logger = logging.getLogger(__name__)

INTERNAL_ERROR = "Внутренняя ошибка приложения"
Starter = Callable[[Callable[[], None]], None]


class Service(Protocol):
    def validate(self, target: RunTarget) -> object: ...

    def run(
        self,
        target: RunTarget,
        *,
        on_progress: ProgressCallback | None = None,
        control: RunControl | None = None,
    ) -> RunReport: ...


class RunManager:
    """Запускает проверки в фоне, ведёт прогресс в хранилище и выполняет остановку.

    Запуск не блокирует запрос: start возвращает запись со статусом RUNNING, пока проверки идут
    в отдельном потоке. Адрес проверяется политикой до создания записи и потока."""

    def __init__(
        self,
        service: Service,
        store: RunStore,
        *,
        starter: Starter | None = None,
        redactor: Redactor | None = None,
        max_run_seconds: float | None = None,
    ) -> None:
        self._service = service
        self._redactor = redactor or Redactor()  # секреты маскируются до сохранения
        self._store = store
        self._starter = starter  # подмена способа запуска (в тестах: синхронно)
        self._max_run_seconds = max_run_seconds  # предел времени запуска; None: без предела
        self._lock = threading.Lock()
        self._controls: dict[str, RunControl] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self, target: RunTarget) -> RunRecord:
        self._service.validate(target)  # TargetRejectedError: ничего не создаётся
        record = self._store.create(target)  # RunLimitError: слишком много одновременных
        control = RunControl()
        with self._lock:
            self._controls[record.run_id] = control
        job = lambda: self._execute_with_deadline(record.run_id, target, control)
        if self._starter is not None:
            self._starter(job)
        else:
            thread = threading.Thread(target=job, daemon=True, name=f"run-{record.run_id[:8]}")
            with self._lock:
                self._threads[record.run_id] = thread
            thread.start()
        return record

    def stop(self, run_id: str) -> bool:
        """Просит остановить запуск: флаг для исполнителя и немедленное закрытие браузера."""
        if not self._store.request_stop(run_id):
            return False
        with self._lock:
            control = self._controls.get(run_id)
        if control is not None:
            control.request_stop()
        return True

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        """Ждёт конца фонового потока (для тестов и остановки приложения). True: поток завершён."""
        with self._lock:
            thread = self._threads.get(run_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _execute_with_deadline(self, run_id: str, target: RunTarget, control: RunControl) -> None:
        """Запуск с пределом времени: по истечении срока запрашивается остановка (браузер закрывается)."""
        timer: threading.Timer | None = None
        if self._max_run_seconds is not None:
            timer = threading.Timer(self._max_run_seconds, self._on_timeout, (run_id, control))
            timer.name = f"run-timeout-{run_id[:8]}"
            timer.daemon = True
            timer.start()
        try:
            self._execute(run_id, target, control)
        finally:
            if timer is not None:
                timer.cancel()

    @staticmethod
    def _on_timeout(run_id: str, control: RunControl) -> None:
        logger.warning("Запуск %s остановлен по пределу времени", run_id[:8])
        control.request_stop()

    def _execute(self, run_id: str, target: RunTarget, control: RunControl) -> None:
        def on_progress(partial: RunReport, total: int) -> None:
            self._store.update_progress(run_id, self._redactor.redact_report(partial), total)

        try:
            report = self._redactor.redact_report(
                self._service.run(target, on_progress=on_progress, control=control)
            )
            record = self._store.get(run_id)
            total = record.total if record is not None else len(report.results)
            if control.should_stop() and len(report.results) < total:
                self._store.stop(run_id, report)
            else:
                self._store.finish(run_id, report)
        except KeyError:
            logger.warning("Запуск %s исчез из хранилища до завершения", run_id)
        except Exception:
            logger.exception("Запуск %s завершился внутренней ошибкой", run_id)
            try:
                self._store.fail(run_id, INTERNAL_ERROR)
            except KeyError:
                pass
        finally:
            with self._lock:
                self._controls.pop(run_id, None)
                self._threads.pop(run_id, None)
