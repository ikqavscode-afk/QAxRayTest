from flask import Flask

from app.bootstrap import build_run_service
from app.web.routes import Service, bp


def create_app(service: Service | None = None) -> Flask:
    """Фабрика приложения. Сервис можно подставить снаружи (в тестах это заглушка)."""
    app = Flask(__name__)
    app.extensions["run_service"] = service or build_run_service()
    app.register_blueprint(bp)
    return app
