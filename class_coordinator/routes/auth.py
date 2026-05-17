from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from flask import Flask, Response, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..config import TINYAUTH_LOGOUT_URL
from ..db import connect


def register_auth_routes(app: Flask) -> None:
    @app.get("/login")
    def login_page() -> str | Response:
        if g.user:
            return redirect(url_for("classes_page"))
        return render_template("login.html", title="Login", user=None)

    @app.post("/logout")
    def logout() -> Response:
        if TINYAUTH_LOGOUT_URL:
            return redirect(tinyauth_logout_url())
        flash("Logout läuft über Tinyauth.")
        return redirect(url_for("classes_page" if g.user else "login_page"))

    @app.get("/profile")
    @login_required
    def profile_page() -> str:
        return render_template("profile.html", title="Profil", user=g.user)

    @app.post("/profile")
    @login_required
    def update_profile() -> Response:
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Anzeigename ist Pflicht")
            return redirect(url_for("profile_page"))
        with connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name, g.user["id"]),
            )
        flash("Profil gespeichert")
        return redirect(url_for("profile_page"))


def tinyauth_logout_url() -> str:
    parts = urlsplit(TINYAUTH_LOGOUT_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["redirect_uri"] = public_app_root()
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def public_app_root() -> str:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    host = (
        request.headers.get("X-Forwarded-Host", "")
        or request.headers.get("Host", "")
        or request.host
    ).split(",")[0].strip()
    prefix = request.headers.get("X-Forwarded-Prefix", "").strip().strip("/")
    path = f"/{prefix}/" if prefix else "/"
    return f"{proto}://{host}{path}"
