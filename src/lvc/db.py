"""SQLite storage. Runs are append-only: rows are INSERTed at completion, never updated."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  headline TEXT,
  company TEXT,
  profile_url TEXT NOT NULL UNIQUE,
  visit_date TEXT,
  source TEXT,
  ingested_at TEXT NOT NULL,
  score INTEGER,
  score_reason TEXT,
  qualified_at TEXT,
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new','qualified','disqualified','drafted','approved','exported'))
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  kind TEXT NOT NULL,
  body TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','approved','edited','rejected','exported')),
  final_body TEXT,
  reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS edits (
  id INTEGER PRIMARY KEY,
  draft_id INTEGER NOT NULL REFERENCES drafts(id),
  original TEXT NOT NULL,
  edited TEXT NOT NULL,
  diff TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
  lead_id INTEGER PRIMARY KEY REFERENCES leads(id),
  sent_at TEXT,
  connected_at TEXT,
  replied_at TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  inputs TEXT,
  model TEXT,
  prompt_version TEXT,
  items_in INTEGER,
  items_out INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  cost_usd REAL,
  notes TEXT
);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record_run(conn, kind, started_at, inputs=None, model=None, prompt_version=None,
               items_in=None, items_out=None, prompt_tokens=None,
               completion_tokens=None, cost_usd=None, notes=None):
    """Append a completed run record. Never updates existing rows."""
    conn.execute(
        """INSERT INTO runs
           (kind, started_at, finished_at, inputs, model, prompt_version,
            items_in, items_out, prompt_tokens, completion_tokens, cost_usd, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (kind, started_at, utcnow(),
         json.dumps(inputs) if inputs is not None else None,
         model, prompt_version, items_in, items_out,
         prompt_tokens, completion_tokens, cost_usd, notes),
    )
    conn.commit()
