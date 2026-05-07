from __future__ import annotations

from typing import Any, Callable

from flask import Flask, Response, g, render_template, request

from .auth import current_user
from .config import STATIC_DIR, TEMPLATE_DIR
from .db import init_db
from .routes import register_routes
from .services import action_label


class PrefixMiddleware:
    def __init__(self, app: Callable):
        self.app = app

    def __call__(self, environ: dict[str, Any], start_response: Callable):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "").strip()
        if prefix and prefix != "/":
            prefix = "/" + prefix.strip("/")
            path_info = environ.get("PATH_INFO", "")
            if path_info == prefix or path_info.startswith(prefix + "/"):
                environ["PATH_INFO"] = path_info[len(prefix):] or "/"
            environ["SCRIPT_NAME"] = prefix
        return self.app(environ, start_response)


def create_app() -> Flask:
    init_db()
    app = Flask(
        __name__,
        static_folder=str(STATIC_DIR),
        template_folder=str(TEMPLATE_DIR),
    )
    app.secret_key = "class-coordinator-local-flash-key"
    app.wsgi_app = PrefixMiddleware(app.wsgi_app)
    app.jinja_env.globals["action_label"] = action_label

    @app.before_request
    def load_user() -> None:
        g.user = current_user()

    @app.after_request
    def add_cors_headers(response: Response) -> Response:
        if request.path.endswith("/status.json") or request.method == "OPTIONS":
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    @app.errorhandler(404)
    def not_found(_: Exception) -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                title="Nicht gefunden",
                user=g.user,
                message="Diese Seite gibt es nicht.",
            ),
            404,
        )

    register_routes(app)
    return app


def run(host: str = "127.0.0.1", port: int = 41234) -> None:
    app = create_app()
    app.run(host=host, port=port)
