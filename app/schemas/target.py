from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator

MAX_API_PATHS = 10
MAX_PATH_LENGTH = 200
MIN_STATUS = 100
MAX_STATUS = 599
MAX_STATUSES_PER_PATH = 5


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _check_path(path: str) -> str:
    """Путь API должен остаться путём: он не может сменить хост или выйти вверх по каталогам."""
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError(f"Путь должен начинаться с одного «/»: {path[:40]!r}")
    if len(path) > MAX_PATH_LENGTH:
        raise ValueError(f"Путь длиннее {MAX_PATH_LENGTH} символов")
    if "\\" in path or "://" in path:
        raise ValueError(f"Недопустимые символы в пути: {path[:40]!r}")
    if any(char.isspace() or ord(char) < 32 for char in path):
        raise ValueError(f"В пути не должно быть пробелов и управляющих символов: {path[:40]!r}")
    if ".." in path.split("?", 1)[0].split("/"):
        raise ValueError(f"В пути не должно быть «..»: {path[:40]!r}")
    return path


def _check_statuses(statuses: tuple[int, ...]) -> tuple[int, ...]:
    if not statuses:
        raise ValueError("Список ожидаемых статусов пуст")
    if len(statuses) > MAX_STATUSES_PER_PATH:
        raise ValueError(f"Не более {MAX_STATUSES_PER_PATH} ожидаемых статусов на путь")
    if any(not MIN_STATUS <= code <= MAX_STATUS for code in statuses):
        raise ValueError(f"HTTP-статус должен быть от {MIN_STATUS} до {MAX_STATUS}")
    return statuses


def parse_statuses(text: str) -> tuple[int, ...]:
    """«201» или «200, 404» в кортеж статусов; ошибка ввода даёт ValueError."""
    parts = [part.strip() for part in text.split(",")]
    if not text.strip() or not all(part.isdigit() for part in parts):
        raise ValueError("Укажите HTTP-статус числом или список через запятую: 201 или 200,404")
    return _check_statuses(tuple(int(part) for part in parts))


def parse_path_line(line: str) -> tuple[str, tuple[int, ...] | None]:
    """Строка формы «/ping» или «/ping | 201»: путь и необязательные ожидаемые статусы."""
    text = line.strip()
    if "|" not in text:
        return text, None
    path, _, tail = text.rpartition("|")
    return path.strip(), parse_statuses(tail)


def parse_expectation(text: str) -> tuple[str, tuple[int, ...]]:
    """Значение флага консоли «/ping=201»: разделитель «=» берётся последний."""
    path, separator, tail = text.strip().rpartition("=")
    if not separator or not path.strip():
        raise ValueError("Ожидается ПУТЬ=СТАТУС, например /ping=201")
    return path.strip(), parse_statuses(tail)


class RunTarget(BaseModel):
    """Что проверять: сайт обязателен, всё про API необязательно.

    Модель только приводит ввод к чистому виду. Проверка на запрещённые адреса
    (внутренние сети) выполняется отдельно в TargetPolicy."""

    model_config = ConfigDict(frozen=True)

    site_url: str
    api_docs_url: str | None = None
    api_base_url: str | None = None
    api_paths: tuple[str, ...] = ()
    api_expected_statuses: dict[str, tuple[int, ...]] = {}

    @field_validator("site_url")
    @classmethod
    def _site_url_is_not_blank(cls, value: str) -> str:
        cleaned = _blank_to_none(value)
        if cleaned is None:
            raise ValueError("Адрес сайта не указан")
        return cleaned

    @field_validator("api_docs_url", "api_base_url")
    @classmethod
    def _optional_url(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("api_paths")
    @classmethod
    def _paths_are_safe(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        unique = tuple(dict.fromkeys(_check_path(path) for path in paths))
        if len(unique) > MAX_API_PATHS:
            raise ValueError(f"Пути API: не более {MAX_API_PATHS}")
        return unique

    @field_validator("api_expected_statuses")
    @classmethod
    def _expected_statuses_are_valid(
        cls, value: dict[str, tuple[int, ...]], info: ValidationInfo
    ) -> dict[str, tuple[int, ...]]:
        known = info.data.get("api_paths", ())
        checked: dict[str, tuple[int, ...]] = {}
        for path, statuses in value.items():
            if path not in known:
                raise ValueError(f"Ожидаемый статус задан для пути вне списка: {path[:40]!r}")
            checked[path] = _check_statuses(tuple(statuses))
        return checked


_FIELDS = ("site_url", "api_docs_url", "api_base_url", "api_paths", "api_expected_statuses")


def describe_errors(error: ValidationError) -> dict[str, str]:
    """Ошибки ввода по полям: читаемый текст без технических префиксов pydantic."""
    messages: dict[str, str] = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "site_url"
        if field not in _FIELDS:
            field = "site_url"
        text = str(item["msg"]).removeprefix("Value error, ")
        messages.setdefault(field, text)
    return messages
