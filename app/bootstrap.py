from app.adapters.http.client import SafeHttpClient
from app.core.checks.catalog import default_checks
from app.core.security.policy import TargetPolicy
from app.services.run_service import RunService


def build_run_service() -> RunService:
    """Корень композиции: здесь настоящие детали соединяются в рабочий сервис."""
    policy = TargetPolicy()
    return RunService(http=SafeHttpClient(policy), policy=policy, checks=default_checks())
