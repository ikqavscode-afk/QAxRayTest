"""Соединение с проверенным IP-адресом (защита от DNS rebinding, ADR-0003).

Обычная схема «проверить адрес, потом запросить» уязвима: DNS может ответить иначе между проверкой и
соединением. Здесь имя разрешается один раз в момент соединения, все полученные адреса проверяются политикой, и
соединение устанавливается именно с ними. Имя хоста остаётся в запросе: заголовок Host, SNI и проверка сертификата
работают как обычно."""

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util import connection as urllib3_connection

from app.core.security.policy import TargetPolicy


def _dial(conn: HTTPConnection, policy: TargetPolicy) -> Any:
    addresses = policy.public_addresses(conn._dns_host)  # TargetRejectedError: соединения не будет
    last_error: OSError | None = None
    for address in addresses:
        try:
            return urllib3_connection.create_connection(
                (address, conn.port),
                conn.timeout,
                source_address=conn.source_address,
                socket_options=conn.socket_options,
            )
        except TimeoutError as error:
            raise ConnectTimeoutError(
                conn, f"Connection to {conn.host} timed out. (connect timeout={conn.timeout})"
            ) from error
        except OSError as error:
            last_error = error  # пробуем следующий проверенный адрес
    raise NewConnectionError(conn, f"Failed to establish a new connection: {last_error}")


def pinned_connection_classes(
    policy: TargetPolicy,
) -> tuple[type[HTTPConnection], type[HTTPSConnection]]:
    """Классы соединений, которые подключаются только к проверенным адресам."""

    class PinnedHTTPConnection(HTTPConnection):
        def _new_conn(self) -> Any:
            return _dial(self, policy)

    class PinnedHTTPSConnection(HTTPSConnection):
        def _new_conn(self) -> Any:
            return _dial(self, policy)

    return PinnedHTTPConnection, PinnedHTTPSConnection


class PinnedAdapter(HTTPAdapter):
    """Адаптер requests, подключающий пулы с проверенными соединениями."""

    def __init__(self, policy: TargetPolicy) -> None:
        http_connection, https_connection = pinned_connection_classes(policy)

        class PinnedHTTPPool(HTTPConnectionPool):
            ConnectionCls = http_connection

        class PinnedHTTPSPool(HTTPSConnectionPool):
            ConnectionCls = https_connection

        self._pool_classes = {"http": PinnedHTTPPool, "https": PinnedHTTPSPool}
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        # свой словарь у менеджера, общий словарь библиотеки не меняется
        self.poolmanager.pool_classes_by_scheme = dict(self._pool_classes)


def pinned_session(policy: TargetPolicy) -> requests.Session:
    """Сессия, которая соединяется только с проверенными адресами и не берёт прокси из окружения."""
    session = requests.Session()
    session.trust_env = False  # прокси из окружения обошёл бы проверку адреса назначения
    adapter = PinnedAdapter(policy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
