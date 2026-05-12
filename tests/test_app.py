import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        os.environ["CLASS_COORDINATOR_DB"] = str(self.db_path)

        import importlib
        import class_coordinator.config
        import class_coordinator.db
        import class_coordinator.security
        import class_coordinator.web

        self.config = importlib.reload(class_coordinator.config)
        self.security = importlib.reload(class_coordinator.security)
        self.db = importlib.reload(class_coordinator.db)
        self.web = importlib.reload(class_coordinator.web)
        self.app = self.web.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("CLASS_COORDINATOR_DB", None)

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def login_admin(self):
        return self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )

    def create_class(self, name="Test Class"):
        with self.db.connect() as conn:
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            return conn.execute(
                """
                INSERT INTO classes
                    (program_id, name, location, starts_on, notes, active, created_at)
                VALUES (?, ?, 'Hall', '2026-09-01', '', 1, ?)
                """,
                (program["id"], name, self.db.now_iso()),
            ).lastrowid

    def first_figures(self, limit=3):
        with self.db.connect() as conn:
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            return conn.execute(
                """
                SELECT *
                FROM figures
                WHERE program_id = ?
                ORDER BY sort_order
                LIMIT ?
                """,
                (program["id"], limit),
            ).fetchall()

    def add_event(self, class_id, figure_id, action, event_date):
        with self.db.connect() as conn:
            admin = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
            conn.execute(
                """
                INSERT INTO figure_events
                    (class_id, figure_id, action, event_date, caller_id, notes, created_at)
                VALUES (?, ?, ?, ?, ?, '', ?)
                """,
                (class_id, figure_id, action, event_date, admin["id"], self.db.now_iso()),
            )

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
                "SELECT COUNT(*) AS count FROM figures WHERE program_id = ?",
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
        response = self.client.get(
            "/class/classes",
            headers={"X-Forwarded-Prefix": "/class"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/class/login")

        class_id = self.create_class("Prefix Class")
        response = self.client.get(
            f"/class/classes/{class_id}",
            headers={"X-Forwarded-Prefix": "/class"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/class/static/style.css"', response.data)
        self.assertIn(f'action="/class/classes/{class_id}/mark"'.encode(), response.data)

    def test_class_form_can_render_name_field(self):
        self.login_admin()
        response = self.client.get("/classes/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<input name="name"', response.data)

    def test_class_status_json_marks_teached_figures(self):
        class_id = self.create_class()
        figure = self.first_figures(1)[0]
        self.add_event(class_id, figure["id"], "taught", "2026-09-02")

        response = self.client.get(f"/classes/{class_id}/status.json")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(payload["class"]["name"], "Test Class")
        self.assertTrue(payload["figures"][0]["teached"])
        self.assertEqual(payload["figures"][0]["first_taught"], "2026-09-02")
        self.assertFalse(payload["figures"][1]["teached"])

    def test_class_detail_is_public_read_only(self):
        class_id = self.create_class("Public Class")
        response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Public Class", response.data)
        self.assertNotIn(b'name="taught"', response.data)
        self.assertNotIn("Einträge speichern".encode(), response.data)

    def test_class_detail_for_authorized_user_has_write_controls(self):
        class_id = self.create_class("Writable Class")
        self.login_admin()
        response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="taught"', response.data)
        self.assertIn("Einträge speichern".encode(), response.data)

    def test_reset_class_button_is_admin_only(self):
        class_id = self.create_class("Reset Button Class")

        public_response = self.client.get(f"/classes/{class_id}")
        self.login_admin()
        admin_response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(admin_response.status_code, 200)
        self.assertNotIn("Zurücksetzen".encode(), public_response.data)
        self.assertIn("Zurücksetzen".encode(), admin_response.data)
        self.assertIn(f'action="/admin/classes/{class_id}/reset"'.encode(), admin_response.data)

    def test_admin_can_reset_class_events(self):
        class_id = self.create_class("Reset Class")
        figures = self.first_figures(2)
        self.add_event(class_id, figures[0]["id"], "taught", "2026-09-01")
        self.add_event(class_id, figures[1]["id"], "reviewed", "2026-09-02")
        self.login_admin()

        response = self.client.post(
            f"/admin/classes/{class_id}/reset",
            follow_redirects=False,
        )

        with self.connect() as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) AS count FROM figure_events WHERE class_id = ?",
                (class_id,),
            ).fetchone()["count"]
            klass = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], f"/classes/{class_id}")
        self.assertEqual(event_count, 0)
        self.assertEqual(klass["name"], "Reset Class")

    def test_class_detail_new_filter_shows_next_ten_untaught_figures(self):
        class_id = self.create_class("Filter Class")
        figures = self.first_figures(13)
        for figure in figures[:3]:
            self.add_event(class_id, figure["id"], "taught", "2026-09-02")
        self.login_admin()

        response = self.client.get(f"/classes/{class_id}?view=new")
        table_body = self.table_body(response.data)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(figures[0]["call_name"].encode(), table_body)
        self.assertIn(figures[3]["call_name"].encode(), table_body)
        self.assertIn(figures[12]["call_name"].encode(), table_body)
        self.assertEqual(table_body.count(b'name="taught"'), 10)

    def test_class_detail_review_filter_shows_taught_but_unreviewed_figures(self):
        class_id = self.create_class("Review Class")
        figures = self.first_figures(3)
        events = [
            (figures[0]["id"], "taught", "2026-09-05"),
            (figures[1]["id"], "taught", "2026-09-03"),
            (figures[1]["id"], "reviewed", "2026-09-04"),
            (figures[2]["id"], "taught", "2026-09-01"),
            (figures[2]["id"], "reviewed", "2026-09-02"),
            (figures[2]["id"], "taught", "2026-09-06"),
        ]
        for figure_id, action, event_date in events:
            self.add_event(class_id, figure_id, action, event_date)
        self.login_admin()

        response = self.client.get(f"/classes/{class_id}?view=review")
        table_body = self.table_body(response.data)

        self.assertEqual(response.status_code, 200)
        self.assertIn(figures[0]["call_name"].encode(), table_body)
        self.assertNotIn(figures[1]["call_name"].encode(), table_body)
        self.assertIn(figures[2]["call_name"].encode(), table_body)
        self.assertEqual(table_body.count(b'name="reviewed"'), 2)

    def test_class_detail_marks_review_status_with_row_classes(self):
        class_id = self.create_class("Color Class")
        figures = self.first_figures(2)
        self.add_event(class_id, figures[0]["id"], "taught", "2026-09-01")
        self.add_event(class_id, figures[1]["id"], "taught", "2026-09-01")
        self.add_event(class_id, figures[1]["id"], "reviewed", "2026-09-02")
        self.login_admin()

        response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<tr class="needs-review">', response.data)
        self.assertIn(b'<tr class="reviewed">', response.data)

    def test_family_row_is_green_only_when_all_family_figures_are_reviewed(self):
        class_id = self.create_class("Family Class")
        circle_figures = self.circle_figures()
        for figure in circle_figures[:2]:
            self.add_event(class_id, figure["id"], "taught", "2026-09-01")
            self.add_event(class_id, figure["id"], "reviewed", "2026-09-02")
        self.login_admin()

        response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<tr class="family-row ">', response.data)
        self.assertNotIn(b'<tr class="family-row reviewed">', response.data)

    def test_family_row_is_yellow_when_any_family_figure_needs_review(self):
        class_id = self.create_class("Family Yellow Class")
        figure = self.circle_figures()[0]
        self.add_event(class_id, figure["id"], "taught", "2026-09-01")
        self.login_admin()

        response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<tr class="family-row needs-review">', response.data)
        self.assertNotIn(b'<tr class="family-row reviewed">', response.data)

    def test_family_row_is_green_when_all_family_figures_are_reviewed(self):
        class_id = self.create_class("Family Complete Class")
        for figure in self.circle_figures():
            self.add_event(class_id, figure["id"], "taught", "2026-09-01")
            self.add_event(class_id, figure["id"], "reviewed", "2026-09-02")
        self.login_admin()

        response = self.client.get(f"/classes/{class_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<tr class="family-row reviewed">', response.data)

    def circle_figures(self):
        with self.db.connect() as conn:
            program = conn.execute(
                "SELECT * FROM programs WHERE key = 'mainstream-2026'"
            ).fetchone()
            return conn.execute(
                """
                SELECT *
                FROM figures
                WHERE program_id = ? AND family = 'Circle Family'
                ORDER BY sort_order
                """,
                (program["id"],),
            ).fetchall()

    def test_user_can_update_display_name_and_password(self):
        self.login_admin()
        response = self.client.post(
            "/profile",
            data={
                "display_name": "New Name",
                "current_password": "admin123",
                "new_password": "new-secret",
                "new_password_confirm": "new-secret",
            },
            follow_redirects=False,
        )

        with self.db.connect() as conn:
            updated = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated["display_name"], "New Name")
        self.assertTrue(self.security.verify_password("new-secret", updated["password_hash"]))

    def test_admin_can_reset_user_password(self):
        with self.db.connect() as conn:
            user_id = conn.execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, role, active, created_at)
                VALUES ('caller', 'Caller', ?, 'caller', 1, ?)
                """,
                (self.security.hash_password("old-secret"), self.db.now_iso()),
            ).lastrowid
        self.login_admin()

        response = self.client.post(
            f"/admin/users/{user_id}/password",
            data={"password": "reset-secret"},
            follow_redirects=False,
        )

        with self.db.connect() as conn:
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.security.verify_password("reset-secret", updated["password_hash"]))


if __name__ == "__main__":
    unittest.main()
