from __future__ import annotations

import sqlite3

from flask import Flask, Response, flash, g, redirect, render_template, request, url_for

from ..auth import admin_required
from ..db import connect, now_iso
from ..security import hash_password


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
        password = request.form.get("password", "")
        role = request.form.get("role", "caller")
        if role not in {"admin", "caller"}:
            role = "caller"
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users
                        (username, display_name, password_hash, role, active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (username, display_name, hash_password(password), role, now_iso()),
                )
        except sqlite3.IntegrityError:
            flash("Benutzername existiert schon")
            return redirect(url_for("users_page"))
        flash("Account angelegt")
        return redirect(url_for("users_page"))

    @app.post("/admin/users/<int:user_id>/password")
    @admin_required
    def set_user_password(user_id: int) -> Response:
        password = request.form.get("password", "")
        if not password:
            flash("Passwort darf nicht leer sein")
            return redirect(url_for("users_page"))
        with connect() as conn:
            user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                flash("Account nicht gefunden")
                return redirect(url_for("users_page"))
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        flash("Passwort gesetzt")
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
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        flash("Account aktualisiert")
        return redirect(url_for("users_page"))
