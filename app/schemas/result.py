from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.status import CheckStatus


class Severity(str, Enum):
    """Важность проверки (H, M, L из чек-листа)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CheckResult(BaseModel):
    """Нормализованный результат одной проверки. После создания не меняется."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(pattern=r"^QC-\d{3}$")
    title: str = Field(min_length=1)
    status: CheckStatus
    severity: Severity
    expected: str
    actual: str
    reason: str | None = None
    evidence: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _reason_is_required_for_some_statuses(self) -> Self:
        if self.status.requires_reason and not (self.reason and self.reason.strip()):
            raise ValueError(f"Для статуса {self.status.value} нужна причина")
        return self
