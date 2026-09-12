"""Terminal approval queue: approve / edit / reject each pending draft.
Edits are stored with diffs and feed future drafts as few-shot examples.
Enforces max_new_notes_per_day on approvals."""
import difflib

from .db import record_run, utcnow
from .pipeline import approved_today


def _diff(original, edited):
    return "\n".join(difflib.unified_diff(
        original.splitlines(), edited.splitlines(),
        fromfile="draft", tofile="edited", lineterm=""))


def review(conn, cfg, input_fn=input, print_fn=print):
    started = utcnow()
    cap = int(cfg.get("max_new_notes_per_day", 20))
    rows = conn.execute(
        """SELECT d.id AS draft_id, d.body, d.kind, l.name, l.headline,
                  l.company, l.profile_url, l.score, l.id AS lead_id
           FROM drafts d JOIN leads l ON l.id = d.lead_id
           WHERE d.state = 'pending' ORDER BY l.score DESC, d.id""").fetchall()
    if not rows:
        print_fn("No pending drafts.")
        return {"approved": 0, "edited": 0, "rejected": 0, "skipped": 0}
    counts = {"approved": 0, "edited": 0, "rejected": 0, "skipped": 0}
    done_today = approved_today(conn)
    print_fn(f"{len(rows)} pending drafts. Approved today: {done_today}/{cap}.")
    for r in rows:
        print_fn("")
        print_fn(f"--- {r['name']} | {r['headline'] or ''} | {r['company'] or ''}")
        print_fn(f"    {r['profile_url']}  (score {r['score']})")
        print_fn(f"    [{r['kind']}, {len(r['body'])} chars]")
        print_fn(f"    {r['body']}")
        while True:
            choice = input_fn("[a]pprove [e]dit [r]eject [s]kip [q]uit > ").strip().lower()
            if choice in ("a", "e", "r", "s", "q", ""):
                break
            print_fn("    enter a, e, r, s or q")
        if choice == "q":
            break
        if choice in ("s", ""):
            counts["skipped"] += 1
            continue
        if choice in ("a", "e") and done_today >= cap:
            print_fn(f"    rate cap reached ({cap}/day) - cannot approve more today.")
            counts["skipped"] += 1
            continue
        if choice == "a":
            conn.execute(
                """UPDATE drafts SET state='approved', final_body=body,
                   reviewed_at=? WHERE id=?""", (utcnow(), r["draft_id"]))
            conn.execute("UPDATE leads SET status='approved' WHERE id=?", (r["lead_id"],))
            counts["approved"] += 1
            done_today += 1
        elif choice == "e":
            edited = input_fn("edited note (single line) > ").strip()
            if not edited:
                print_fn("    empty edit - skipped.")
                counts["skipped"] += 1
                continue
            limit = int(cfg.get("connection_note_char_limit", 300))
            if r["kind"] == "connection_note" and len(edited) > limit:
                print_fn(f"    over {limit} chars - rejected, try again.")
                counts["skipped"] += 1
                continue
            conn.execute(
                "INSERT INTO edits (draft_id, original, edited, diff, created_at) VALUES (?,?,?,?,?)",
                (r["draft_id"], r["body"], edited, _diff(r["body"], edited), utcnow()))
            conn.execute(
                """UPDATE drafts SET state='edited', final_body=?,
                   reviewed_at=? WHERE id=?""", (edited, utcnow(), r["draft_id"]))
            conn.execute("UPDATE leads SET status='approved' WHERE id=?", (r["lead_id"],))
            counts["edited"] += 1
            done_today += 1
        elif choice == "r":
            conn.execute(
                "UPDATE drafts SET state='rejected', reviewed_at=? WHERE id=?",
                (utcnow(), r["draft_id"]))
            counts["rejected"] += 1
        conn.commit()
    record_run(conn, "review", started, inputs={"cap": cap},
               items_in=len(rows),
               items_out=counts["approved"] + counts["edited"],
               notes=str(counts))
    return counts
