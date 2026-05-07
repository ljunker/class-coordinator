from __future__ import annotations

from flask import Flask

from .admin import register_admin_routes
from .auth import register_auth_routes
from .classes import register_class_routes


def register_routes(app: Flask) -> None:
    register_auth_routes(app)
    register_class_routes(app)
    register_admin_routes(app)
