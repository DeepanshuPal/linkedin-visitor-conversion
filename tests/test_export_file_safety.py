"""An export must not destroy an earlier CSV or consume approved notes on collision."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lvc.cli import main
from lvc.db import connect
from lvc.pipeline import export_approved


class ExportFileSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "lvc.db")
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO leads (name, profile_url, ingested_at, status) "
                          "VALUES ('Jane', 'https://www.linkedin.com/in/jane', '2026-09-01', 'approved')")
        self.conn.execute("INSERT INTO drafts (lead_id, kind, body, created_at, state, "
                          "final_body, reviewed_at) "
                          "VALUES (1, 'connection_note', 'Hi', '2026-09-01', "
                          "'approved', 'Hi', '2026-09-01')")
        self.conn.commit()

    def test_existing_csv_is_not_overwritten_or_consumed(self):
        path = Path(self.tmp.name) / "batch.csv"
        path.write_text("previous batch\n")
        with self.assertRaises(FileExistsError):
            export_approved(self.conn, {"max_new_notes_per_day": 20}, out_path=path)
        self.assertEqual(path.read_text(), "previous batch\n")
        self.assertEqual(self.conn.execute("SELECT state FROM drafts").fetchone()[0], "approved")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
        rows, _, _ = export_approved(self.conn, {"max_new_notes_per_day": 20},
                                     out_path=str(Path(self.tmp.name) / "fresh.csv"))
        self.assertEqual(len(rows), 1)

    def test_cli_reports_collision_without_traceback(self):
        path = Path(self.tmp.name) / "batch.csv"
        path.write_text("earlier batch\n")
        from contextlib import redirect_stderr
        from io import StringIO
        error = StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as exit_:
            main(["--db", self.db, "export", "--out", str(path)])
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("already exists", error.getvalue())
        self.assertEqual(path.read_text(), "earlier batch\n")
        self.assertEqual(self.conn.execute("SELECT state FROM drafts").fetchone()[0], "approved")

    def test_existing_csv_refused_even_for_empty_batch(self):
        self.conn.execute("UPDATE drafts SET state='exported'")
        self.conn.commit()
        path = Path(self.tmp.name) / "batch.csv"
        path.write_text("previous batch\n")
        with self.assertRaises(FileExistsError):
            export_approved(self.conn, {}, out_path=path)
        self.assertEqual(path.read_text(), "previous batch\n")


if __name__ == "__main__":
    unittest.main()
