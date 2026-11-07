import time
from dataclasses import replace
from urllib.parse import urljoin

import requests

from app.core.ports import HttpResponse, NetworkError, TooManyRedirectsError
from app.core.security.policy import TargetPolicy

REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
USER_AGENT = "QAxRayTest/0.1"


class SafeHttpClient:
    """GET-запросы с проверкой каждого адреса, лимитами и повтором только при сетевых сбоях."""

    def __init__(
        self,
        policy: TargetPolicy,
        *,
        timeout: float = 10.0,
        max_bytes: int = 1_000_000,
        max_redirects: int = 5,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        session: requests.Session | None = None,
    ) -> None:
        self._policy = policy
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def get(self, url: str) -> HttpResponse:
        started = time.monotonic()
        current = self._policy.validate(url)
        visited: list[str] = []
        for _ in range(self._max_redirects + 1):
            response = self._get_with_retries(current)
            location = response.headers.get("location")
            if response.status_code not in REDIRECT_CODES or not location:
                elapsed = int((time.monotonic() - started) * 1000)
                return replace(response, redirects=visited, elapsed_ms=elapsed)
            visited.append(current)
            current = self._policy.validate(urljoin(current, location))
        raise TooManyRedirectsError(f"Больше {self._max_redirects} перенаправлений")

    def _get_with_retries(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(self._retry_delay)
            try:
                return self._request_once(url)
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
        raise NetworkError(
            f"Нет ответа от {url} после {self._max_retries + 1} попыток"
        ) from last_error

    def _request_once(self, url: str) -> HttpResponse:
        with self._session.get(
            url, timeout=self._timeout, allow_redirects=False, stream=True
        ) as response:
            chunks: list[bytes] = []
            size = 0
            truncated = False
            for chunk in response.iter_content(chunk_size=8192):
                remaining = self._max_bytes - size
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    truncated = True
                    break
                chunks.append(chunk)
                size += len(chunk)
            headers = {name.lower(): value for name, value in response.headers.items()}
            return HttpResponse(
                url=url,
                status_code=response.status_code,
                headers=headers,
                body=b"".join(chunks),
                truncated=truncated,
            )
