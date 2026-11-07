import os

import requests

from app.adapters.browser.selenium_browser import selenium_browser_factory
from app.adapters.http.client import SafeHttpClient
from app.config import Settings
from app.core.checks.catalog import default_checks
from app.core.security.policy import TargetPolicy
from app.services.run_service import RunService


def build_http_client(
    settings: Settings, policy: TargetPolicy, session: requests.Session | None = None
) -> SafeHttpClient:
    """HTTP-клиент с настройками из конфигурации (сессию можно подставить в тестах)."""
    return SafeHttpClient(
        policy,
        timeout=settings.http_timeout,
        max_bytes=settings.max_bytes,
        max_redirects=settings.max_redirects,
        max_retries=settings.max_retries,
        retry_delay=settings.retry_delay,
        session=session,
    )


def build_run_service(settings: Settings | None = None) -> RunService:
    """Корень композиции: здесь настоящие детали соединяются в рабочий сервис.

    Без аргумента настройки читаются из переменных окружения QAXRAY_*."""
    settings = settings or Settings.from_env(os.environ)
    policy = TargetPolicy()
    browser_factory = selenium_browser_factory(
        policy, page_timeout=settings.browser_page_timeout, headless=settings.browser_headless
    )  # браузер запускается лениво, только если нужна браузерная проверка
    return RunService(
        http=build_http_client(settings, policy),
        policy=policy,
        checks=default_checks(),
        browser_factory=browser_factory,
    )
