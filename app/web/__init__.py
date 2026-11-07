from flask import Flask

from app.bootstrap import build_run_service
from app.services.run_store import InMemoryRunStore
from app.web.routes import Service, Store, bp


def create_app(service: Service | None = None, store: Store | None = None) -> Flask:
    """Фабрика приложения. Сервис и хранилище запусков можно подставить снаружи (в тестах)."""
    app = Flask(__name__)
    app.extensions["run_service"] = service or build_run_service()
    app.extensions["run_store"] = store or InMemoryRunStore()
    app.register_blueprint(bp)
    return app
