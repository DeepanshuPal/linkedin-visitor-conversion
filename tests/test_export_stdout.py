"""CSV printed by export must be usable directly in a pipe."""
import csv
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lvc.cli import main
from lvc.db import connect


class ExportStdout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "lvc.db")
        conn = connect(self.db)
        self.addCleanup(conn.close)
        for n in ("Jane", "Sam"):
            lead = conn.execute(
                "INSERT INTO leads (name, profile_url, ingested_at, status) "
                "VALUES (?, ?, '2026-09-01', 'approved') RETURNING id",
                (n, f"https://www.linkedin.com/in/{n.lower()}"),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO drafts (lead_id, kind, body, created_at, state, "
                "final_body, reviewed_at) VALUES (?, 'connection_note', 'Hi', "
                "'2026-09-01', 'approved', 'Hi', '2026-09-01')", (lead,)
            )
        conn.commit()

    def export(self, cap):
        cfg = os.path.join(self.tmp.name, "config.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(f"max_new_notes_per_day: {cap}\n")
        output, diagnostics = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(diagnostics):
            main(["--config", cfg, "--db", self.db, "export"])
        return output.getvalue(), diagnostics.getvalue()

    def test_stdout_is_only_csv(self):
        output, diagnostics = self.export(2)
        rows = list(csv.DictReader(io.StringIO(output)))
        self.assertEqual([r["name"] for r in rows], ["Jane", "Sam"])
        self.assertNotIn("send these manually", output)
        self.assertIn("send these manually", diagnostics)

    def test_cap_warning_stays_off_csv_stream(self):
        output, diagnostics = self.export(1)
        rows = list(csv.DictReader(io.StringIO(output)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Jane")
        self.assertNotIn("warning", output)
        self.assertIn("rate cap", diagnostics)
        self.assertIn("send these manually", diagnostics)


if __name__ == "__main__":
    unittest.main()
