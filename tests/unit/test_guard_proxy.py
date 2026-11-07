"""Прокси-сторож: браузер ходит в сеть только через него, он пропускает лишь публичные адреса."""

import socket
import threading

import pytest

from app.adapters.browser.guard_proxy import GuardProxy
from app.core.security.policy import TargetPolicy

PUBLIC_IP = "93.184.216.34"
RESOLVER = {
    "site.test": [PUBLIC_IP],
    "internal.test": ["127.0.0.1"],
    "metadata.test": ["169.254.169.254"],
    "mixed.test": [PUBLIC_IP, "10.0.0.5"],
}
CANNED = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello"


def resolve(host):
    if host not in RESOLVER:
        raise OSError("нет такого имени")
    return RESOLVER[host]


class FakeUpstream:
    """Подменяет сеть: connector отдаёт сокет, на другом конце которого «внешний сервер»."""

    def __init__(self, mode="echo", error=None):
        self.mode = mode
        self.error = error
        self.calls = []
        self.received = b""
        self.threads = []

    def __call__(self, address, timeout):
        self.calls.append(address)
        if self.error:
            raise self.error
        near, far = socket.socketpair()
        thread = threading.Thread(target=self._serve, args=(far,), daemon=True)
        thread.start()
        self.threads.append(thread)
        return near

    def _serve(self, sock):
        try:
            if self.mode == "http":
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = sock.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                self.received = data
                sock.sendall(CANNED)
                return
            while True:  # echo: возвращает данные заглавными буквами
                chunk = sock.recv(4096)
                if not chunk:
                    return
                sock.sendall(chunk.upper())
        except OSError:
            pass
        finally:
            sock.close()


def make_proxy(upstream=None, **kwargs):
    upstream = upstream or FakeUpstream()
    proxy = GuardProxy(TargetPolicy(resolver=resolve), connect=upstream, **kwargs)
    proxy.start()
    return proxy, upstream


@pytest.fixture
def started():
    proxies = []

    def _start(upstream=None, **kwargs):
        proxy, upstream = make_proxy(upstream, **kwargs)
        proxies.append(proxy)
        return proxy, upstream

    yield _start
    for proxy in proxies:
        proxy.stop()


def connect_to(proxy):
    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    return sock


def read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def ask(proxy, request):
    sock = connect_to(proxy)
    try:
        sock.sendall(request)
        return read_head(sock)
    finally:
        sock.close()


def tunnel(host="site.test", port=443):
    return f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()


# Где слушает


def test_proxy_listens_on_loopback_only_on_a_free_port(started):
    proxy, _ = started()
    assert proxy.address[0] == "127.0.0.1" and proxy.port > 0


# HTTPS-туннель (CONNECT)


def test_tunnel_to_a_public_host_is_opened_to_the_validated_ip_and_relays_data(started):
    proxy, upstream = started()
    sock = connect_to(proxy)
    sock.sendall(tunnel())
    assert read_head(sock).startswith(b"HTTP/1.1 200")
    sock.sendall(b"ping")
    assert sock.recv(10) == b"PING"
    sock.close()
    assert upstream.calls == [(PUBLIC_IP, 443)]  # соединение с проверенным IP, не по имени


@pytest.mark.parametrize("host", ["internal.test", "metadata.test", "mixed.test"])
def test_tunnel_to_internal_or_mixed_hosts_is_forbidden_and_never_connected(started, host):
    proxy, upstream = started()
    assert ask(proxy, tunnel(host)).startswith(b"HTTP/1.1 403")
    assert upstream.calls == []
    assert f"{host}:443" in proxy.blocked


@pytest.mark.parametrize("target", ["169.254.169.254:80", "127.0.0.1:443", "[::1]:443"])
def test_tunnel_to_internal_ip_literals_is_forbidden(started, target):
    proxy, upstream = started()
    host, _, port = target.rpartition(":")
    assert ask(proxy, tunnel(host.strip("[]"), int(port))).startswith(b"HTTP/1.1 403")
    assert upstream.calls == []


@pytest.mark.parametrize("port", [22, 25, 3306, 8080, 6379])
def test_only_ports_80_and_443_are_allowed(started, port):
    proxy, upstream = started()
    assert ask(proxy, tunnel("site.test", port)).startswith(b"HTTP/1.1 403")
    assert upstream.calls == []


def test_unresolvable_host_is_forbidden(started):
    proxy, upstream = started()
    assert ask(proxy, tunnel("nowhere.test")).startswith(b"HTTP/1.1 403")
    assert upstream.calls == []


def test_unreachable_upstream_gives_bad_gateway(started):
    proxy, _ = started(FakeUpstream(error=OSError("отказ соединения")))
    assert ask(proxy, tunnel()).startswith(b"HTTP/1.1 502")


# Обычный HTTP


def test_plain_get_is_forwarded_in_origin_form_with_connection_close(started):
    proxy, upstream = started(FakeUpstream("http"))
    request = (
        b"GET http://site.test/a?x=1 HTTP/1.1\r\nHost: site.test\r\n"
        b"Proxy-Connection: keep-alive\r\nAccept: */*\r\n\r\n"
    )
    sock = connect_to(proxy)
    sock.sendall(request)
    response = b""
    while chunk := sock.recv(4096):
        response += chunk
    sock.close()
    assert response == CANNED
    assert upstream.calls == [(PUBLIC_IP, 80)]
    sent = upstream.received.decode("latin-1")
    assert sent.startswith("GET /a?x=1 HTTP/1.1\r\n")
    assert "Host: site.test" in sent and "Connection: close" in sent
    assert "Proxy-Connection" not in sent


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_only_safe_methods_are_forwarded(started, method):
    proxy, upstream = started()
    request = f"{method} http://site.test/a HTTP/1.1\r\nHost: site.test\r\n\r\n".encode()
    assert ask(proxy, request).startswith(b"HTTP/1.1 405")
    assert upstream.calls == []


def test_get_to_an_internal_host_is_forbidden_and_report_has_no_query_or_path(started):
    proxy, upstream = started()
    request = (
        b"GET http://internal.test/secret?token=SECRET HTTP/1.1\r\nHost: internal.test\r\n\r\n"
    )
    assert ask(proxy, request).startswith(b"HTTP/1.1 403")
    assert upstream.calls == []
    assert proxy.blocked == ["internal.test:80"]


@pytest.mark.parametrize(
    "request_bytes",
    [
        b"GET /relative HTTP/1.1\r\nHost: site.test\r\n\r\n",  # не абсолютный адрес
        b"GARBAGE\r\n\r\n",
        b"GET ftp://site.test/x HTTP/1.1\r\n\r\n",
    ],
)
def test_malformed_requests_get_bad_request(started, request_bytes):
    proxy, upstream = started()
    assert ask(proxy, request_bytes).startswith(b"HTTP/1.1 400")
    assert upstream.calls == []


def test_oversized_request_head_is_rejected(started):
    proxy, upstream = started()
    sock = connect_to(proxy)
    sock.sendall(b"GET http://site.test/ HTTP/1.1\r\n" + b"X: " + b"a" * 40_000)
    assert read_head(sock).startswith(b"HTTP/1.1 400")
    sock.close()
    assert upstream.calls == []


# Лимиты


def test_total_traffic_is_limited(started):
    proxy, _ = started(byte_limit=10)
    sock = connect_to(proxy)
    sock.sendall(tunnel())
    read_head(sock)
    sock.sendall(b"x" * 1000)
    received = b""
    try:
        while chunk := sock.recv(4096):
            received += chunk
    except OSError:
        pass
    sock.close()
    assert len(received) < 1000


def test_number_of_simultaneous_connections_is_limited(started):
    proxy, _ = started(max_connections=1)
    first = connect_to(proxy)
    first.sendall(tunnel())
    assert read_head(first).startswith(b"HTTP/1.1 200")  # первое соединение занято
    assert ask(proxy, tunnel()).startswith(b"HTTP/1.1 503")
    first.close()


def test_stop_closes_the_port_and_is_idempotent(started):
    proxy, _ = started()
    port = proxy.port
    proxy.stop()
    proxy.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
