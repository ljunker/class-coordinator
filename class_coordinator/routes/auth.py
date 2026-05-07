from __future__ import annotations

import secrets

from flask import Flask, Response, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..config import COOKIE_NAME
from ..db import connect, now_iso
from ..security import hash_password, verify_password


def register_auth_routes(app: Flask) -> None:
    @app.get("/login")
    def login_page() -> str:
        return render_template("login.html", title="Login", user=None)

    @app.post("/login")
    def login() -> Response:
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
            ).fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                flash("Login fehlgeschlagen")
                return redirect(url_for("login_page"))
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, user["id"], now_iso()),
            )
        response = redirect(url_for("classes_page"))
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Lax")
        return response

    @app.post("/logout")
    def logout() -> Response:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        response = redirect(url_for("login_page"))
        response.delete_cookie(COOKIE_NAME)
        flash("Abgemeldet")
        return response

    @app.get("/profile")
    @login_required
    def profile_page() -> str:
        return render_template("profile.html", title="Profil", user=g.user)

    @app.post("/profile")
    @login_required
    def update_profile() -> Response:
        display_name = request.form.get("display_name", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        new_password_confirm = request.form.get("new_password_confirm", "")
        if not display_name:
            flash("Anzeigename ist Pflicht")
            return redirect(url_for("profile_page"))
        if new_password:
            if not verify_password(current_password, g.user["password_hash"]):
                flash("Aktuelles Passwort stimmt nicht")
                return redirect(url_for("profile_page"))
            if new_password != new_password_confirm:
                flash("Neue Passwörter stimmen nicht überein")
                return redirect(url_for("profile_page"))
        with connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name, g.user["id"]),
            )
            if new_password:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(new_password), g.user["id"]),
                )
        flash("Profil gespeichert")
        return redirect(url_for("profile_page"))
