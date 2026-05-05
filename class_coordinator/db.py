from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

from .config import DATA_DIR, DB_PATH
from .security import hash_password


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def today_iso() -> str:
    return dt.date.today().isoformat()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'caller')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                effective_date TEXT,
                source_name TEXT,
                source_url TEXT
            );

            CREATE TABLE IF NOT EXISTS figures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
                number TEXT NOT NULL,
                family TEXT NOT NULL,
                call_name TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                UNIQUE (program_id, sort_order)
            );

            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL REFERENCES programs(id),
                name TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                starts_on TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS caller_class_access (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, class_id)
            );

            CREATE TABLE IF NOT EXISTS figure_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                figure_id INTEGER NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
                action TEXT NOT NULL CHECK (action IN ('taught', 'reviewed')),
                event_date TEXT NOT NULL,
                caller_id INTEGER NOT NULL REFERENCES users(id),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        seed_programs(conn)
        seed_admin(conn)


def seed_programs(conn: sqlite3.Connection) -> None:
    call_aliases = {
        "Star Right/Left": ["Star Right / Left"],
        "Ladies Chain, (Ladies Chain 3/4)": ["Ladies Chain / Ladies Chain 3/4"],
        "Four Ladies Chain, (Chain 3/4)": ["Four Ladies Chain / Four Ladies Chain 3/4"],
        "Separate Around 1 or 2 into the middle": [
            "Separate Around 1 or 2 and come into the middle"
        ],
        "Lead Right / Lead Left": ["Lead Right/Left"],
        "Veer Left / Veer Right": ["Veer Left/Right"],
        "Cast Off Three-Quarters": ["Cast Off 3/4"],
        "Recycle (from Waves only)": ["Recycle (From Waves Only)"],
    }
    for file_path in sorted(DATA_DIR.glob("*.json")):
        with file_path.open("r", encoding="utf-8") as f:
            program = json.load(f)
        conn.execute(
            """
            INSERT INTO programs (key, name, effective_date, source_name, source_url)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                name = excluded.name,
                effective_date = excluded.effective_date,
                source_name = excluded.source_name,
                source_url = excluded.source_url
            """,
            (
                program["key"],
                program["name"],
                program.get("effective_date", ""),
                program.get("source_name", ""),
                program.get("source_url", ""),
            ),
        )
        program_id = conn.execute(
            "SELECT id FROM programs WHERE key = ?", (program["key"],)
        ).fetchone()["id"]
        existing_rows = conn.execute(
            "SELECT * FROM figures WHERE program_id = ?", (program_id,)
        ).fetchall()
        existing_by_call = {row["call_name"]: row for row in existing_rows}

        conn.execute(
            "UPDATE figures SET sort_order = -1000000 - id WHERE program_id = ?",
            (program_id,),
        )
        used_ids: set[int] = set()
        sort_order = 1
        for family in program["families"]:
            for call_name in family["calls"]:
                existing = existing_by_call.get(call_name)
                if not existing:
                    for alias in call_aliases.get(call_name, []):
                        existing = existing_by_call.get(alias)
                        if existing:
                            break
                if existing:
                    conn.execute(
                        """
                        UPDATE figures
                        SET number = ?, family = ?, call_name = ?, sort_order = ?
                        WHERE id = ?
                        """,
                        (
                            family["number"],
                            family["name"],
                            call_name,
                            sort_order,
                            existing["id"],
                        ),
                    )
                    used_ids.add(existing["id"])
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO figures
                            (program_id, number, family, call_name, sort_order)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            program_id,
                            family["number"],
                            family["name"],
                            call_name,
                            sort_order,
                        ),
                    )
                    used_ids.add(cur.lastrowid)
                sort_order += 1

        obsolete_rows = [
            row for row in existing_rows if row["id"] not in used_ids
        ]
        for row in obsolete_rows:
            event_count = conn.execute(
                "SELECT COUNT(*) AS count FROM figure_events WHERE figure_id = ?",
                (row["id"],),
            ).fetchone()["count"]
            if event_count:
                conn.execute(
                    """
                    UPDATE figures
                    SET number = '', family = 'Legacy', sort_order = ?
                    WHERE id = ?
                    """,
                    (sort_order, row["id"]),
                )
                sort_order += 1
            else:
                conn.execute("DELETE FROM figures WHERE id = ?", (row["id"],))


def seed_admin(conn: sqlite3.Connection) -> None:
    admin_count = conn.execute(
        "SELECT COUNT(*) AS count FROM users WHERE role = 'admin'"
    ).fetchone()["count"]
    if admin_count:
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin123")
    conn.execute(
        """
        INSERT INTO users (username, display_name, password_hash, role, active, created_at)
        VALUES (?, ?, ?, 'admin', 1, ?)
        """,
        (username, "Administrator", hash_password(password), now_iso()),
    )
    print(
        f"Created initial admin account: {username} / {password}. "
        "Change the password after first login."
    )
