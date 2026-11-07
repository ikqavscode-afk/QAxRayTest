from collections.abc import Sequence

from app.core.checks.base import Check
from app.core.checks.context import CheckContext
from app.core.execution.runner import Runner
from app.core.ports import HttpClient
from app.core.security.policy import TargetPolicy
from app.schemas.report import RunReport


class RunService:
    """Сценарий «проверить сайт»: политика безопасности, контекст, исполнитель, отчёт."""

    def __init__(self, http: HttpClient, policy: TargetPolicy, checks: Sequence[Check]) -> None:
        self._http = http
        self._policy = policy
        self._checks = list(checks)

    def run(self, site_url: str) -> RunReport:
        safe_url = self._policy.validate(site_url)  # запрещённый адрес не доходит до запросов
        context = CheckContext(http=self._http, site_url=safe_url)
        return Runner(self._checks).run(context)
