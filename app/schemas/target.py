from pydantic import BaseModel, ConfigDict, field_validator

MAX_API_PATHS = 10
MAX_PATH_LENGTH = 200


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


class RunTarget(BaseModel):
    """Что проверять: сайт обязателен, всё про API необязательно.

    Модель только приводит ввод к чистому виду. Проверка на запрещённые адреса
    (внутренние сети) выполняется отдельно в TargetPolicy."""

    model_config = ConfigDict(frozen=True)

    site_url: str
    api_docs_url: str | None = None
    api_base_url: str | None = None
    api_paths: tuple[str, ...] = ()

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
