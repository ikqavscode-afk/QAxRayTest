import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from app.schemas.report import RunReport
from app.schemas.run import RunRecord, RunStatus
from app.schemas.target import RunTarget

DEFAULT_TTL_SECONDS = 3600.0
DEFAULT_MAX_RUNS = 50


class RunLimitError(RuntimeError):
    """Все места заняты работающими запусками: новый пока не принимается."""


class RunStore(Protocol):
    """Контракт хранилища запусков: сейчас память, позже файл или база без правок веб-слоя."""

    def create(self, target: RunTarget) -> RunRecord: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def update_progress(self, run_id: str, report: RunReport, total: int) -> RunRecord: ...

    def request_stop(self, run_id: str) -> bool: ...

    def stop(self, run_id: str, report: RunReport) -> RunRecord: ...

    def finish(self, run_id: str, report: RunReport) -> RunRecord: ...

    def fail(self, run_id: str, reason: str) -> RunRecord: ...

    def discard(self, run_id: str) -> None: ...


def _new_id() -> str:
    return secrets.token_urlsafe(16)  # неугадываемый: адрес результата нельзя перебрать


class InMemoryRunStore:
    """Запуски в памяти процесса: срок хранения, ограничение числа, безопасность потоков.

    Интерфейс рассчитан на замену хранилища (файл, база) без изменений в веб-слое. Работающие
    запуски не вытесняются; при нехватке места вытесняется самый старый завершённый."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_runs: int = DEFAULT_MAX_RUNS,
        max_running: int | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_runs = max_runs
        self._max_running = max_running
        self._clock = clock
        self._id_factory = id_factory
        self._lock = threading.Lock()
        self._runs: dict[str, RunRecord] = {}

    def create(self, target: RunTarget) -> RunRecord:
        with self._lock:
            self._purge_expired()
            if len(self._runs) >= self._max_runs:
                self._evict_oldest_finished()
            if len(self._runs) >= self._max_runs:
                raise RunLimitError("Слишком много одновременных запусков")
            running = sum(1 for rec in self._runs.values() if rec.status is RunStatus.RUNNING)
            if self._max_running is not None and running >= self._max_running:
                raise RunLimitError("Слишком много одновременных запусков")
            record = RunRecord(
                run_id=self._id_factory(),
                target=target,
                status=RunStatus.RUNNING,
                created_at=self._clock(),
            )
            self._runs[record.run_id] = record
            return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            self._purge_expired()
            return self._runs.get(run_id)

    def update_progress(self, run_id: str, report: RunReport, total: int) -> RunRecord:
        """Отчёт по выполненным проверкам и общее число проверок. Для завершённого запуска
        запоздавшее обновление игнорируется."""
        with self._lock:
            record = self._runs[run_id]
            if record.status is not RunStatus.RUNNING:
                return record
            updated = replace(record, report=report, total=total)
            self._runs[run_id] = updated
            return updated

    def request_stop(self, run_id: str) -> bool:
        """Отмечает просьбу остановить работающий запуск. False: запуска нет или он уже закончен."""
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.status is not RunStatus.RUNNING:
                return False
            self._runs[run_id] = replace(record, stop_requested=True)
            return True

    def stop(self, run_id: str, report: RunReport) -> RunRecord:
        return self._update(
            run_id, status=RunStatus.STOPPED, report=report, finished_at=self._clock()
        )

    def finish(self, run_id: str, report: RunReport) -> RunRecord:
        return self._update(
            run_id, status=RunStatus.COMPLETED, report=report, finished_at=self._clock()
        )

    def fail(self, run_id: str, reason: str) -> RunRecord:
        return self._update(
            run_id, status=RunStatus.FAILED, error=reason, finished_at=self._clock()
        )

    def discard(self, run_id: str) -> None:
        """Убирает запуск, который не начинался (например, адрес отклонён политикой)."""
        with self._lock:
            self._runs.pop(run_id, None)

    def _update(self, run_id: str, **changes: object) -> RunRecord:
        with self._lock:
            record = self._runs[run_id]  # KeyError: запуск неизвестен или уже убран
            updated = replace(record, **changes)  # type: ignore[arg-type]
            self._runs[run_id] = updated
            return updated

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [rid for rid, rec in self._runs.items() if now - rec.created_at > self._ttl]
        for run_id in expired:
            del self._runs[run_id]

    def _evict_oldest_finished(self) -> None:
        finished = [rec for rec in self._runs.values() if rec.status is not RunStatus.RUNNING]
        if finished:
            del self._runs[min(finished, key=lambda rec: rec.created_at).run_id]
