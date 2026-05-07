from __future__ import annotations

import sqlite3
from functools import wraps
from typing import Any, Callable

from flask import g, redirect, render_template, request, url_for

from .config import COOKIE_NAME
from .db import connect


def current_user() -> sqlite3.Row | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    with connect() as conn:
        return conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND users.active = 1
            """,
            (token,),
        ).fetchone()


def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        if not g.user:
            return redirect(url_for("login_page"))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        if not g.user:
            return redirect(url_for("login_page"))
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
