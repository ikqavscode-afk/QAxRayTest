"""Настройки из переменных окружения с жёсткими границами.

Границы защищают чужие сайты от нагрузки и приложение от опасных значений. Настройки,
отключающей проверку адресов (защиту от запросов во внутренние сети), намеренно нет."""

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# (поле, переменная окружения, минимум, максимум, целое ли число)
_RULES = (
    ("http_timeout", "QAXRAY_HTTP_TIMEOUT", 1.0, 30.0, False),
    ("max_bytes", "QAXRAY_MAX_BYTES", 10_000, 5_000_000, True),
    ("max_redirects", "QAXRAY_MAX_REDIRECTS", 0, 10, True),
    ("max_retries", "QAXRAY_MAX_RETRIES", 0, 3, True),
    ("retry_delay", "QAXRAY_RETRY_DELAY", 0.0, 5.0, False),
    ("browser_page_timeout", "QAXRAY_BROWSER_PAGE_TIMEOUT", 5.0, 60.0, False),
    ("max_parallel_runs", "QAXRAY_MAX_PARALLEL_RUNS", 1, 4, True),
    ("rate_limit_runs", "QAXRAY_RATE_LIMIT_RUNS", 1, 30, True),
    ("rate_limit_window", "QAXRAY_RATE_LIMIT_WINDOW", 10.0, 600.0, False),
    ("trusted_proxies", "QAXRAY_TRUSTED_PROXIES", 0, 3, True),
    ("max_run_seconds", "QAXRAY_MAX_RUN_SECONDS", 30.0, 900.0, False),
)
ALLOWED_HOSTS_VARIABLE = "QAXRAY_ALLOWED_HOSTS"
MAX_ALLOWED_HOSTS = 10
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]")
# Имя хоста без порта и без подстановочных знаков: домен, IPv4 или IPv6 в квадратных скобках
_HOST_NAME = re.compile(r"^(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?|\[[0-9a-f:]{2,45}\])$")
# Переключатели «0 или 1»: (поле, переменная окружения)
_SWITCHES = (
    ("browser_headless", "QAXRAY_BROWSER_HEADLESS"),
    ("browser_no_sandbox", "QAXRAY_BROWSER_NO_SANDBOX"),
    ("cookie_secure", "QAXRAY_COOKIE_SECURE"),
)


class SettingsError(ValueError):
    """Неверное значение настройки; в сообщении названа переменная окружения."""


class Settings(BaseModel):
    """Параметры HTTP-клиента, браузера и ограничений запуска. Значения по умолчанию совпадают с прежними зашитыми в код."""

    model_config = ConfigDict(frozen=True)

    http_timeout: float = Field(10.0, ge=1.0, le=30.0, allow_inf_nan=False)
    max_bytes: int = Field(1_000_000, ge=10_000, le=5_000_000)
    max_redirects: int = Field(5, ge=0, le=10)
    max_retries: int = Field(2, ge=0, le=3)
    retry_delay: float = Field(0.5, ge=0.0, le=5.0, allow_inf_nan=False)
    browser_page_timeout: float = Field(20.0, ge=5.0, le=60.0, allow_inf_nan=False)
    max_parallel_runs: int = Field(
        2, ge=1, le=4
    )  # одновременно идущих запусков (у каждого свой браузер)
    rate_limit_runs: int = Field(10, ge=1, le=30)  # запусков с одного адреса за окно
    rate_limit_window: float = Field(60.0, ge=10.0, le=600.0, allow_inf_nan=False)  # окно, секунды
    browser_headless: bool = True
    browser_no_sandbox: bool = False  # только для одноразовых машин CI
    # 1 при работе по HTTPS: cookie сессии уходит только по защищённому соединению
    cookie_secure: bool = False
    # сколько обратных прокси доверять в X-Forwarded-For (0: заголовок игнорируется)
    trusted_proxies: int = Field(0, ge=0, le=3)
    # предел времени одного запуска, секунды
    max_run_seconds: float = Field(300.0, ge=30.0, le=900.0, allow_inf_nan=False)
    # допустимые имена в заголовке Host (защита от подмены DNS против самого приложения)
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts_are_plain_names(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        if not hosts or len(hosts) > MAX_ALLOWED_HOSTS:
            raise ValueError("число имён хоста")
        if not all(_HOST_NAME.fullmatch(host) for host in hosts):
            raise ValueError("имя хоста")
        return hosts

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        """Читает QAXRAY_*; пустые значения считаются незаданными. Окружение передаётся
        аргументом, чтобы тесты не зависели от настоящего."""
        values: dict[str, Any] = {}
        errors: list[str] = []
        for field, variable, _, _, _ in _RULES:
            raw = environ.get(variable, "").strip()
            if raw:
                values[field] = raw
        hosts = environ.get(ALLOWED_HOSTS_VARIABLE, "").strip()
        if hosts:
            values["allowed_hosts"] = tuple(
                part.strip().lower() for part in hosts.split(",") if part.strip()
            )
        for field, variable in _SWITCHES:
            switch = environ.get(variable, "").strip()
            if switch in ("0", "1"):
                values[field] = switch == "1"
            elif switch:
                errors.append(f"{variable}: нужно 0 или 1")
        try:
            settings = cls(**values)
        except ValidationError as error:
            errors.append(_describe(error))
        if errors:
            raise SettingsError("; ".join(errors))
        return settings


def _describe(error: ValidationError) -> str:
    rules = {field: (variable, low, high, whole) for field, variable, low, high, whole in _RULES}
    problems: list[str] = []
    for item in error.errors():
        if item["loc"] and item["loc"][0] == "allowed_hosts":
            problems.append(
                f"{ALLOWED_HOSTS_VARIABLE}: нужен список имён хоста через запятую "
                f"(не более {MAX_ALLOWED_HOSTS}, без «*», портов и путей)"
            )
            continue
        variable, low, high, whole = rules[str(item["loc"][0])]
        kind = "целое число" if whole else "число"
        problems.append(f"{variable}: нужно {kind} от {low:g} до {high:g}")
    return "; ".join(dict.fromkeys(problems))
