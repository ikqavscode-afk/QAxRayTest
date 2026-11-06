from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})


class TargetRejectedError(ValueError):
    """Адрес не прошёл политику безопасности; в сообщении указана причина."""


class TargetPolicy:
    """Решает, можно ли тестировать адрес. В сеть не ходит."""

    def validate(self, url: str) -> str:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            raise TargetRejectedError("Разрешены только адреса http и https.")
        if not parts.hostname:
            raise TargetRejectedError("В адресе не указан хост.")
        return url
