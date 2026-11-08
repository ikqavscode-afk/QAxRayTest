"""Ограничитель частоты: не более N событий за скользящее окно для каждого ключа (адреса клиента).

Память ограничена: ключей хранится не больше max_keys, устаревшие забываются, при переполнении вытесняется
давно не использованный. Заблокированные попытки в счёт не идут, поэтому настойчивые повторы блокировку не
продлевают."""

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after: int = 0  # через сколько секунд повторить (для отказа)


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_keys: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("Лимит должен быть не меньше 1")
        if window_seconds <= 0:
            raise ValueError("Окно должно быть больше 0")
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max(1, max_keys)
        self._clock = clock
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> RateDecision:
        """Учитывает попытку и решает, можно ли её выполнить."""
        with self._lock:
            now = self._clock()
            self._forget_expired(now)
            events = self._events.get(key)
            if events is None:
                while len(self._events) >= self._max_keys:
                    self._events.popitem(last=False)  # давно не использованный ключ
                events = self._events[key] = deque()
            else:
                self._events.move_to_end(key)
            if len(events) >= self._limit:
                wait = self._window - (now - events[0])
                return RateDecision(False, max(1, math.ceil(wait)))
            events.append(now)
            return RateDecision(True)

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._events)

    def _forget_expired(self, now: float) -> None:
        for key in list(self._events):
            events = self._events[key]
            while events and now - events[0] >= self._window:
                events.popleft()
            if not events:
                del self._events[key]
