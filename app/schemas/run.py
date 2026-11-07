from dataclasses import dataclass
from enum import Enum

from app.schemas.report import RunReport
from app.schemas.target import RunTarget


class RunStatus(str, Enum):
    """Статус запуска (ADR-0002). FAILED: внутренняя ошибка приложения, а не находка о сайте."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RunRecord:
    """Снимок запуска. Неизменяемый: хранилище выдаёт копии и заменяет их целиком."""

    run_id: str
    target: RunTarget
    status: RunStatus
    created_at: float
    report: RunReport | None = None
    error: str | None = None
    finished_at: float | None = None
