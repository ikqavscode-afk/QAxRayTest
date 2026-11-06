from enum import Enum


class CheckStatus(str, Enum):
    """Статус результата проверки (см. docs/decisions/0002-statuses-and-stability.md)."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"

    @property
    def requires_reason(self) -> bool:
        """Для этих статусов результат обязан содержать причину."""
        return self in {CheckStatus.WARNING, CheckStatus.SKIPPED, CheckStatus.BLOCKED}
