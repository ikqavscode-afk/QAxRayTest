"""Прокси-сторож для браузера.

Браузер запускается с этим прокси и ходит в сеть только через него. Для каждого соединения
сторож разрешает имя, проверяет ВСЕ ответы DNS политикой адресов и соединяется с проверенным
IP-адресом (не по имени). Так страница чужого сайта не может заставить браузер обратиться к
внутренней сети ни редиректом, ни картинкой, ни скриптом, а подмена DNS между проверкой и
соединением невозможна.

Разрешено: HTTPS-туннель (CONNECT) и обычный HTTP-запрос GET/HEAD на порты 80 и 443.
Остальные методы и порты отклоняются. Известное ограничение: внутри HTTPS-туннеля сторож
видит только адрес, но не метод запроса."""

import logging
import select
import socket
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from app.core.security.policy import TargetPolicy, TargetRejectedError

logger = logging.getLogger(__name__)

Connector = Callable[[tuple[str, int], float], socket.socket]

MAX_HEAD_BYTES = 16 * 1024
MAX_BLOCKED_RECORDS = 100
CHUNK = 64 * 1024
HOP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "proxy-connection",
        "proxy-authorization",
        "keep-alive",
        "upgrade",
        "te",
        "trailer",
        "transfer-encoding",
        "content-length",
    }
)
REASONS = {
    400: "Bad Request",
    403: "Forbidden",
    405: "Method Not Allowed",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _default_connect(address: tuple[str, int], timeout: float) -> socket.socket:
    return socket.create_connection(address, timeout=timeout)


class _Reject(Exception):
    """Запрос отклонён: код ответа и признак «записать в список заблокированных»."""

    def __init__(self, code: int, blocked: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.blocked = blocked


class GuardProxy:
    def __init__(
        self,
        policy: TargetPolicy,
        *,
        connect: Connector = _default_connect,
        allowed_ports: tuple[int, ...] = (80, 443),
        max_connections: int = 64,
        io_timeout: float = 15.0,
        byte_limit: int = 100_000_000,
    ) -> None:
        self._policy = policy
        self._connect = connect
        self._allowed_ports = allowed_ports
        self._io_timeout = io_timeout
        self._byte_limit = byte_limit
        self._slots = threading.BoundedSemaphore(max_connections)
        self._lock = threading.Lock()
        self._bytes = 0
        self._active: set[socket.socket] = set()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = False
        self.blocked: list[str] = []  # «хост:порт» без пути и параметров запроса

    # Жизненный цикл

    def start(self) -> int:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))  # только петля: снаружи прокси недоступен
        server.listen(64)
        server.settimeout(0.05)
        self._server = server
        self._thread = threading.Thread(target=self._accept_loop, name="guard-proxy", daemon=True)
        self._thread.start()
        return self.port

    @property
    def address(self) -> tuple[str, int]:
        assert self._server is not None, "прокси не запущен"
        host, port = self._server.getsockname()[:2]
        return str(host), int(port)

    @property
    def port(self) -> int:
        return self.address[1]

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._server is not None:
            self._server.close()
        with self._lock:
            active = list(self._active)
        for sock in active:
            _close(sock)
        if self._thread is not None:
            self._thread.join(timeout=2)

    # Приём соединений

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stopped:
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                self._refuse(client, 503)
                continue
            with self._lock:
                self._active.add(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(self._io_timeout)
            head = self._read_head(client)
            method, target, headers = self._parse(head)
            if method == "CONNECT":
                host, port = self._split_authority(target)
                upstream = self._open(host, port)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            elif method in ("GET", "HEAD"):
                host, port, request = self._rebuild(method, target, headers)
                upstream = self._open(host, port)
                upstream.sendall(request)
            else:
                raise _Reject(405)
            self._pipe(client, upstream)
        except _Reject as rejected:
            if rejected.blocked:
                self._record_blocked(rejected.blocked)
            self._refuse(client, rejected.code)
            return
        except (OSError, ValueError):
            pass  # обрыв или сбой соединения: просто закрываем
        finally:
            if upstream is not None:
                _close(upstream)
            self._slots.release()
            with self._lock:
                self._active.discard(client)
        _close(client)

    # Разбор запроса

    @staticmethod
    def _read_head(client: socket.socket) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            if len(data) > MAX_HEAD_BYTES:
                raise _Reject(400)
            chunk = client.recv(4096)
            if not chunk:
                raise _Reject(400)
            data += chunk
        return data.split(b"\r\n\r\n", 1)[0]

    @staticmethod
    def _parse(head: bytes) -> tuple[str, str, list[tuple[str, str]]]:
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            raise _Reject(400)
        headers = []
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if not separator:
                raise _Reject(400)
            headers.append((name.strip(), value.strip()))
        return parts[0].upper(), parts[1], headers

    @staticmethod
    def _split_authority(authority: str) -> tuple[str, int]:
        host, separator, port_text = authority.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            raise _Reject(400)
        port = int(port_text)
        if not 0 < port < 65536:
            raise _Reject(400)
        return host.strip("[]").lower(), port

    def _rebuild(
        self, method: str, target: str, headers: list[tuple[str, str]]
    ) -> tuple[str, int, bytes]:
        if not target.lower().startswith("http://"):
            raise _Reject(400)  # относительный адрес или другая схема
        try:
            parts = urlsplit(target)
            host = (parts.hostname or "").lower()
            port = parts.port or 80
        except ValueError as error:
            raise _Reject(400) from error
        if not host:
            raise _Reject(400)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        lines = [f"{method} {path} HTTP/1.1", f"Host: {parts.netloc}", "Connection: close"]
        lines += [f"{name}: {value}" for name, value in headers if name.lower() not in HOP_HEADERS]
        return host, port, ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    # Соединение с внешним миром

    def _open(self, host: str, port: int) -> socket.socket:
        where = f"{host}:{port}"
        if port not in self._allowed_ports:
            raise _Reject(403, where)
        try:
            addresses = self._policy.public_addresses(host)
        except TargetRejectedError as error:
            raise _Reject(403, where) from error
        for address in addresses:  # соединение только с проверенным IP, без повторного DNS
            try:
                return self._connect((address, port), self._io_timeout)
            except OSError:
                continue
        raise _Reject(502)

    def _pipe(self, client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        for sock in sockets:
            sock.settimeout(self._io_timeout)
        while True:
            readable, _, _ = select.select(sockets, [], [], self._io_timeout)
            if not readable:
                return  # простой дольше таймаута
            for source in readable:
                data = source.recv(CHUNK)
                if not data or not self._count(len(data)):
                    return
                (upstream if source is client else client).sendall(data)

    def _count(self, size: int) -> bool:
        with self._lock:
            self._bytes += size
            return self._bytes <= self._byte_limit

    def _record_blocked(self, where: str) -> None:
        logger.info("Сторож отклонил соединение с %s", where)
        with self._lock:
            if len(self.blocked) < MAX_BLOCKED_RECORDS and where not in self.blocked:
                self.blocked.append(where)

    # Ответы об отказе

    def _refuse(self, client: socket.socket, code: int) -> None:
        """Отвечает кодом и аккуратно закрывает: неотправленные данные клиента не должны
        сбросить соединение раньше, чем он прочитает ответ."""
        reason = REASONS.get(code, "Error")
        body = f"HTTP/1.1 {code} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
        try:
            client.settimeout(1.0)
            client.sendall(body.encode("ascii"))
            client.shutdown(socket.SHUT_WR)
            drained = 0
            while drained < 4 * CHUNK:
                chunk = client.recv(CHUNK)
                if not chunk:
                    break
                drained += len(chunk)
        except OSError:
            pass
        finally:
            _close(client)
            with self._lock:
                self._active.discard(client)


def _close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass
