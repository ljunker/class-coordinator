from __future__ import annotations

from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, url_for

from ..auth import admin_required, login_required
from ..db import connect, now_iso, today_iso
from ..services import (
    checked_values,
    class_detail_context,
    class_status_payload,
    render_class_form,
    save_class,
    visible_class,
)


def register_class_routes(app: Flask) -> None:
    @app.get("/")
    @login_required
    def index() -> Response:
        return redirect(url_for("classes_page"))

    @app.get("/classes")
    @login_required
    def classes_page() -> str:
        with connect() as conn:
            if g.user["role"] == "admin":
                classes = conn.execute(
                    """
                    SELECT classes.*, programs.name AS program_name
                    FROM classes
                    JOIN programs ON programs.id = classes.program_id
                    ORDER BY classes.active DESC, classes.starts_on, classes.name
                    """
                ).fetchall()
            else:
                classes = conn.execute(
                    """
                    SELECT classes.*, programs.name AS program_name
                    FROM classes
                    JOIN programs ON programs.id = classes.program_id
                    JOIN caller_class_access access ON access.class_id = classes.id
                    WHERE access.user_id = ? AND classes.active = 1
                    ORDER BY classes.starts_on, classes.name
                    """,
                    (g.user["id"],),
                ).fetchall()
        return render_template("classes.html", title="Classes", user=g.user, classes=classes)

    @app.get("/classes/new")
    @admin_required
    def new_class_form() -> str:
        return render_class_form(None)

    @app.post("/classes/new")
    @admin_required
    def create_class() -> Response:
        class_id = save_class(None)
        if class_id is None:
            flash("Name und Programm sind Pflicht")
            return redirect(url_for("classes_page"))
        flash("Class gespeichert")
        return redirect(url_for("class_detail", class_id=class_id))

    @app.get("/admin/classes/<int:class_id>/edit")
    @admin_required
    def edit_class_form(class_id: int) -> str | tuple[str, int]:
        return render_class_form(class_id)

    @app.post("/admin/classes/<int:class_id>/edit")
    @admin_required
    def update_class(class_id: int) -> Response:
        saved_id = save_class(class_id)
        if saved_id is None:
            flash("Name und Programm sind Pflicht")
            return redirect(url_for("classes_page"))
        flash("Class gespeichert")
        return redirect(url_for("class_detail", class_id=saved_id))

    @app.post("/admin/classes/<int:class_id>/reset")
    @admin_required
    def reset_class(class_id: int) -> Response | tuple[str, int]:
        with connect() as conn:
            klass = visible_class(conn, g.user, class_id)
            if not klass:
                return (
                    render_template(
                        "error.html",
                        title="Nicht gefunden",
                        user=g.user,
                        message="Class nicht gefunden.",
                    ),
                    404,
                )
            conn.execute("DELETE FROM figure_events WHERE class_id = ?", (class_id,))
        flash("Class zurückgesetzt")
        return redirect(url_for("class_detail", class_id=class_id))

    @app.get("/classes/<int:class_id>")
    def class_detail(class_id: int) -> str | tuple[str, int]:
        data = class_detail_context(g.user, class_id, request.args.get("view", "all"))
        if data is None:
            return (
                render_template(
                    "error.html",
                    title="Nicht gefunden",
                    user=g.user,
                    message="Class nicht gefunden.",
                ),
                404,
            )
        return render_template("class_detail.html", **data)

    @app.post("/classes/<int:class_id>/mark")
    @login_required
    def mark_figures(class_id: int) -> Response | tuple[str, int]:
        with connect() as conn:
            klass = visible_class(conn, g.user, class_id)
            if not klass:
                return (
                    render_template(
                        "error.html",
                        title="Nicht gefunden",
                        user=g.user,
                        message="Class nicht gefunden.",
                    ),
                    404,
                )
            valid_ids = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM figures WHERE program_id = ?", (klass["program_id"],)
                )
            }
            taught_ids = checked_values("taught")
            reviewed_ids = checked_values("reviewed")
            event_date = request.form.get("event_date", today_iso()).strip()
            notes = request.form.get("notes", "").strip()
            for action, figure_ids in (("taught", taught_ids), ("reviewed", reviewed_ids)):
                for figure_id in sorted(figure_ids & valid_ids):
                    conn.execute(
                        """
                        INSERT INTO figure_events
                            (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (class_id, figure_id, action, event_date, g.user["id"], notes, now_iso()),
                    )
        flash("Einträge gespeichert")
        return redirect(url_for("class_detail", class_id=class_id))

    @app.get("/classes/<int:class_id>/status.json")
    def class_status_json(class_id: int) -> Response:
        payload = class_status_payload(g.user, class_id)
        status = 200 if "error" not in payload else 404
        return jsonify(payload), status
