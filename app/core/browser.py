"""Порт браузера: контракт, от которого зависят проверки. Selenium остаётся за адаптером."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

MIN_WIDTH, MAX_WIDTH = 320, 3840
MIN_HEIGHT, MAX_HEIGHT = 480, 2160


class BrowserError(Exception):
    """Сбой браузера (среда, а не находка о сайте): исполнитель превращает его в BLOCKED."""


class BrowserNetworkError(BrowserError):
    """Сетевой сбой при открытии страницы (обрыв, не разрешилось имя). Единственный повторяемый."""


class BrowserCertificateError(BrowserError):
    """Браузер не принял сертификат сайта (просрочен, самоподписан, чужой). Не повторяется."""


class BrowserTimeoutError(BrowserError):
    """Страница не загрузилась за отведённое время. Не повторяется."""


class BrowserUnavailableError(BrowserError):
    """Браузер или драйвер не запустился, не подключён к запуску либо сессия закрыта."""


class BrowserBlockedError(BrowserError):
    """Навигация или запрос страницы запрещены политикой безопасности. Не повторяется."""


class BrowserBudgetError(BrowserError):
    """Исчерпан лимит запуска: число открытий страницы или общее время браузера."""


@dataclass(frozen=True)
class Viewport:
    """Размер окна браузера в CSS-пикселях."""

    width: int
    height: int

    def __post_init__(self) -> None:
        if not (MIN_WIDTH <= self.width <= MAX_WIDTH and MIN_HEIGHT <= self.height <= MAX_HEIGHT):
            raise ValueError(
                f"Недопустимый размер окна {self.width}x{self.height}: "
                f"ширина {MIN_WIDTH}-{MAX_WIDTH}, высота {MIN_HEIGHT}-{MAX_HEIGHT}"
            )


DESKTOP = Viewport(1280, 800)
MOBILE = Viewport(375, 812)


@dataclass(frozen=True)
class PageInfo:
    """Итог открытия страницы: адрес после перенаправлений, заголовок, время загрузки."""

    url: str
    title: str
    load_ms: int


@dataclass(frozen=True)
class ImageInfo:
    """Факты об одном видимом изображении. Адрес без параметров запроса (в них бывают токены)."""

    src: str
    complete: bool
    natural_width: int
    lazy: bool


@dataclass(frozen=True)
class ImagesSnapshot:
    """Видимые изображения: сколько всего и первые из них (не более заданного лимита)."""

    total: int
    items: tuple[ImageInfo, ...]


@dataclass(frozen=True)
class OverflowInfo:
    """Ширина окна и ширина содержимого; offenders: что выходит за правый край (теги, id, класс)."""

    viewport_width: int
    scroll_width: int
    offenders: tuple[str, ...]


class BrowserPort(Protocol):
    """Один браузер, одна страница. Методы-пробы для проверок добавляются по мере появления проверок.

    Реализация обязана: открывать только адреса, прошедшие политику безопасности, ждать явными
    ожиданиями (без sleep), не скачивать файлы и не обращаться к локальным файлам."""

    def open(self, url: str, viewport: Viewport) -> PageInfo: ...

    def images(self, limit: int) -> ImagesSnapshot: ...

    def overflow(self) -> OverflowInfo: ...

    def close(self) -> None: ...


BrowserFactory = Callable[[], BrowserPort]
