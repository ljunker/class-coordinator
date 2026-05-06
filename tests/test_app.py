import os
import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        os.environ["CLASS_COORDINATOR_DB"] = str(self.db_path)

        import importlib
        import class_coordinator.config
        import class_coordinator.db
        import class_coordinator.security

        self.config = importlib.reload(class_coordinator.config)
        self.security = importlib.reload(class_coordinator.security)
        self.db = importlib.reload(class_coordinator.db)
        self.db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("CLASS_COORDINATOR_DB", None)

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def request_with_form(self, data):
        body = urlencode(data, doseq=True).encode("utf-8")
        return type(
            "Request",
            (),
            {
                "headers": {"Content-Length": str(len(body))},
                "rfile": BytesIO(body),
            },
        )()

    def table_body(self, body: bytes) -> bytes:
        start = body.index(b"<tbody>")
        end = body.index(b"</tbody>", start)
        return body[start:end]

    def test_seeds_admin_and_mainstream_program(self):
        with self.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            figure_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM figures
                WHERE program_id = ?
                """,
                (program["id"],),
            ).fetchone()["count"]
            first_figures = conn.execute(
                """
                SELECT family, call_name
                FROM figures
                WHERE program_id = ?
                ORDER BY sort_order
                LIMIT 5
                """,
                (program["id"],),
            ).fetchall()

        self.assertEqual(admin["username"], "admin")
        self.assertEqual(program["name"], "Mainstream")
        self.assertEqual(program["effective_date"], "2026-09-01")
        self.assertGreaterEqual(figure_count, 50)
        self.assertEqual(first_figures[0]["family"], "Circle Family")
        self.assertEqual(
            [row["call_name"] for row in first_figures],
            [
                "Circle Left/Right (1/4, 1/2, 3/4, Full)",
                "Circle of 4 Left/Right (1/4, 1/2, 3/4, Full)",
                "Single Circle Left/Right (1/4, 1/2, 3/4, Full)",
                "Dosado",
                "Couples Promenade",
            ],
        )

    def test_password_hash_roundtrip(self):
        encoded = self.security.hash_password("secret")

        self.assertTrue(self.security.verify_password("secret", encoded))
        self.assertFalse(self.security.verify_password("wrong", encoded))
        self.assertTrue(encoded.startswith("$2b$13$"))

    def test_forwarded_prefix_rewrites_redirects_and_html_links(self):
        from class_coordinator.web import App, redirect

        app = App()
        status, headers, body = app.with_prefix(redirect("/login"), "/class")

        self.assertEqual(status, 303)
        self.assertEqual(dict(headers)["Location"], "/class/login")

        status, headers, body = app.with_prefix(
            (
                200,
                [("Content-Type", "text/html; charset=utf-8")],
                b'<a href="/classes">x</a><form action="/logout"></form>',
            ),
            "/class",
        )

        self.assertIn(b'href="/class/classes"', body)
        self.assertIn(b'action="/class/logout"', body)

    def test_forwarded_prefix_is_stripped_for_routing(self):
        from class_coordinator.web import App

        app = App()

        self.assertEqual(app.strip_prefix("/class/classes/1", "/class"), "/classes/1")
        self.assertEqual(app.strip_prefix("/class", "/class"), "/")
        self.assertEqual(app.strip_prefix("/classes/1", "/class"), "/classes/1")

    def test_class_form_can_render_name_field(self):
        from class_coordinator.web import App

        conn = self.connect()
        try:
            user = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
        finally:
            conn.close()

        status, headers, body = App().class_form(user, None)

        self.assertEqual(status, 200)
        self.assertIn(b'<input name="name"', body)

    def test_class_status_json_marks_teached_figures(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Test Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            figure = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ?
                ORDER BY sort_order
                LIMIT 1
                """,
                (program["id"],),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO figure_events
                    (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                VALUES (?, ?, 'taught', '2026-09-02', ?, '', ?)
                """,
                (class_id, figure["id"], admin["id"], self.db.now_iso()),
            )

        status, headers, body = App().class_status_json(admin, class_id)
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(headers[0][1], "application/json; charset=utf-8")
        self.assertEqual(payload["class"]["name"], "Test Class")
        self.assertTrue(payload["figures"][0]["teached"])
        self.assertEqual(payload["figures"][0]["first_taught"], "2026-09-02")
        self.assertFalse(payload["figures"][1]["teached"])

    def test_class_status_json_is_public_with_cors(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Public Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid

        status, headers, body = App().class_status_json(None, class_id)
        header_map = dict(headers)
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(header_map["Access-Control-Allow-Origin"], "*")
        self.assertEqual(payload["class"]["name"], "Public Class")

    def test_class_detail_is_public_read_only(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Public Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid

        status, headers, body = App().class_detail(None, class_id)

        self.assertEqual(status, 200)
        self.assertIn(b"Public Class", body)
        self.assertNotIn(b'name="taught"', body)
        self.assertNotIn("Einträge speichern".encode("utf-8"), body)

    def test_class_detail_for_authorized_user_has_write_controls(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Writable Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid

        status, headers, body = App().class_detail(admin, class_id)

        self.assertEqual(status, 200)
        self.assertIn(b'name="taught"', body)
        self.assertIn("Einträge speichern".encode("utf-8"), body)

    def test_class_detail_new_filter_shows_next_ten_untaught_figures(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Filter Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            figures = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ?
                ORDER BY sort_order
                LIMIT 13
                """,
                (program["id"],),
            ).fetchall()
            for figure in figures[:3]:
                conn.execute(
                    """
                    INSERT INTO figure_events
                        (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                    VALUES (?, ?, 'taught', '2026-09-02', ?, '', ?)
                    """,
                    (class_id, figure["id"], admin["id"], self.db.now_iso()),
                )

        status, headers, body = App().class_detail(admin, class_id, view="new")
        table_body = self.table_body(body)

        self.assertEqual(status, 200)
        self.assertNotIn(figures[0]["call_name"].encode("utf-8"), table_body)
        self.assertIn(figures[3]["call_name"].encode("utf-8"), table_body)
        self.assertIn(figures[12]["call_name"].encode("utf-8"), table_body)
        self.assertEqual(table_body.count(b'name="taught"'), 10)

    def test_class_detail_review_filter_shows_taught_but_unreviewed_figures(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Review Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            figures = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ?
                ORDER BY sort_order
                LIMIT 3
                """,
                (program["id"],),
            ).fetchall()
            events = [
                (figures[0]["id"], "taught", "2026-09-05"),
                (figures[1]["id"], "taught", "2026-09-03"),
                (figures[1]["id"], "reviewed", "2026-09-04"),
                (figures[2]["id"], "taught", "2026-09-01"),
                (figures[2]["id"], "reviewed", "2026-09-02"),
                (figures[2]["id"], "taught", "2026-09-06"),
            ]
            for figure_id, action, event_date in events:
                conn.execute(
                    """
                    INSERT INTO figure_events
                        (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, '', ?)
                    """,
                    (class_id, figure_id, action, event_date, admin["id"], self.db.now_iso()),
                )

        status, headers, body = App().class_detail(admin, class_id, view="review")
        table_body = self.table_body(body)

        self.assertEqual(status, 200)
        self.assertIn(figures[0]["call_name"].encode("utf-8"), table_body)
        self.assertNotIn(figures[1]["call_name"].encode("utf-8"), table_body)
        self.assertIn(figures[2]["call_name"].encode("utf-8"), table_body)
        self.assertEqual(table_body.count(b'name="reviewed"'), 2)

    def test_class_detail_marks_review_status_with_row_classes(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Color Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            figures = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ?
                ORDER BY sort_order
                LIMIT 2
                """,
                (program["id"],),
            ).fetchall()
            events = [
                (figures[0]["id"], "taught", "2026-09-01"),
                (figures[1]["id"], "taught", "2026-09-01"),
                (figures[1]["id"], "reviewed", "2026-09-02"),
            ]
            for figure_id, action, event_date in events:
                conn.execute(
                    """
                    INSERT INTO figure_events
                        (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, '', ?)
                    """,
                    (class_id, figure_id, action, event_date, admin["id"], self.db.now_iso()),
                )

        status, headers, body = App().class_detail(admin, class_id)

        self.assertEqual(status, 200)
        self.assertIn(b'<tr class="needs-review">', body)
        self.assertIn(b'<tr class="reviewed">', body)

    def test_family_row_is_green_only_when_all_family_figures_are_reviewed(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Family Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            circle_figures = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ? AND family = 'Circle Family'
                ORDER BY sort_order
                """,
                (program["id"],),
            ).fetchall()
            for figure in circle_figures[:2]:
                for action, event_date in (("taught", "2026-09-01"), ("reviewed", "2026-09-02")):
                    conn.execute(
                        """
                        INSERT INTO figure_events
                            (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, '', ?)
                        """,
                        (class_id, figure["id"], action, event_date, admin["id"], self.db.now_iso()),
                    )

        status, headers, body = App().class_detail(admin, class_id)

        self.assertEqual(status, 200)
        self.assertIn(b'<tr class="family-row">', body)
        self.assertNotIn(b'<tr class="family-row reviewed">', body)

    def test_family_row_is_yellow_when_any_family_figure_needs_review(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Family Yellow Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            figure = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ? AND family = 'Circle Family'
                ORDER BY sort_order
                LIMIT 1
                """,
                (program["id"],),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO figure_events
                    (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                VALUES (?, ?, 'taught', '2026-09-01', ?, '', ?)
                """,
                (class_id, figure["id"], admin["id"], self.db.now_iso()),
            )

        status, headers, body = App().class_detail(admin, class_id)

        self.assertEqual(status, 200)
        self.assertIn(b'<tr class="family-row needs-review">', body)
        self.assertNotIn(b'<tr class="family-row reviewed">', body)

    def test_family_row_is_green_when_all_family_figures_are_reviewed(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            class_id = conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, 'Family Complete Class', 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], self.db.now_iso()),
            ).lastrowid
            circle_figures = conn.execute(
                """
                SELECT * FROM figures
                WHERE program_id = ? AND family = 'Circle Family'
                ORDER BY sort_order
                """,
                (program["id"],),
            ).fetchall()
            for figure in circle_figures:
                for action, event_date in (("taught", "2026-09-01"), ("reviewed", "2026-09-02")):
                    conn.execute(
                        """
                        INSERT INTO figure_events
                            (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, '', ?)
                        """,
                        (class_id, figure["id"], action, event_date, admin["id"], self.db.now_iso()),
                    )

        status, headers, body = App().class_detail(admin, class_id)

        self.assertEqual(status, 200)
        self.assertIn(b'<tr class="family-row reviewed">', body)

    def test_user_can_update_display_name_and_password(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()

        response = App().update_profile(
            self.request_with_form(
                {
                    "display_name": "New Name",
                    "current_password": "admin123",
                    "new_password": "new-secret",
                    "new_password_confirm": "new-secret",
                }
            ),
            user,
        )

        with self.db.connect() as conn:
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

        self.assertEqual(response[0], 303)
        self.assertEqual(updated["display_name"], "New Name")
        self.assertTrue(self.security.verify_password("new-secret", updated["password_hash"]))

    def test_admin_can_reset_user_password(self):
        from class_coordinator.web import App

        with self.db.connect() as conn:
            user_id = conn.execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, role, active, created_at)
                VALUES ('caller', 'Caller', ?, 'caller', 1, ?)
                """,
                (self.security.hash_password("old-secret"), self.db.now_iso()),
            ).lastrowid

        response = App().set_user_password(
            self.request_with_form({"password": "reset-secret"}),
            user_id,
        )

        with self.db.connect() as conn:
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        self.assertEqual(response[0], 303)
        self.assertTrue(self.security.verify_password("reset-secret", updated["password_hash"]))


if __name__ == "__main__":
    unittest.main()
