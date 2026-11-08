from flask import Flask

from app.bootstrap import build_run_service
from app.core.security.redactor import Redactor
from app.logging_setup import configure_logging
from app.services.run_manager import RunManager, Service
from app.services.run_store import InMemoryRunStore, RunStore
from app.web.csrf import init_csrf
from app.web.routes import Manager, bp

MAX_RUNNING = 2  # одновременно выполняющихся запусков (у каждого свой браузер)


def create_app(
    service: Service | None = None,
    store: RunStore | None = None,
    manager: Manager | None = None,
) -> Flask:
    """Фабрика приложения. Службу, хранилище и менеджер запусков можно подставить (в тестах)."""
    configure_logging()
    app = Flask(__name__)
    app.jinja_env.filters["redact"] = Redactor().redact_text  # адрес сайта на странице
    store = store or InMemoryRunStore(max_running=MAX_RUNNING)
    manager = manager or RunManager(service or build_run_service(), store)
    app.extensions["run_store"] = store
    app.extensions["run_manager"] = manager
    init_csrf(app)
    app.register_blueprint(bp)
    return app
