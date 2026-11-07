from flask import Flask

from app.bootstrap import build_run_service
from app.services.run_manager import RunManager, Service
from app.services.run_store import InMemoryRunStore, RunStore
from app.web.routes import Manager, bp

MAX_RUNNING = 2  # одновременно выполняющихся запусков (у каждого свой браузер)


def create_app(
    service: Service | None = None,
    store: RunStore | None = None,
    manager: Manager | None = None,
) -> Flask:
    """Фабрика приложения. Службу, хранилище и менеджер запусков можно подставить (в тестах)."""
    app = Flask(__name__)
    store = store or InMemoryRunStore(max_running=MAX_RUNNING)
    manager = manager or RunManager(service or build_run_service(), store)
    app.extensions["run_store"] = store
    app.extensions["run_manager"] = manager
    app.register_blueprint(bp)
    return app
