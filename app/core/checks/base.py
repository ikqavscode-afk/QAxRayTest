from typing import Protocol

from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity


class Check(Protocol):
    """Контракт проверки. Инфраструктурные ошибки (сеть и т. п.) проверка не ловит:
    их превращает в статус BLOCKED исполнитель запуска."""

    check_id: str
    title: str
    severity: Severity

    def run(self, context: CheckContext) -> CheckResult: ...
