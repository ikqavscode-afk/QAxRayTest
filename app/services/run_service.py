from collections.abc import Sequence

from app.core.browser import BrowserFactory
from app.core.browser_session import BrowserSession
from app.core.checks.base import Check
from app.core.checks.context import CheckContext
from app.core.execution.runner import Runner
from app.core.ports import HttpClient
from app.core.security.policy import TargetPolicy, TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.target import RunTarget


class RunService:
    """Сценарий «проверить цель»: политика безопасности, контекст, исполнитель, отчёт."""

    def __init__(
        self,
        http: HttpClient,
        policy: TargetPolicy,
        checks: Sequence[Check],
        browser_factory: BrowserFactory | None = None,
    ) -> None:
        self._http = http
        self._browser_factory = browser_factory
        self._policy = policy
        self._checks = list(checks)

    def run(self, target: RunTarget | str) -> RunReport:
        if isinstance(target, str):
            target = RunTarget(site_url=target)  # прежний вызов с одним адресом
        # Все адреса проверяются до первого запроса: запрещённый не доходит до сети.
        site_url = self._policy.validate(target.site_url)
        api_docs_url = self._validated(target.api_docs_url, "api_docs_url")
        api_base_url = self._validated(target.api_base_url, "api_base_url")
        session = BrowserSession(self._browser_factory, site_url) if self._browser_factory else None
        context = CheckContext(
            http=self._http,
            site_url=site_url,
            api_docs_url=api_docs_url,
            api_base_url=api_base_url,
            api_paths=target.api_paths,
            api_expected_statuses=target.api_expected_statuses,
            browser=session,
        )
        try:
            return Runner(self._checks).run(context)
        finally:
            if session:
                session.close()  # браузер закрывается всегда, даже при прерывании запуска

    def _validated(self, url: str | None, field: str) -> str | None:
        if url is None:
            return None
        try:
            return self._policy.validate(url)
        except TargetRejectedError as error:
            raise TargetRejectedError(str(error), field=field) from error
