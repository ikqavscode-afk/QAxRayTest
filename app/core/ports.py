from dataclasses import dataclass, field
from typing import Protocol


class HttpClientError(Exception):
    """Базовая ошибка HTTP-клиента."""


class NetworkError(HttpClientError):
    """Сетевой сбой (тайм-аут, обрыв соединения) после всех повторов."""


class TooManyRedirectsError(HttpClientError):
    """Превышено допустимое число перенаправлений."""


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    truncated: bool
    elapsed_ms: int = 0
    redirects: list[str] = field(default_factory=list)


class HttpClient(Protocol):
    """Контракт, от которого зависят проверки. Реализация может быть любой."""

    def get(self, url: str) -> HttpResponse: ...
