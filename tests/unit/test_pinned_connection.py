"""Защита от DNS rebinding: соединение идёт на IP, проверенный политикой, а имя второй раз не разрешается."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from app.adapters.http.client import NetworkError, SafeHttpClient
from app.adapters.http.pinned import pinned_session
from app.bootstrap import build_http_client
from app.config import Settings
from app.core.security.policy import TargetPolicy, TargetRejectedError

PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"
PRIVATE = "10.0.0.5"


class Resolver:
    """Ответы DNS по очереди: последний повторяется. Считает обращения."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, host):
        self.calls += 1
        index = min(self.calls, len(self.answers)) - 1
        return self.answers[index]


class Dialer:
    """Заменяет создание сокета: записывает адреса, на которые пытались соединиться."""

    def __init__(self, failing=()):
        self.addresses = []
        self.failing = set(failing)

    def __call__(self, address, *args, **kwargs):
        self.addresses.append(address)
        if address[0] in self.failing:
            raise OSError("нет маршрута")
        return object()


@pytest.fixture
def dialer(monkeypatch):
    fake = Dialer()
    monkeypatch.setattr("urllib3.util.connection.create_connection", fake)
    return fake


def connection(resolver, https=False):
    from app.adapters.http.pinned import pinned_connection_classes

    http_cls, https_cls = pinned_connection_classes(TargetPolicy(resolver=resolver))
    return (https_cls if https else http_cls)("pinned.test", 443 if https else 80)


def test_connection_dials_the_validated_ip_not_the_name(dialer):
    resolver = Resolver([PUBLIC])
    connection(resolver)._new_conn()
    assert dialer.addresses == [(PUBLIC, 80)]
    assert resolver.calls == 1  # имя разрешено один раз, результат проверен и использован


def test_https_connection_keeps_the_hostname_for_sni_and_certificate(dialer):
    conn = connection(Resolver([PUBLIC]), https=True)
    assert conn.host == "pinned.test"  # по нему проверяются сертификат и SNI
    conn._new_conn()
    assert dialer.addresses == [(PUBLIC, 443)]


def test_next_address_is_tried_when_the_first_is_unreachable(monkeypatch):
    fake = Dialer(failing={PUBLIC})
    monkeypatch.setattr("urllib3.util.connection.create_connection", fake)
    connection(Resolver([PUBLIC, PUBLIC_2]))._new_conn()
    assert [a[0] for a in fake.addresses] == [PUBLIC, PUBLIC_2]


def test_all_addresses_failing_gives_a_connection_error(monkeypatch):
    from urllib3.exceptions import NewConnectionError

    monkeypatch.setattr("urllib3.util.connection.create_connection", Dialer(failing={PUBLIC}))
    with pytest.raises(NewConnectionError):
        connection(Resolver([PUBLIC]))._new_conn()


def test_private_answer_at_connect_time_is_refused_without_dialing(dialer):
    with pytest.raises(TargetRejectedError):
        connection(Resolver([PRIVATE]))._new_conn()
    assert dialer.addresses == []


def test_mixed_public_and_private_answer_is_refused(dialer):
    with pytest.raises(TargetRejectedError):
        connection(Resolver([PUBLIC, PRIVATE]))._new_conn()
    assert dialer.addresses == []


def test_rebinding_between_validation_and_connection_is_blocked(dialer):
    """Сначала DNS отвечает публичным адресом (проверка прошла), при соединении уже внутренним."""
    resolver = Resolver([PUBLIC], [PRIVATE])
    client = SafeHttpClient(
        TargetPolicy(resolver=resolver),
        session=pinned_session(TargetPolicy(resolver=resolver)),
        max_retries=0,
    )
    with pytest.raises(TargetRejectedError):
        client.get("http://rebind.test/")
    assert dialer.addresses == []


def test_second_resolution_is_not_trusted_even_if_it_is_public_again(dialer):
    """Каждое соединение проверяет именно тот ответ, по которому соединяется."""
    resolver = Resolver([PUBLIC], [PRIVATE], [PUBLIC])
    policy = TargetPolicy(resolver=resolver)
    client = SafeHttpClient(policy, session=pinned_session(policy), max_retries=0)
    with pytest.raises(TargetRejectedError):
        client.get("http://rebind.test/")


def test_connect_timeout_becomes_a_network_error(monkeypatch):
    def slow(address, *args, **kwargs):
        raise TimeoutError("долго")

    monkeypatch.setattr("urllib3.util.connection.create_connection", slow)
    policy = TargetPolicy(resolver=Resolver([PUBLIC]))
    client = SafeHttpClient(policy, session=pinned_session(policy), max_retries=0)
    with pytest.raises(NetworkError):
        client.get("http://slow.test/")


def test_pinned_session_does_not_use_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    session = pinned_session(TargetPolicy(resolver=Resolver([PUBLIC])))
    assert session.trust_env is False
    assert session.proxies == {}


def test_production_client_uses_the_pinned_adapters():
    client = build_http_client(Settings(), TargetPolicy(resolver=Resolver([PUBLIC])))
    session = client._session
    for scheme in ("http://", "https://"):
        assert type(session.get_adapter(scheme + "x.test")).__name__ == "PinnedAdapter"


# Настоящее соединение с локальным сервером: имени нет в DNS, соединение идёт по проверенному IP


class _Handler(BaseHTTPRequestHandler):
    seen_hosts: ClassVar[list[str]] = []

    def do_GET(self):
        type(self).seen_hosts.append(self.headers.get("Host", ""))
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    _Handler.seen_hosts = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_real_request_reaches_the_pinned_ip_with_the_original_host_header(
    monkeypatch, local_server
):
    monkeypatch.setattr(
        "app.core.security.policy._is_public", lambda address: address == "127.0.0.1"
    )
    resolver = Resolver(["127.0.0.1"])
    policy = TargetPolicy(resolver=resolver)
    client = SafeHttpClient(policy, session=pinned_session(policy), max_retries=0, timeout=3)
    response = client.get(f"http://pinned.test:{local_server}/path")
    assert response.status_code == 200 and response.body == b"ok"
    assert _Handler.seen_hosts == [f"pinned.test:{local_server}"]
    # ни requests, ни urllib3 не разрешали имя сами: иначе pinned.test не нашлось бы
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("pinned.test", None)


def test_real_request_is_refused_when_the_server_address_turns_private(monkeypatch, local_server):
    monkeypatch.setattr(
        "app.core.security.policy._is_public", lambda address: address == "127.0.0.1"
    )
    resolver = Resolver(["127.0.0.1"], [PRIVATE])
    policy = TargetPolicy(resolver=resolver)
    client = SafeHttpClient(policy, session=pinned_session(policy), max_retries=0, timeout=3)
    with pytest.raises(TargetRejectedError):
        client.get(f"http://pinned.test:{local_server}/path")
    assert _Handler.seen_hosts == []
