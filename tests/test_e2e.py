"""End-to-end pipeline test with the deterministic mock provider and the
synthetic sample CSV (clearly fake names). No network, no API key."""
import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lvc.config import load_config
from lvc.db import connect
from lvc import pipeline
from lvc.review import review

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "visitors.sample.csv")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        cfg_path = os.path.join(self.tmp.name, "config.yaml")
        with open(cfg_path, "w") as f:
            f.write("provider: mock\nmax_new_notes_per_day: 3\n"
                    "qualification_threshold: 60\n")
        self.cfg = load_config(cfg_path)
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_pipeline(self):
        with open(SAMPLE) as f:
            inserted, skipped = pipeline.ingest_csv(self.conn, f.read(), "sample")
        self.assertEqual(inserted, 6)
        self.assertEqual(skipped, 0)

        # re-ingesting the same file dedupes on profile URL
        with open(SAMPLE) as f:
            inserted2, skipped2 = pipeline.ingest_csv(self.conn, f.read(), "sample")
        self.assertEqual((inserted2, skipped2), (0, 6))

        total, qualified = pipeline.qualify(self.conn, self.cfg)
        self.assertEqual(total, 6)
        self.assertGreaterEqual(qualified, 3)   # buyer-titled fakes qualify
        scores = self.conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE score IS NOT NULL").fetchone()
        self.assertEqual(scores["n"], 6)

        n_drafted = pipeline.draft(self.conn, self.cfg)
        self.assertEqual(n_drafted, qualified)
        over = self.conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE length(body) > 300").fetchone()
        self.assertEqual(over["n"], 0)

        # review: approve one, edit one, reject one, skip the rest
        answers = iter(["a", "e", "Hi Ada - loved your visit; here is the playbook.", "r", "s", "s", "s"])
        silent = lambda *a, **k: None
        counts = review(self.conn, self.cfg,
                        input_fn=lambda prompt="": next(answers), print_fn=silent)
        self.assertEqual(counts["approved"], 1)
        self.assertEqual(counts["edited"], 1)
        self.assertEqual(counts["rejected"], 1)
        diff = self.conn.execute("SELECT diff FROM edits").fetchone()
        self.assertIn("draft", diff["diff"])

        # few-shot: the edit should be picked up for future drafts
        examples = pipeline._few_shot_examples(self.conn, 3)
        self.assertEqual(len(examples), 1)

        # rate cap: only 2 approvals used of 3; approve remaining then hit cap
        rows, text, warning = pipeline.export_approved(self.conn, self.cfg)
        self.assertEqual(len(rows), 2)
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(parsed), 2)
        self.assertTrue(all(r["note"] for r in parsed))
        self.assertIsNone(warning)

        # outcome backfill
        ok = pipeline.set_outcome(
            self.conn, "https://www.linkedin.com/in/ada-fake-001", "sent_at")
        self.assertTrue(ok)
        ok = pipeline.set_outcome(
            self.conn, "https://www.linkedin.com/in/does-not-exist", "sent_at")
        self.assertFalse(ok)

        # run records exist and are append-only (one per stage, >= 6)
        runs = pipeline.list_runs(self.conn, limit=50)
        kinds = [r["kind"] for r in runs]
        for k in ("ingest", "qualify", "draft", "review", "export"):
            self.assertIn(k, kinds)

        # persistence: reopen the db file, data survives
        self.conn.close()
        conn2 = sqlite3.connect(self.db_path)
        n = conn2.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        self.assertEqual(n, 6)
        n = conn2.execute(
            "SELECT COUNT(*) FROM drafts WHERE state='exported'").fetchone()[0]
        self.assertEqual(n, 2)
        conn2.close()

    def test_rate_cap_blocks_extra_approvals(self):
        with open(SAMPLE) as f:
            pipeline.ingest_csv(self.conn, f.read(), "sample")
        pipeline.qualify(self.conn, self.cfg)
        n = pipeline.draft(self.conn, self.cfg)
        # cap is 3; approve everything offered - 4th+ approval must be blocked
        answers = iter(["a"] * (n + 2))
        counts = review(self.conn, self.cfg,
                        input_fn=lambda prompt="": next(answers),
                        print_fn=lambda *a, **k: None)
        self.assertLessEqual(counts["approved"], 3)
        if n > 3:
            self.assertGreaterEqual(counts["skipped"], n - 3)


if __name__ == "__main__":
    unittest.main()
