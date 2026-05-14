from __future__ import annotations

import sqlite3
from functools import wraps
from typing import Any, Callable

from flask import g, render_template, request

from .db import connect


def remote_username() -> str:
    return (
        request.headers.get("Remote-User", "")
        or request.environ.get("REMOTE_USER", "")
    ).strip()


def current_user() -> sqlite3.Row | None:
    username = remote_username()
    if not username:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (username,),
        ).fetchone()


def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        if not g.user:
            message = (
                "Dieser Tinyauth-User ist in Class Coordinator nicht freigeschaltet."
                if remote_username()
                else "Diese Seite wird über Tinyauth geschützt."
            )
            return (
                render_template(
                    "error.html",
                    title="Keine Berechtigung",
                    user=None,
                    message=message,
                ),
                403 if remote_username() else 401,
            )
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        if not g.user:
            message = (
                "Dieser Tinyauth-User ist in Class Coordinator nicht freigeschaltet."
                if remote_username()
                else "Diese Seite wird über Tinyauth geschützt."
            )
            return (
                render_template(
                    "error.html",
                    title="Keine Berechtigung",
                    user=None,
                    message=message,
                ),
                403 if remote_username() else 401,
            )
        if g.user["role"] != "admin":
            return (
                render_template(
                    "error.html",
                    title="Keine Berechtigung",
                    user=g.user,
                    message="Nur der Admin darf diese Aktion ausführen.",
                ),
                403,
            )
        return fn(*args, **kwargs)

    return wrapper
