"""Настройки из переменных окружения с жёсткими границами.

Границы защищают чужие сайты от нагрузки и приложение от опасных значений. Настройки,
отключающей проверку адресов (защиту от запросов во внутренние сети), намеренно нет."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# (поле, переменная окружения, минимум, максимум, целое ли число)
_RULES = (
    ("http_timeout", "QAXRAY_HTTP_TIMEOUT", 1.0, 30.0, False),
    ("max_bytes", "QAXRAY_MAX_BYTES", 10_000, 5_000_000, True),
    ("max_redirects", "QAXRAY_MAX_REDIRECTS", 0, 10, True),
    ("max_retries", "QAXRAY_MAX_RETRIES", 0, 3, True),
    ("retry_delay", "QAXRAY_RETRY_DELAY", 0.0, 5.0, False),
)


class SettingsError(ValueError):
    """Неверное значение настройки; в сообщении названа переменная окружения."""


class Settings(BaseModel):
    """Параметры HTTP-клиента. Значения по умолчанию совпадают с прежними зашитыми в код."""

    model_config = ConfigDict(frozen=True)

    http_timeout: float = Field(10.0, ge=1.0, le=30.0, allow_inf_nan=False)
    max_bytes: int = Field(1_000_000, ge=10_000, le=5_000_000)
    max_redirects: int = Field(5, ge=0, le=10)
    max_retries: int = Field(2, ge=0, le=3)
    retry_delay: float = Field(0.5, ge=0.0, le=5.0, allow_inf_nan=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        """Читает QAXRAY_*; пустые значения считаются незаданными. Окружение передаётся
        аргументом, чтобы тесты не зависели от настоящего."""
        values: dict[str, str] = {}
        for field, variable, _, _, _ in _RULES:
            raw = environ.get(variable, "").strip()
            if raw:
                values[field] = raw
        try:
            return cls(**values)  # type: ignore[arg-type]
        except ValidationError as error:
            raise SettingsError(_describe(error)) from error


def _describe(error: ValidationError) -> str:
    rules = {field: (variable, low, high, whole) for field, variable, low, high, whole in _RULES}
    problems: list[str] = []
    for item in error.errors():
        variable, low, high, whole = rules[str(item["loc"][0])]
        kind = "целое число" if whole else "число"
        problems.append(f"{variable}: нужно {kind} от {low:g} до {high:g}")
    return "; ".join(dict.fromkeys(problems))
