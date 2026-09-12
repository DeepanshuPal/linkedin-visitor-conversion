"""Pipeline stages: ingest, qualify, draft, export, outcome backfill, run listing."""
import csv
import io
import json
import sys
from urllib.parse import urlsplit

from . import llm
from .db import record_run, utcnow

COLUMN_ALIASES = {
    "name": ["name", "full_name", "visitor", "person"],
    "headline": ["headline", "title", "job_title", "position"],
    "company": ["company", "organization", "org", "employer"],
    "profile_url": ["profile_url", "url", "profile", "linkedin", "linkedin_url", "profile url"],
    "visit_date": ["visit_date", "date", "visited_at", "visited", "visit date"],
}


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return f"https://{netloc}{path}"


def _map_columns(fieldnames):
    lower = {f.lower().strip(): f for f in (fieldnames or [])}
    out = {}
    for canon, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in lower:
                out[canon] = lower[a]
                break
    return out


def ingest_rows(conn, rows, source):
    started = utcnow()
    inserted, skipped = 0, 0
    for raw in rows:
        url = normalize_url(raw.get("profile_url"))
        if not url:
            skipped += 1
            continue
        name = (raw.get("name") or "").strip()
        if not name:
            slug = url.rsplit("/", 1)[-1].replace("-", " ").strip()
            name = slug.title() if slug else "Unknown"
        cur = conn.execute(
            """INSERT OR IGNORE INTO leads
               (name, headline, company, profile_url, visit_date, source, ingested_at)
               VALUES (?,?,?,?,?,?,?)""",
            (name, (raw.get("headline") or "").strip(),
             (raw.get("company") or "").strip(), url,
             (raw.get("visit_date") or "").strip(), source, utcnow()),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    record_run(conn, "ingest", started, inputs={"source": source},
               items_in=inserted + skipped, items_out=inserted,
               notes=f"{skipped} duplicate/invalid rows skipped")
    return inserted, skipped


def ingest_csv(conn, csv_text, source):
    reader = csv.DictReader(io.StringIO(csv_text))
    colmap = _map_columns(reader.fieldnames)
    if "profile_url" not in colmap:
        raise ValueError(
            "CSV must have a profile URL column (one of: "
            + ", ".join(COLUMN_ALIASES["profile_url"]) + ")")
    rows = [{canon: raw.get(col) for canon, col in colmap.items()} for raw in reader]
    return ingest_rows(conn, rows, source)


def qualify(conn, cfg, limit=None):
    started = utcnow()
    q = "SELECT * FROM leads WHERE status = 'new' ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    leads = conn.execute(q).fetchall()
    threshold = int(cfg.get("qualification_threshold", 60))
    pt = ct = 0
    cost = 0.0
    qualified = 0
    model = cfg["model"] if cfg.get("provider") != "mock" else "mock"
    for lead in leads:
        score, reason, usage = llm.qualify_lead(cfg, lead)
        pt += usage.get("prompt_tokens") or 0
        ct += usage.get("completion_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
        status = "qualified" if score >= threshold else "disqualified"
        qualified += status == "qualified"
        conn.execute(
            "UPDATE leads SET score=?, score_reason=?, qualified_at=?, status=? WHERE id=?",
            (score, reason, utcnow(), status, lead["id"]))
    conn.commit()
    record_run(conn, "qualify", started,
               inputs={"limit": limit, "threshold": threshold},
               model=model, prompt_version=llm.PROMPT_VERSION_QUALIFY,
               items_in=len(leads), items_out=qualified,
               prompt_tokens=pt, completion_tokens=ct,
               cost_usd=cost or None)
    return len(leads), qualified


def _few_shot_examples(conn, n):
    rows = conn.execute(
        "SELECT original, edited FROM edits ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [(r["original"], r["edited"]) for r in reversed(rows)]


def draft(conn, cfg, limit=None):
    started = utcnow()
    q = """SELECT * FROM leads WHERE status = 'qualified'
           AND id NOT IN (SELECT lead_id FROM drafts WHERE state IN ('pending','approved','edited','exported'))
           ORDER BY score DESC"""
    if limit:
        q += f" LIMIT {int(limit)}"
    leads = conn.execute(q).fetchall()
    examples = _few_shot_examples(conn, int(cfg.get("few_shot_examples", 3)))
    pt = ct = 0
    cost = 0.0
    truncated = 0
    model = cfg["model"] if cfg.get("provider") != "mock" else "mock"
    for lead in leads:
        body, usage, was_truncated = llm.draft_message(cfg, lead, examples)
        truncated += was_truncated
        pt += usage.get("prompt_tokens") or 0
        ct += usage.get("completion_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
        conn.execute(
            """INSERT INTO drafts (lead_id, kind, body, model, prompt_version, created_at)
               VALUES (?,?,?,?,?,?)""",
            (lead["id"], cfg.get("draft_kind", "connection_note"), body,
             model, llm.PROMPT_VERSION_DRAFT, utcnow()))
        conn.execute("UPDATE leads SET status='drafted' WHERE id=?", (lead["id"],))
    conn.commit()
    record_run(conn, "draft", started, inputs={"limit": limit},
               model=model, prompt_version=llm.PROMPT_VERSION_DRAFT,
               items_in=len(leads), items_out=len(leads),
               prompt_tokens=pt, completion_tokens=ct,
               cost_usd=cost or None,
               notes=f"{truncated} drafts truncated to char limit" if truncated else None)
    return len(leads)


def approved_today(conn):
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM drafts
           WHERE state IN ('approved','edited','exported')
           AND date(reviewed_at) = date('now')""").fetchone()
    return row["n"]


def export_approved(conn, cfg, out_path=None, mark_exported=True):
    started = utcnow()
    rows = conn.execute(
        """SELECT d.id AS draft_id, d.final_body, d.kind, l.name, l.headline,
                  l.company, l.profile_url, l.id AS lead_id
           FROM drafts d JOIN leads l ON l.id = d.lead_id
           WHERE d.state IN ('approved','edited')
           ORDER BY d.reviewed_at""").fetchall()
    cap = int(cfg.get("max_new_notes_per_day", 20))
    already = conn.execute(
        """SELECT COUNT(*) AS n FROM drafts
           WHERE state='exported' AND date(reviewed_at) = date('now')""").fetchone()["n"]
    warning = None
    if already + len(rows) > cap:
        warning = (f"rate cap: exporting {len(rows)} notes would bring today to "
                   f"{already + len(rows)} > max_new_notes_per_day={cap}. "
                   f"Exporting only the first {max(0, cap - already)}.")
        rows = rows[: max(0, cap - already)]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "headline", "company", "profile_url", "note", "chars"])
    for r in rows:
        note = r["final_body"]
        writer.writerow([r["name"], r["headline"], r["company"],
                         r["profile_url"], note, len(note)])
    text = output.getvalue()
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    if mark_exported:
        for r in rows:
            conn.execute("UPDATE drafts SET state='exported' WHERE id=?", (r["draft_id"],))
            conn.execute("UPDATE leads SET status='exported' WHERE id=?", (r["lead_id"],))
        conn.commit()
    record_run(conn, "export", started,
               inputs={"out_path": out_path, "cap": cap},
               items_in=len(rows), items_out=len(rows), notes=warning)
    return rows, text, warning


def set_outcome(conn, profile_url, field, at=None, notes=None):
    if field not in ("sent_at", "connected_at", "replied_at"):
        raise ValueError("field must be sent_at, connected_at or replied_at")
    url = normalize_url(profile_url)
    lead = conn.execute("SELECT id FROM leads WHERE profile_url=?", (url,)).fetchone()
    if not lead:
        return False
    conn.execute(
        f"""INSERT INTO outcomes (lead_id, {field}, notes) VALUES (?,?,?)
            ON CONFLICT(lead_id) DO UPDATE SET {field}=excluded.{field},
            notes=COALESCE(excluded.notes, outcomes.notes)""",
        (lead["id"], at or utcnow(), notes))
    conn.commit()
    return True


def list_runs(conn, limit=20):
    return conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
