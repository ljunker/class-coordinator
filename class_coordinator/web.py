from __future__ import annotations

import secrets
import sqlite3
from functools import wraps
from typing import Any, Callable

from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .config import COOKIE_NAME, STATIC_DIR, TEMPLATE_DIR
from .db import connect, init_db, now_iso, today_iso
from .security import hash_password, verify_password


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

    @app.get("/")
    @login_required
    def index() -> Response:
        return redirect(url_for("classes_page"))

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

    @app.get("/classes/<int:class_id>")
    def class_detail(class_id: int) -> str | tuple[str, int]:
        data = class_detail_context(g.user, class_id, request.args.get("view", "all"))
        if data is None:
            return error_page("Class nicht gefunden.", 404)
        return render_template("class_detail.html", **data)

    @app.post("/classes/<int:class_id>/mark")
    @login_required
    def mark_figures(class_id: int) -> Response | tuple[str, int]:
        with connect() as conn:
            klass = visible_class(conn, g.user, class_id)
            if not klass:
                return error_page("Class nicht gefunden.", 404)
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

    @app.errorhandler(404)
    def not_found(_: Exception) -> tuple[str, int]:
        return error_page("Diese Seite gibt es nicht.", 404)

    def error_page(message: str, status: int) -> tuple[str, int]:
        return (
            render_template("error.html", title="Nicht gefunden", user=g.user, message=message),
            status,
        )

    return app


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


def checked_values(key: str) -> set[int]:
    result: set[int] = set()
    for value in request.form.getlist(key):
        try:
            result.add(int(value))
        except ValueError:
            pass
    return result


def render_class_form(class_id: int | None) -> str | tuple[str, int]:
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
        return (
            render_template(
                "error.html",
                title="Nicht gefunden",
                user=g.user,
                message="Class nicht gefunden.",
            ),
            404,
        )
    title = "Class bearbeiten" if klass else "Class anlegen"
    selected_program = klass["program_id"] if klass else (programs[0]["id"] if programs else "")
    return render_template(
        "class_form.html",
        title=title,
        user=g.user,
        klass=klass,
        class_id=class_id,
        programs=programs,
        selected_program=selected_program,
        callers=callers,
        assigned=assigned,
    )


def save_class(class_id: int | None) -> int | None:
    name = request.form.get("name", "").strip()
    program_id = int(request.form.get("program_id", "0") or "0")
    location = request.form.get("location", "").strip()
    starts_on = request.form.get("starts_on", "").strip()
    notes = request.form.get("notes", "").strip()
    active = 1 if "active" in request.form else 0
    caller_ids = checked_values("caller_ids")
    if not name or not program_id:
        return None
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
    return target_id


def class_detail_context(
    user: sqlite3.Row | None,
    class_id: int,
    view: str,
) -> dict[str, Any] | None:
    with connect() as conn:
        write_klass = visible_class(conn, user, class_id) if user else None
        klass = write_klass or public_class(conn, class_id)
        if not klass:
            return None
        can_write = bool(write_klass)
        figures = conn.execute(
            "SELECT * FROM figures WHERE program_id = ? ORDER BY sort_order",
            (klass["program_id"],),
        ).fetchall()
        status = figure_status(conn, class_id)
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
    visible_figures = filter_figures(figures, status, active_view)
    family_statuses = family_statuses_for(figures, status)
    return {
        "title": klass["name"],
        "user": user,
        "klass": klass,
        "class_id": class_id,
        "can_write": can_write,
        "today": today_iso(),
        "groups": group_figures(visible_figures, status, family_statuses, can_write),
        "recent": recent,
        "active_view": active_view,
    }


def class_status_payload(user: sqlite3.Row | None, class_id: int) -> dict[str, Any]:
    with connect() as conn:
        klass = visible_class(conn, user, class_id) if user else public_class(conn, class_id)
        if not klass:
            return {"error": "class not found"}
        figures = conn.execute(
            "SELECT * FROM figures WHERE program_id = ? ORDER BY sort_order",
            (klass["program_id"],),
        ).fetchall()
        status = figure_status(conn, class_id)
    return {
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
        "figures": [figure_status_payload(figure, status.get(figure["id"])) for figure in figures],
    }


def figure_status(conn: sqlite3.Connection, class_id: int) -> dict[int, sqlite3.Row]:
    return {
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


def group_figures(
    figures: list[sqlite3.Row],
    status: dict[int, sqlite3.Row],
    family_statuses: dict[str, str],
    can_write: bool,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_family: dict[str, dict[str, Any]] = {}
    for figure in figures:
        family = figure["family"]
        if family not in by_family:
            group = {
                "number": figure["number"],
                "family": family,
                "status_class": family_statuses.get(family, ""),
                "figures": [],
            }
            by_family[family] = group
            groups.append(group)
        row_status = status.get(figure["id"])
        by_family[family]["figures"].append(
            {
                "figure": figure,
                "status": row_status,
                "status_class": figure_row_status_class(row_status),
                "can_write": can_write,
            }
        )
    return groups


def family_statuses_for(
    figures: list[sqlite3.Row],
    status: dict[int, sqlite3.Row],
) -> dict[str, str]:
    statuses: dict[str, list[str]] = {}
    for figure in figures:
        statuses.setdefault(figure["family"], []).append(
            figure_row_status_class(status.get(figure["id"]))
        )
    return {family: family_status_class(values) for family, values in statuses.items()}


def family_status_class(statuses: list[str]) -> str:
    if not statuses:
        return ""
    if all(status == "reviewed" for status in statuses):
        return "reviewed"
    if any(status == "needs-review" for status in statuses):
        return "needs-review"
    return ""


def figure_row_status_class(status: sqlite3.Row | None) -> str:
    if not status or not status["first_taught"]:
        return ""
    if needs_review(status):
        return "needs-review"
    if status["last_reviewed"]:
        return "reviewed"
    return ""


def filter_figures(
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
            [figure for figure in figures if needs_review(status.get(figure["id"]))],
            key=lambda figure: status[figure["id"]]["last_taught"] or "",
            reverse=True,
        )
    return figures


def needs_review(status: sqlite3.Row | None) -> bool:
    if not status or not status["first_taught"]:
        return False
    if not status["last_reviewed"]:
        return True
    return (status["last_taught"] or "") > status["last_reviewed"]


def figure_status_payload(
    figure: sqlite3.Row, status: sqlite3.Row | None
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


def visible_class(conn: sqlite3.Connection, user: sqlite3.Row | None, class_id: int):
    if not user:
        return None
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


def public_class(conn: sqlite3.Connection, class_id: int):
    return conn.execute(
        """
        SELECT classes.*, programs.name AS program_name
        FROM classes
        JOIN programs ON programs.id = classes.program_id
        WHERE classes.id = ? AND classes.active = 1
        """,
        (class_id,),
    ).fetchone()


def action_label(action: str) -> str:
    return "geteacht" if action == "taught" else "wiederholt"


def run(host: str = "127.0.0.1", port: int = 41234) -> None:
    app = create_app()
    app.run(host=host, port=port)
