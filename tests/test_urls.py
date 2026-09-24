"""Profile URL canonicalization: one person, one lead, however the URL is spelled."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lvc import pipeline
from lvc.db import connect
from lvc.urls import normalize_url

CANON = "https://www.linkedin.com/in/jane-example"


class NormalizeUrl(unittest.TestCase):
    def test_linkedin_variants_collapse(self):
        for raw in [
            "https://www.linkedin.com/in/jane-example",
            "https://linkedin.com/in/jane-example/",
            "http://www.linkedin.com/in/jane-example?trk=public_profile",
            "linkedin.com/in/jane-example",
            "https://uk.linkedin.com/in/jane-example",
            "https://in.linkedin.com/in/jane-example#experience",
            "https://www.linkedin.com/in/Jane-Example",
            "  HTTPS://WWW.LINKEDIN.COM/in/jane-example/  ",
        ]:
            self.assertEqual(normalize_url(raw), CANON, raw)

    def test_other_hosts_keep_path_case(self):
        self.assertEqual(normalize_url("https://Example.com/People/Jane/"),
                         "https://example.com/People/Jane")

    def test_lookalike_host_is_not_linkedin(self):
        self.assertEqual(normalize_url("https://notlinkedin.com/in/Jane"),
                         "https://notlinkedin.com/in/Jane")

    def test_blank(self):
        self.assertEqual(normalize_url("  "), "")


class Dedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_person_from_two_exports_is_one_lead(self):
        conn = connect(self.db_path)
        pipeline.ingest_csv(conn, "name,profile_url\nJane,https://www.linkedin.com/in/jane-example\n", "followers")
        inserted, skipped = pipeline.ingest_csv(
            conn, "name,url\nJane,https://uk.linkedin.com/in/Jane-Example/?trk=x\n", "visitors")
        self.assertEqual((inserted, skipped), (0, 1))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 1)
        self.assertTrue(pipeline.set_outcome(conn, "linkedin.com/in/JANE-EXAMPLE", "sent_at"))

    def test_existing_db_rows_are_migrated_without_merging(self):
        conn = connect(self.db_path)
        conn.close()
        raw = sqlite3.connect(self.db_path)
        for i, url in enumerate(["https://linkedin.com/in/Jane-Example",
                                 "https://www.linkedin.com/in/jane-example",
                                 "https://linkedin.com/in/Sam-Example"]):
            raw.execute("INSERT INTO leads (name, profile_url, ingested_at) VALUES (?,?,?)",
                        (f"p{i}", url, "2026-09-01"))
        raw.commit()
        raw.close()

        conn = connect(self.db_path)
        urls = sorted(r[0] for r in conn.execute("SELECT profile_url FROM leads"))
        # Sam moves to the canonical key; the older Jane duplicate keeps its
        # URL instead of being merged into the existing canonical Jane lead.
        self.assertEqual(urls, ["https://linkedin.com/in/Jane-Example",
                                "https://www.linkedin.com/in/jane-example",
                                "https://www.linkedin.com/in/sam-example"])
        inserted, _ = pipeline.ingest_csv(conn, "url\nlinkedin.com/in/sam-example/\n", "again")
        self.assertEqual(inserted, 0)
        self.assertTrue(pipeline.set_outcome(conn, "https://uk.linkedin.com/in/Sam-Example", "connected_at"))


if __name__ == "__main__":
    unittest.main()
