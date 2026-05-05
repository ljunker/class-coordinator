from __future__ import annotations

import json
import secrets
import sqlite3
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import COOKIE_NAME, STATIC_DIR
from .db import connect, init_db, now_iso, today_iso
from .security import hash_password, verify_password
from .templates import esc, render_page, render_template


Response = tuple[int, list[tuple[str, str]], bytes]


def redirect(location: str, headers: list[tuple[str, str]] | None = None) -> Response:
    response_headers = [("Location", location)]
    if headers:
        response_headers.extend(headers)
    return HTTPStatus.SEE_OTHER, response_headers, b""


def parse_body(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8") if length else ""
    return parse_qs(raw, keep_blank_values=True)


def single(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    return values[0].strip() if values else default


def checked_values(form: dict[str, list[str]], key: str) -> set[int]:
    result: set[int] = set()
    for value in form.get(key, []):
        try:
            result.add(int(value))
        except ValueError:
            pass
    return result


def make_flash(message: str) -> str:
    return quote(message)


def consume_flash(query: dict[str, list[str]]) -> str:
    values = query.get("flash", [])
    return unquote(values[0]) if values else ""


class App:
    def current_user(self, handler: BaseHTTPRequestHandler) -> sqlite3.Row | None:
        cookies = SimpleCookie()
        cookies.load(handler.headers.get("Cookie", ""))
        morsel = cookies.get(COOKIE_NAME)
        if not morsel:
            return None
        with connect() as conn:
            return conn.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token = ? AND users.active = 1
                """,
                (morsel.value,),
            ).fetchone()

    def handle(self, handler: BaseHTTPRequestHandler) -> Response:
        parsed = urlparse(handler.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        user = self.current_user(handler)

        if parsed.path.startswith("/static/"):
            return self.static_file(parsed.path)
        if handler.command == "GET" and path == "/login":
            return self.login_page(consume_flash(query))
        if handler.command == "POST" and path == "/login":
            return self.login(handler)
        if handler.command == "POST" and path == "/logout":
            return self.logout(handler)
        if handler.command == "GET" and path.startswith("/classes/") and path.endswith("/status.json"):
            class_id = self.path_id(path.removesuffix("/status.json"), "/classes/")
            if class_id is not None:
                return self.class_status_json(user, class_id)
        if handler.command == "GET" and path.startswith("/classes/"):
            class_id = self.path_id(path, "/classes/")
            if class_id is not None:
                return self.class_detail(
                    user,
                    class_id,
                    consume_flash(query),
                    view=single(query, "view", "all"),
                )
        if not user:
            return redirect("/login")
        if handler.command == "GET" and path == "/":
            return redirect("/classes")
        if handler.command == "GET" and path == "/classes":
            return self.classes_page(user, consume_flash(query))
        if handler.command == "GET" and path == "/profile":
            return self.profile_page(user, consume_flash(query))
        if handler.command == "POST" and path == "/profile":
            return self.update_profile(handler, user)
        if handler.command == "GET" and path == "/classes/new":
            return self.require_admin(user, lambda: self.class_form(user, None))
        if handler.command == "POST" and path == "/classes/new":
            return self.require_admin(user, lambda: self.save_class(handler, None))
        if handler.command == "POST" and path.startswith("/classes/") and path.endswith("/mark"):
            class_id = self.path_id(path.removesuffix("/mark"), "/classes/")
            if class_id is not None:
                return self.mark_figures(handler, user, class_id)
        if handler.command == "GET" and path.startswith("/admin/classes/") and path.endswith("/edit"):
            class_id = self.path_id(path.removesuffix("/edit"), "/admin/classes/")
            if class_id is not None:
                return self.require_admin(user, lambda: self.class_form(user, class_id))
        if handler.command == "POST" and path.startswith("/admin/classes/") and path.endswith("/edit"):
            class_id = self.path_id(path.removesuffix("/edit"), "/admin/classes/")
            if class_id is not None:
                return self.require_admin(user, lambda: self.save_class(handler, class_id))
        if handler.command == "GET" and path == "/admin/users":
            return self.require_admin(user, lambda: self.users_page(user, consume_flash(query)))
        if handler.command == "GET" and path == "/admin/users/new":
            return self.require_admin(user, lambda: self.user_form(user))
        if handler.command == "POST" and path == "/admin/users/new":
            return self.require_admin(user, lambda: self.create_user(handler))
        if handler.command == "POST" and path.startswith("/admin/users/") and path.endswith("/toggle"):
            user_id = self.path_id(path.removesuffix("/toggle"), "/admin/users/")
            if user_id is not None:
                return self.require_admin(user, lambda: self.toggle_user(user, user_id))
        if handler.command == "POST" and path.startswith("/admin/users/") and path.endswith("/password"):
            user_id = self.path_id(path.removesuffix("/password"), "/admin/users/")
            if user_id is not None:
                return self.require_admin(user, lambda: self.set_user_password(handler, user_id))
        return self.page("Nicht gefunden", user, render_template("error.html", message="Diese Seite gibt es nicht."), status=404)

    def static_file(self, path: str) -> Response:
        relative = path.removeprefix("/static/").replace("..", "")
        file_path = STATIC_DIR / relative
        if not file_path.is_file():
            return 404, [("Content-Type", "text/plain; charset=utf-8")], b"not found"
        content_type = "text/css; charset=utf-8" if file_path.suffix == ".css" else "application/octet-stream"
        return 200, [("Content-Type", content_type)], file_path.read_bytes()

    def path_id(self, path: str, prefix: str) -> int | None:
        try:
            return int(path.removeprefix(prefix))
        except ValueError:
            return None

    def require_admin(self, user: sqlite3.Row, callback: Callable[[], Response]) -> Response:
        if user["role"] != "admin":
            body = render_template("error.html", message="Nur der Admin darf diese Aktion ausführen.")
            return self.page("Keine Berechtigung", user, body, status=403)
        return callback()

    def login_page(self, flash: str = "") -> Response:
        return self.page("Login", None, render_template("login.html"), flash=flash)

    def login(self, handler: BaseHTTPRequestHandler) -> Response:
        form = parse_body(handler)
        username = single(form, "username")
        password = single(form, "password")
        with connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
            ).fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                return redirect("/login?flash=" + make_flash("Login fehlgeschlagen"))
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, user["id"], now_iso()),
            )
        return redirect(
            "/classes",
            [("Set-Cookie", f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax")],
        )

    def logout(self, handler: BaseHTTPRequestHandler) -> Response:
        cookies = SimpleCookie()
        cookies.load(handler.headers.get("Cookie", ""))
        morsel = cookies.get(COOKIE_NAME)
        if morsel:
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (morsel.value,))
        return redirect(
            "/login?flash=" + make_flash("Abgemeldet"),
            [("Set-Cookie", f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")],
        )

    def profile_page(self, user: sqlite3.Row, flash: str = "") -> Response:
        body = render_template(
            "profile.html",
            display_name=esc(user["display_name"]),
            username=esc(user["username"]),
        )
        return self.page("Profil", user, body, flash=flash)

    def update_profile(self, handler: BaseHTTPRequestHandler, user: sqlite3.Row) -> Response:
        form = parse_body(handler)
        display_name = single(form, "display_name")
        current_password = single(form, "current_password")
        new_password = single(form, "new_password")
        new_password_confirm = single(form, "new_password_confirm")
        if not display_name:
            return redirect("/profile?flash=" + make_flash("Anzeigename ist Pflicht"))
        if new_password:
            if not verify_password(current_password, user["password_hash"]):
                return redirect("/profile?flash=" + make_flash("Aktuelles Passwort stimmt nicht"))
            if new_password != new_password_confirm:
                return redirect("/profile?flash=" + make_flash("Neue Passwörter stimmen nicht überein"))
        with connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name, user["id"]),
            )
            if new_password:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(new_password), user["id"]),
                )
        return redirect("/profile?flash=" + make_flash("Profil gespeichert"))

    def classes_page(self, user: sqlite3.Row, flash: str = "") -> Response:
        with connect() as conn:
            if user["role"] == "admin":
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
                    (user["id"],),
                ).fetchall()
        rows = []
        for item in classes:
            inactive = "" if item["active"] else '<span class="pill muted">inaktiv</span>'
            rows.append(
                render_template(
                    "partials/class_row.html",
                    id=item["id"],
                    name=esc(item["name"]),
                    program_name=esc(item["program_name"]),
                    location=esc(item["location"]) or "ohne Ort",
                    starts_on=esc(item["starts_on"]) or "offen",
                    inactive=inactive,
                )
            )
        body = render_template(
            "classes.html",
            admin_action='<a class="button" href="/classes/new">Class anlegen</a>'
            if user["role"] == "admin"
            else "",
            rows="".join(rows) if rows else "<p>Noch keine Classes vorhanden.</p>",
        )
        return self.page("Classes", user, body, flash=flash)

    def class_form(self, user: sqlite3.Row, class_id: int | None) -> Response:
        with connect() as conn:
            programs = conn.execute("SELECT * FROM programs ORDER BY name").fetchall()
            callers = conn.execute(
                "SELECT * FROM users WHERE role = 'caller' ORDER BY display_name"
            ).fetchall()
            klass = None
            assigned: set[int] = set()
            if class_id:
                klass = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
                assigned = {
                    row["user_id"]
                    for row in conn.execute(
                        "SELECT user_id FROM caller_class_access WHERE class_id = ?",
                        (class_id,),
                    )
                }
        if class_id and not klass:
            return self.page("Nicht gefunden", user, render_template("error.html", message="Class nicht gefunden."), status=404)

        selected_program = klass["program_id"] if klass else (programs[0]["id"] if programs else "")
        program_options = "".join(
            render_template(
                "partials/option.html",
                value=program["id"],
                selected="selected" if program["id"] == selected_program else "",
                label=f"{esc(program['name'])} ({esc(program['effective_date'])})",
            )
            for program in programs
        )
        caller_checks = "".join(
            render_template(
                "partials/caller_checkbox.html",
                id=caller["id"],
                checked="checked" if caller["id"] in assigned else "",
                display_name=esc(caller["display_name"]),
                username=esc(caller["username"]),
            )
            for caller in callers
        )
        title = "Class bearbeiten" if klass else "Class anlegen"
        body = render_template(
            "class_form.html",
            title=title,
            action=f"/admin/classes/{class_id}/edit" if klass else "/classes/new",
            name=esc(klass["name"] if klass else ""),
            program_options=program_options,
            location=esc(klass["location"] if klass else ""),
            starts_on=esc(klass["starts_on"] if klass else ""),
            notes=esc(klass["notes"] if klass else ""),
            active_checked="checked" if not klass or klass["active"] else "",
            caller_checks=caller_checks or "<p>Lege zuerst Caller-Accounts an.</p>",
        )
        return self.page(title, user, body)

    def save_class(self, handler: BaseHTTPRequestHandler, class_id: int | None) -> Response:
        form = parse_body(handler)
        name = single(form, "name")
        program_id = int(single(form, "program_id", "0") or "0")
        location = single(form, "location")
        starts_on = single(form, "starts_on")
        notes = single(form, "notes")
        active = 1 if "active" in form else 0
        caller_ids = checked_values(form, "caller_ids")
        if not name or not program_id:
            return redirect("/classes?flash=" + make_flash("Name und Programm sind Pflicht"))
        with connect() as conn:
            if class_id:
                conn.execute(
                    """
                    UPDATE classes
                    SET name = ?, program_id = ?, location = ?, starts_on = ?,
                        notes = ?, active = ?
                    WHERE id = ?
                    """,
                    (name, program_id, location, starts_on, notes, active, class_id),
                )
                target_id = class_id
                conn.execute("DELETE FROM caller_class_access WHERE class_id = ?", (target_id,))
            else:
                cur = conn.execute(
                    """
                    INSERT INTO classes
                        (program_id, name, location, starts_on, notes, active, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (program_id, name, location, starts_on, notes, now_iso()),
                )
                target_id = cur.lastrowid
            for caller_id in caller_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO caller_class_access (user_id, class_id) VALUES (?, ?)",
                    (caller_id, target_id),
                )
        return redirect(f"/classes/{target_id}?flash=" + make_flash("Class gespeichert"))

    def class_detail(
        self,
        user: sqlite3.Row | None,
        class_id: int,
        flash: str = "",
        view: str = "all",
    ) -> Response:
        with connect() as conn:
            write_klass = self.visible_class(conn, user, class_id) if user else None
            klass = write_klass or self.public_class(conn, class_id)
            if not klass:
                body = render_template("error.html", message="Class nicht gefunden.")
                return self.page("Nicht gefunden", user, body, status=404)
            can_write = bool(write_klass)
            figures = conn.execute(
                "SELECT * FROM figures WHERE program_id = ? ORDER BY sort_order",
                (klass["program_id"],),
            ).fetchall()
            status = {
                row["figure_id"]: row
                for row in conn.execute(
                    """
                    SELECT
                        figure_id,
                        MIN(CASE WHEN action = 'taught' THEN event_date END) AS first_taught,
                        MAX(CASE WHEN action = 'taught' THEN event_date END) AS last_taught,
                        MAX(CASE WHEN action = 'reviewed' THEN event_date END) AS last_reviewed
                    FROM figure_events
                    WHERE class_id = ?
                    GROUP BY figure_id
                    """,
                    (class_id,),
                )
            }
            recent = conn.execute(
                """
                SELECT figure_events.*, figures.call_name, users.display_name
                FROM figure_events
                JOIN figures ON figures.id = figure_events.figure_id
                JOIN users ON users.id = figure_events.caller_id
                WHERE figure_events.class_id = ?
                ORDER BY figure_events.event_date DESC, figure_events.id DESC
                LIMIT 12
                """,
                (class_id,),
            ).fetchall()

        active_view = view if view in {"all", "review", "new"} else "all"
        figures = self.filter_figures(figures, status, active_view)
        figure_rows = []
        current_family = None
        for figure in figures:
            if figure["family"] != current_family:
                current_family = figure["family"]
                figure_rows.append(
                    render_template(
                        "partials/family_row.html",
                        number=esc(figure["number"]),
                        family=esc(figure["family"]),
                    )
                )
            item = status.get(figure["id"])
            figure_rows.append(
                render_template(
                    "partials/figure_row.html",
                    id=figure["id"],
                    call_name=esc(figure["call_name"]),
                    first_taught=esc(item["first_taught"] if item else "") or "-",
                    last_taught=esc(item["last_taught"] if item else "") or "-",
                    last_reviewed=esc(item["last_reviewed"] if item else "") or "-",
                    status_class=self.figure_row_status_class(item),
                    taught_control=(
                        f'<input type="checkbox" name="taught" value="{figure["id"]}" aria-label="taught {esc(figure["call_name"])}">'
                        if can_write
                        else "-"
                    ),
                    reviewed_control=(
                        f'<input type="checkbox" name="reviewed" value="{figure["id"]}" aria-label="reviewed {esc(figure["call_name"])}">'
                        if can_write
                        else "-"
                    ),
                )
            )
        if not figure_rows:
            figure_rows.append(
                '<tr><td colspan="6" class="empty-row">Keine passenden Figuren.</td></tr>'
            )
        figure_rows = self.apply_family_row_status(figure_rows)
        recent_items = "".join(
            render_template(
                "partials/recent_item.html",
                event_date=esc(row["event_date"]),
                call_name=esc(row["call_name"]),
                action=esc(self.action_label(row["action"])),
                display_name=esc(row["display_name"]),
            )
            for row in recent
        )
        body = render_template(
            "class_detail.html",
            id=class_id,
            name=esc(klass["name"]),
            program_name=esc(klass["program_name"]),
            location=esc(klass["location"]) or "ohne Ort",
            starts_on=esc(klass["starts_on"]) or "offen",
            admin_action=f'<a class="button secondary" href="/admin/classes/{class_id}/edit">Bearbeiten</a>'
            if user and user["role"] == "admin" and can_write
            else "",
            filter_tabs=self.filter_tabs(class_id, active_view),
            mark_bar=render_template("partials/mark_bar.html", today=today_iso())
            if can_write
            else "",
            today=today_iso(),
            figure_rows="".join(figure_rows),
            recent_items=recent_items or "<li>Noch keine Einträge.</li>",
        )
        return self.page(klass["name"], user, body, flash=flash)

    def figure_row_status_class(self, status: sqlite3.Row | None) -> str:
        if not status or not status["first_taught"]:
            return ""
        if self.needs_review(status):
            return "needs-review"
        if status["last_reviewed"]:
            return "reviewed"
        return ""

    def apply_family_row_status(self, rows: list[str]) -> list[str]:
        result: list[str] = []
        family_index: int | None = None
        family_statuses: list[str] = []

        def flush_family() -> None:
            if family_index is None:
                return
            status_class = self.family_status_class(family_statuses)
            result[family_index] = result[family_index].replace(
                'class="family-row"',
                f'class="family-row {status_class}"' if status_class else 'class="family-row"',
                1,
            )

        for row in rows:
            if 'class="family-row"' in row:
                flush_family()
                family_index = len(result)
                family_statuses = []
            elif 'class="needs-review"' in row:
                family_statuses.append("needs-review")
            elif 'class="reviewed"' in row:
                family_statuses.append("reviewed")
            elif "<td" in row:
                family_statuses.append("")
            result.append(row)
        flush_family()
        return result

    def family_status_class(self, statuses: list[str]) -> str:
        if not statuses:
            return ""
        if all(status == "reviewed" for status in statuses):
            return "reviewed"
        if any(status == "needs-review" for status in statuses):
            return "needs-review"
        return ""

    def filter_figures(
        self,
        figures: list[sqlite3.Row],
        status: dict[int, sqlite3.Row],
        view: str,
    ) -> list[sqlite3.Row]:
        if view == "new":
            return [
                figure
                for figure in figures
                if not (status.get(figure["id"]) and status[figure["id"]]["first_taught"])
            ][:10]
        if view == "review":
            return sorted(
                [
                    figure
                    for figure in figures
                    if self.needs_review(status.get(figure["id"]))
                ],
                key=lambda figure: status[figure["id"]]["last_taught"] or "",
                reverse=True,
            )
        return figures

    def needs_review(self, status: sqlite3.Row | None) -> bool:
        if not status or not status["first_taught"]:
            return False
        if not status["last_reviewed"]:
            return True
        return (status["last_taught"] or "") > status["last_reviewed"]

    def filter_tabs(self, class_id: int, active_view: str) -> str:
        filters = [
            ("all", "Alle", f"/classes/{class_id}"),
            ("review", "Zum wiederholen", f"/classes/{class_id}?view=review"),
            ("new", "Neue Figuren", f"/classes/{class_id}?view=new"),
        ]
        return "".join(
            render_template(
                "partials/filter_tab.html",
                href=href,
                label=label,
                active="active" if key == active_view else "",
            )
            for key, label, href in filters
        )

    def class_status_json(self, user: sqlite3.Row | None, class_id: int) -> Response:
        with connect() as conn:
            klass = (
                self.visible_class(conn, user, class_id)
                if user
                else self.public_class(conn, class_id)
            )
            if not klass:
                payload = {"error": "class not found"}
                return self.json_response(payload, status=404)
            figures = conn.execute(
                "SELECT * FROM figures WHERE program_id = ? ORDER BY sort_order",
                (klass["program_id"],),
            ).fetchall()
            status = {
                row["figure_id"]: row
                for row in conn.execute(
                    """
                    SELECT
                        figure_id,
                        MIN(CASE WHEN action = 'taught' THEN event_date END) AS first_taught,
                        MAX(CASE WHEN action = 'taught' THEN event_date END) AS last_taught,
                        MAX(CASE WHEN action = 'reviewed' THEN event_date END) AS last_reviewed
                    FROM figure_events
                    WHERE class_id = ?
                    GROUP BY figure_id
                    """,
                    (class_id,),
                )
            }

        payload = {
            "class": {
                "id": klass["id"],
                "name": klass["name"],
                "location": klass["location"],
                "starts_on": klass["starts_on"],
                "active": bool(klass["active"]),
            },
            "program": {
                "id": klass["program_id"],
                "name": klass["program_name"],
            },
            "figures": [
                self.figure_status_payload(figure, status.get(figure["id"]))
                for figure in figures
            ],
        }
        return self.json_response(payload)

    def public_class(self, conn: sqlite3.Connection, class_id: int):
        return conn.execute(
            """
            SELECT classes.*, programs.name AS program_name
            FROM classes
            JOIN programs ON programs.id = classes.program_id
            WHERE classes.id = ? AND classes.active = 1
            """,
            (class_id,),
        ).fetchone()

    def figure_status_payload(
        self, figure: sqlite3.Row, status: sqlite3.Row | None
    ) -> dict[str, Any]:
        first_taught = status["first_taught"] if status else None
        last_taught = status["last_taught"] if status else None
        last_reviewed = status["last_reviewed"] if status else None
        return {
            "id": figure["id"],
            "number": figure["number"],
            "family": figure["family"],
            "call_name": figure["call_name"],
            "teached": bool(first_taught),
            "reviewed": bool(last_reviewed),
            "first_taught": first_taught,
            "last_taught": last_taught,
            "last_reviewed": last_reviewed,
        }

    def visible_class(self, conn: sqlite3.Connection, user: sqlite3.Row, class_id: int):
        if user["role"] == "admin":
            return conn.execute(
                """
                SELECT classes.*, programs.name AS program_name
                FROM classes
                JOIN programs ON programs.id = classes.program_id
                WHERE classes.id = ?
                """,
                (class_id,),
            ).fetchone()
        return conn.execute(
            """
            SELECT classes.*, programs.name AS program_name
            FROM classes
            JOIN programs ON programs.id = classes.program_id
            JOIN caller_class_access access ON access.class_id = classes.id
            WHERE classes.id = ? AND access.user_id = ? AND classes.active = 1
            """,
            (class_id, user["id"]),
        ).fetchone()

    def mark_figures(self, handler: BaseHTTPRequestHandler, user: sqlite3.Row, class_id: int) -> Response:
        form = parse_body(handler)
        taught_ids = checked_values(form, "taught")
        reviewed_ids = checked_values(form, "reviewed")
        event_date = single(form, "event_date", today_iso())
        notes = single(form, "notes")
        with connect() as conn:
            klass = self.visible_class(conn, user, class_id)
            if not klass:
                body = render_template("error.html", message="Class nicht gefunden.")
                return self.page("Nicht gefunden", user, body, status=404)
            valid_ids = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM figures WHERE program_id = ?", (klass["program_id"],)
                )
            }
            for action, figure_ids in (("taught", taught_ids), ("reviewed", reviewed_ids)):
                for figure_id in sorted(figure_ids & valid_ids):
                    conn.execute(
                        """
                        INSERT INTO figure_events
                            (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (class_id, figure_id, action, event_date, user["id"], notes, now_iso()),
                    )
        return redirect(f"/classes/{class_id}?flash=" + make_flash("Einträge gespeichert"))

    def users_page(self, user: sqlite3.Row, flash: str = "") -> Response:
        with connect() as conn:
            users = conn.execute("SELECT * FROM users ORDER BY role, display_name").fetchall()
        rows = "".join(
            render_template(
                "partials/user_row.html",
                id=row["id"],
                display_name=esc(row["display_name"]),
                username=esc(row["username"]),
                role=esc(row["role"]),
                active="aktiv" if row["active"] else "inaktiv",
                password_action=render_template("partials/password_reset.html", id=row["id"]),
                toggle=""
                if row["id"] == user["id"]
                else render_template(
                    "partials/user_toggle.html",
                    id=row["id"],
                    label="Deaktivieren" if row["active"] else "Aktivieren",
                ),
            )
            for row in users
        )
        return self.page("Accounts", user, render_template("users.html", rows=rows), flash=flash)

    def user_form(self, user: sqlite3.Row) -> Response:
        return self.page("Account anlegen", user, render_template("user_form.html"))

    def create_user(self, handler: BaseHTTPRequestHandler) -> Response:
        form = parse_body(handler)
        display_name = single(form, "display_name")
        username = single(form, "username")
        password = single(form, "password")
        role = single(form, "role", "caller")
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
            return redirect("/admin/users?flash=" + make_flash("Benutzername existiert schon"))
        return redirect("/admin/users?flash=" + make_flash("Account angelegt"))

    def set_user_password(self, handler: BaseHTTPRequestHandler, user_id: int) -> Response:
        form = parse_body(handler)
        password = single(form, "password")
        if not password:
            return redirect("/admin/users?flash=" + make_flash("Passwort darf nicht leer sein"))
        with connect() as conn:
            user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return redirect("/admin/users?flash=" + make_flash("Account nicht gefunden"))
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return redirect("/admin/users?flash=" + make_flash("Passwort gesetzt"))

    def toggle_user(self, current_user: sqlite3.Row, user_id: int) -> Response:
        if current_user["id"] == user_id:
            return redirect("/admin/users?flash=" + make_flash("Eigenen Account nicht deaktiviert"))
        with connect() as conn:
            conn.execute(
                "UPDATE users SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
                (user_id,),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return redirect("/admin/users?flash=" + make_flash("Account aktualisiert"))

    def action_label(self, action: str) -> str:
        return "geteacht" if action == "taught" else "wiederholt"

    def page(
        self,
        title: str,
        user: sqlite3.Row | None,
        body: str,
        *,
        flash: str = "",
        status: int = 200,
    ) -> Response:
        return status, [("Content-Type", "text/html; charset=utf-8")], render_page(title, user, body, flash)

    def json_response(self, payload: Any, status: int = 200) -> Response:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return status, [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Access-Control-Allow-Origin", "*"),
        ], body


class Handler(BaseHTTPRequestHandler):
    app = App()

    def do_GET(self) -> None:
        self.respond(self.app.handle(self))

    def do_POST(self) -> None:
        self.respond(self.app.handle(self))

    def do_OPTIONS(self) -> None:
        self.respond((
            204,
            [
                ("Access-Control-Allow-Origin", "*"),
                ("Access-Control-Allow-Methods", "GET, OPTIONS"),
                ("Access-Control-Allow-Headers", "Content-Type"),
            ],
            b"",
        ))

    def respond(self, response: Response) -> None:
        status, headers, body = response
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    init_db()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Class Coordinator running at http://{host}:{port}")
    server.serve_forever()
