from pydantic import BaseModel, ConfigDict, Field

from app.schemas.result import CheckResult
from app.schemas.status import CheckStatus


class RunReport(BaseModel):
    """Итог запуска: результаты проверок в порядке выполнения."""

    model_config = ConfigDict(frozen=True)

    results: list[CheckResult] = Field(default_factory=list)

    @property
    def summary(self) -> dict[CheckStatus, int]:
        """Сколько результатов в каждом статусе (нули тоже показываются)."""
        counts = dict.fromkeys(CheckStatus, 0)
        for result in self.results:
            counts[result.status] += 1
        return counts
