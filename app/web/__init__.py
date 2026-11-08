import os

from flask import Flask

from app.bootstrap import build_run_service
from app.config import Settings
from app.core.security.redactor import Redactor
from app.logging_setup import configure_logging
from app.services.rate_limiter import RateLimiter
from app.services.run_manager import RunManager, Service
from app.services.run_store import InMemoryRunStore, RunStore
from app.web.csrf import init_csrf
from app.web.routes import Manager, bp


def create_app(
    service: Service | None = None,
    store: RunStore | None = None,
    manager: Manager | None = None,
    limiter: RateLimiter | None = None,
    settings: Settings | None = None,
) -> Flask:
    """Фабрика приложения. Службу, хранилище и менеджер запусков можно подставить (в тестах)."""
    configure_logging()
    app = Flask(__name__)
    app.jinja_env.filters["redact"] = Redactor().redact_text  # адрес сайта на странице
    settings = settings or Settings.from_env(os.environ)
    store = store or InMemoryRunStore(max_running=settings.max_parallel_runs)
    manager = manager or RunManager(service or build_run_service(), store)
    app.extensions["run_store"] = store
    app.extensions["run_manager"] = manager
    app.extensions["rate_limiter"] = limiter or RateLimiter(
        settings.rate_limit_runs, settings.rate_limit_window
    )
    init_csrf(app)
    app.register_blueprint(bp)
    return app
