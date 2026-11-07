import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

Resolver = Callable[[str], list[str]]


class TargetRejectedError(ValueError):
    """Адрес не прошёл политику безопасности; в сообщении указана причина.

    field: какое поле ввода отклонено (форма показывает ошибку рядом с ним)."""

    def __init__(self, message: str, field: str = "site_url") -> None:
        super().__init__(message)
        self.field = field


def system_resolver(host: str) -> list[str]:
    """Настоящий DNS: все IP-адреса, на которые указывает имя."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class TargetPolicy:
    """Решает, можно ли тестировать адрес. Сама в сеть не ходит: DNS передаётся снаружи."""

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self._resolver = resolver

    def validate(self, url: str) -> str:
        try:
            parts = urlsplit(url.strip())
        except ValueError as error:
            raise TargetRejectedError("Адрес записан некорректно.") from error
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            raise TargetRejectedError("Разрешены только адреса http и https.")
        host = parts.hostname
        if not host:
            raise TargetRejectedError("В адресе не указан хост.")
        for address in self._addresses(host):
            if not _is_public(address):
                raise TargetRejectedError(
                    "Адрес указывает на внутреннюю сеть или служебный диапазон."
                )
        return url

    def _addresses(self, host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return [host]  # IP записан прямо в адресе, DNS не нужен
        try:
            addresses = self._resolver(host)
        except OSError as error:
            raise TargetRejectedError("Не удалось определить IP-адрес хоста.") from error
        if not addresses:
            raise TargetRejectedError("Не удалось определить IP-адрес хоста.")
        return addresses
