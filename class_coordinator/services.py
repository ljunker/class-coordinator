from __future__ import annotations

import sqlite3
from typing import Any

from flask import g, render_template, request

from .db import connect, now_iso, today_iso


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
