"""Export limits are based on when a note leaves the queue, not its review date."""
import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lvc.db import connect
from lvc.pipeline import export_approved


class ExportCap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "leads.db")
        self.conn = connect(self.path)
        self.cfg = {"max_new_notes_per_day": 2}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def note(self, suffix, reviewed_at, state="approved", exported_at=None):
        lead = self.conn.execute(
            "INSERT INTO leads (name, profile_url, ingested_at, status) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            (suffix, f"https://www.linkedin.com/in/{suffix}", reviewed_at,
             "exported" if state == "exported" else "approved"),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO drafts (lead_id, kind, body, created_at, state, "
            "final_body, reviewed_at, exported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead, "connection_note", "Hello", reviewed_at, state, "Hello",
             reviewed_at, exported_at),
        )
        self.conn.commit()

    def test_yesterday_reviewed_notes_consume_todays_export_cap(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        for suffix in ("one", "two", "three"):
            self.note(suffix, yesterday)
        first, text, warning = export_approved(self.conn, self.cfg)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(text)))), 2)
        self.assertIn("rate cap", warning)
        dates = self.conn.execute(
            "SELECT date(exported_at) FROM drafts WHERE state='exported'"
        ).fetchall()
        self.assertEqual([r[0] for r in dates], [datetime.now(timezone.utc).date().isoformat()] * 2)
        second, _, warning = export_approved(self.conn, self.cfg)
        self.assertEqual(len(second), 0)
        self.assertIn("rate cap", warning)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM drafts WHERE state='approved'"
        ).fetchone()[0], 1)

    def test_preview_does_not_mark_or_consume_cap(self):
        self.note("one", datetime.now(timezone.utc).isoformat())
        rows, _, _ = export_approved(self.conn, self.cfg, mark_exported=False)
        self.assertEqual(len(rows), 1)
        state = self.conn.execute("SELECT state, exported_at FROM drafts").fetchone()
        self.assertEqual((state[0], state[1]), ("approved", None))
        rows, _, _ = export_approved(self.conn, self.cfg)
        self.assertEqual(len(rows), 1)

    def test_existing_db_migration_does_not_forge_export_dates(self):
        self.conn.close()
        raw = sqlite3.connect(self.path)
        raw.execute("DROP TABLE drafts")
        raw.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, lead_id INTEGER, "
                    "kind TEXT, body TEXT, created_at TEXT, state TEXT, "
                    "final_body TEXT, reviewed_at TEXT)")
        raw.execute("INSERT INTO drafts (kind, body, created_at, state, reviewed_at) "
                    "VALUES ('connection_note', 'Hi', '2026-09-24', 'exported', '2026-09-24')")
        raw.commit()
        raw.close()
        self.conn = connect(self.path)
        self.assertIsNone(self.conn.execute("SELECT exported_at FROM drafts").fetchone()[0])
        self.conn.close()
        self.conn = connect(self.path)  # idempotent migration
        self.assertIsNone(self.conn.execute("SELECT exported_at FROM drafts").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
