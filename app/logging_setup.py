"""Журналы приложения: любое сообщение и след ошибки проходят маскирование секретов."""

import logging
from typing import IO

from app.core.security.redactor import Redactor

_MARK = "_qaxray_redacting"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RedactingFormatter(logging.Formatter):
    """Маскирует уже собранную строку, включая трассировку ошибки."""

    def __init__(
        self, fmt: str | None = None, datefmt: str | None = None, redactor: Redactor | None = None
    ) -> None:
        super().__init__(fmt, datefmt)
        self._redactor = redactor or Redactor()

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.redact_text(super().format(record))


def configure_logging(*, level: int = logging.INFO, stream: IO[str] | None = None) -> None:
    """Подключает к корневому журналу обработчик с маскированием. Повторный вызов ничего не меняет."""
    root = logging.getLogger()
    if any(getattr(handler, _MARK, False) for handler in root.handlers):
        return
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(FORMAT))
    setattr(handler, _MARK, True)
    root.addHandler(handler)
    root.setLevel(level)
