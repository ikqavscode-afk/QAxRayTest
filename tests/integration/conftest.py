"""Общая сборка для сквозных тестов: настоящие проверки, HTTP-клиент, служба, менеджер с потоком
и веб-приложение. Подменены только края: сеть (responses), разрешение имён и браузер."""

import re
import threading
from dataclasses import dataclass

import pytest
import requests
import responses

from app.adapters.http.client import SafeHttpClient
from app.core.checks.catalog import default_checks
from app.core.security.policy import TargetPolicy
from app.services.run_manager import RunManager
from app.services.run_service import RunService
from app.services.run_store import InMemoryRunStore
from app.web import create_app
from tests.unit.support import FakeBrowser, FakeFactory

SITE = "https://site.example.com"
API = "https://api.example.com"
PUBLIC_IP = "93.184.216.34"
HOSTS = {
    "site.example.com": PUBLIC_IP,
    "api.example.com": PUBLIC_IP,
    "internal.example.com": "10.0.0.5",
}
WAIT_SECONDS = 20

HTML = (
    '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Главная</title>'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="description" content="Описание"></head><body><h1>Заголовок</h1>'
    '<a href="/about">О нас</a></body></html>'
)


def resolve(host):
    return [HOSTS.get(host, PUBLIC_IP)]


def site_reply(request):
    return (200, {"Content-Type": "text/html; charset=utf-8"}, HTML)


class Behaviour:
    """Подмена поведения сети в тесте: before_home вызывается перед ответом на запрос главной страницы."""

    before_home = None


def api_reply(request):
    path = request.url.split("?", 1)[0].removeprefix(API)
    if path == "/broken":
        raise requests.ConnectionError("нет соединения")
    if path in ("", "/", "/health"):
        return (200, {"Content-Type": "application/json"}, '{"ok": true}')
    return (404, {"Content-Type": "application/json"}, '{"error": "not found"}')


@dataclass
class Stack:
    client: object
    manager: RunManager
    store: InMemoryRunStore
    factory: FakeFactory
    browser: FakeBrowser

    def token(self, path="/"):
        """Токен защиты формы со страницы, как это делает браузер."""
        html = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def run(self, **form):
        """Отправляет форму и ждёт конца запуска; возвращает (id запуска, ответ формы)."""
        data = {"site_url": SITE, "consent": "on", "csrf_token": self.token(), **form}
        reply = self.client.post("/check", data=data)
        if reply.status_code != 303:
            return None, reply
        run_id = reply.headers["Location"].rsplit("/", 1)[-1]
        assert self.manager.wait(run_id, WAIT_SECONDS), "запуск не завершился вовремя"
        return run_id, reply


@pytest.fixture
def behaviour():
    return Behaviour()


@pytest.fixture
def network(behaviour):
    """Имитированная сеть: к внешним сайтам запросы не уходят."""

    def site(request):
        if behaviour.before_home and request.url.rstrip("/") == SITE:
            behaviour.before_home()
        return site_reply(request)

    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add_callback(responses.GET, re.compile(re.escape(SITE) + r".*"), callback=site)
        mock.add_callback(responses.GET, re.compile(re.escape(API) + r".*"), callback=api_reply)
        yield mock


@pytest.fixture
def make_stack(network):
    def _make(browser_error=None):
        policy = TargetPolicy(resolver=resolve)
        http = SafeHttpClient(
            policy, timeout=1, max_bytes=100_000, max_redirects=3, max_retries=0, retry_delay=0
        )
        browser = FakeBrowser()
        factory = FakeFactory(browser=browser, error=browser_error)
        service = RunService(http, policy, default_checks(), browser_factory=factory)
        store = InMemoryRunStore(max_running=2)
        manager = RunManager(service, store)
        app = create_app(service=service, store=store, manager=manager)
        app.config.update(TESTING=True)
        return Stack(app.test_client(), manager, store, factory, browser)

    return _make


@pytest.fixture
def stack(make_stack):
    return make_stack()


@pytest.fixture
def gate(behaviour):
    """Запрос главной страницы ждёт, пока тест не откроет ворота: запуск «зависает» посреди работы."""
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        release.wait(10)

    behaviour.before_home = hold
    return entered, release
