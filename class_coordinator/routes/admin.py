from __future__ import annotations

import sqlite3

from flask import Flask, Response, flash, g, redirect, render_template, request, url_for

from ..auth import admin_required
from ..db import connect, now_iso


def register_admin_routes(app: Flask) -> None:
    @app.get("/admin/users")
    @admin_required
    def users_page() -> str:
        with connect() as conn:
            users = conn.execute("SELECT * FROM users ORDER BY role, display_name").fetchall()
        return render_template("users.html", title="Accounts", user=g.user, users=users)

    @app.get("/admin/users/new")
    @admin_required
    def user_form() -> str:
        return render_template("user_form.html", title="Account anlegen", user=g.user)

    @app.post("/admin/users/new")
    @admin_required
    def create_user() -> Response:
        display_name = request.form.get("display_name", "").strip()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "caller")
        if role not in {"admin", "caller"}:
            role = "caller"
        if not display_name or not username:
            flash("Anzeigename und Tinyauth-Login sind Pflicht")
            return redirect(url_for("users_page"))
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users
                        (username, display_name, password_hash, role, active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (username, display_name, "", role, now_iso()),
                )
        except sqlite3.IntegrityError:
            flash("Tinyauth-Login existiert schon")
            return redirect(url_for("users_page"))
        flash("Account angelegt")
        return redirect(url_for("users_page"))

    @app.post("/admin/users/<int:user_id>/toggle")
    @admin_required
    def toggle_user(user_id: int) -> Response:
        if g.user["id"] == user_id:
            flash("Eigenen Account nicht deaktiviert")
            return redirect(url_for("users_page"))
        with connect() as conn:
            conn.execute(
                "UPDATE users SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
                (user_id,),
            )
        flash("Account aktualisiert")
        return redirect(url_for("users_page"))
