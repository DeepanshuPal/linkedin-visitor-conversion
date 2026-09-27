"""lvc command line interface."""
import argparse
import sys

from . import __version__
from .config import load_config
from .db import connect
from . import pipeline
from .review import review as review_queue

STARTER_CONFIG = """\
# lvc config - fill in your ICP and voice, keep keys in env vars.
icp_blurb: ""                # who buys from you, one paragraph
qualification_criteria: ""   # e.g. "B2B SaaS founders and growth leads, 10-200 staff"
voice_notes: ""              # how you write; drafts match this + your past edits

provider: openrouter         # openrouter | mock (mock = offline dry run, no key)
model: openai/gpt-4o-mini    # any OpenRouter model id
openrouter_api_key_env: OPENROUTER_API_KEY

qualification_threshold: 60  # 0-100; >= threshold moves to drafting
draft_kind: connection_note  # connection_note | followup
connection_note_char_limit: 300
few_shot_examples: 3
max_new_notes_per_day: 20    # rate cap on approvals - protects your account

db_path: lvc.db
"""


def _open(args):
    cfg = load_config(args.config)
    db_path = args.db or cfg["db_path"]
    return cfg, connect(db_path)


def cmd_init(args):
    cfg_path = args.config or "config.yaml"
    import os
    if os.path.exists(cfg_path):
        print(f"{cfg_path} already exists, leaving it alone.")
    else:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(STARTER_CONFIG)
        print(f"wrote {cfg_path} - fill in icp_blurb, qualification_criteria, voice_notes.")
    cfg, conn = _open(args)
    conn.execute("SELECT 1 FROM leads LIMIT 1")
    print(f"database ready at {args.db or cfg['db_path']}")


def cmd_ingest(args):
    cfg, conn = _open(args)
    if args.csv:
        with open(args.csv, "r", encoding="utf-8") as f:
            text = f.read()
        source = args.csv
    else:
        print("Paste CSV (header + rows), then Ctrl-D:", file=sys.stderr)
        text = sys.stdin.read()
        source = "paste"
    inserted, skipped = pipeline.ingest_csv(conn, text, source)
    print(f"ingested {inserted} new leads ({skipped} duplicates/invalid skipped)")


def cmd_qualify(args):
    cfg, conn = _open(args)
    try:
        total, qualified = pipeline.qualify(conn, cfg, limit=args.limit)
    except Exception as e:
        print(f"qualify failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"qualified {qualified}/{total} leads "
          f"(threshold {cfg['qualification_threshold']})")


def cmd_draft(args):
    cfg, conn = _open(args)
    try:
        n = pipeline.draft(conn, cfg, limit=args.limit)
    except Exception as e:
        print(f"draft failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"drafted {n} notes - run `lvc review` to approve them")


def cmd_review(args):
    cfg, conn = _open(args)
    counts = review_queue(conn, cfg)
    print(f"done: {counts}")


def cmd_export(args):
    cfg, conn = _open(args)
    try:
        rows, text, warning = pipeline.export_approved(conn, cfg, out_path=args.out)
    except FileExistsError:
        print(f"export failed: {args.out} already exists; choose a new output path",
              file=sys.stderr)
        sys.exit(1)
    if warning:
        print(f"warning: {warning}")
    if args.out:
        print(f"wrote {len(rows)} approved notes to {args.out}")
    else:
        print(text, end="")
    print("send these manually on LinkedIn - this tool never sends for you.")


def cmd_outcome(args):
    cfg, conn = _open(args)
    field = {"sent": "sent_at", "connected": "connected_at",
             "replied": "replied_at"}[args.event]
    ok = pipeline.set_outcome(conn, args.url, field, at=args.at, notes=args.notes)
    print("recorded." if ok else "no lead with that profile URL.")


def cmd_runs(args):
    cfg, conn = _open(args)
    for r in pipeline.list_runs(conn, limit=args.limit):
        cost = f" ${r['cost_usd']:.4f}" if r["cost_usd"] else ""
        print(f"#{r['id']} {r['kind']} {r['finished_at']} "
              f"in={r['items_in']} out={r['items_out']} "
              f"model={r['model'] or '-'}{cost} {r['notes'] or ''}")


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="lvc",
        description="LinkedIn visitor/follower conversion - manual in, human-approved out.")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--db", help="path to sqlite db (overrides config db_path)")
    p.add_argument("--version", action="version", version=f"lvc {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="write a starter config.yaml and create the db")

    pi = sub.add_parser("ingest", help="ingest leads from CSV file or paste")
    pi.add_argument("--csv", help="CSV file path (omit to paste via stdin)")

    pq = sub.add_parser("qualify", help="score new leads against your ICP (BYOK LLM)")
    pq.add_argument("--limit", type=int)

    pd = sub.add_parser("draft", help="draft notes for qualified leads (BYOK LLM)")
    pd.add_argument("--limit", type=int)

    sub.add_parser("review", help="terminal approval queue: approve/edit/reject")

    pe = sub.add_parser("export", help="export approved batch as CSV (manual sending)")
    pe.add_argument("--out", help="new output CSV path; refuses to overwrite (omit to print to stdout)")

    po = sub.add_parser("outcome", help="backfill what happened after you sent")
    po.add_argument("--url", required=True, help="lead profile URL")
    po.add_argument("--event", required=True,
                    choices=["sent", "connected", "replied"])
    po.add_argument("--at", help="ISO timestamp (default: now)")
    po.add_argument("--notes")

    pr = sub.add_parser("runs", help="list append-only run records")
    pr.add_argument("--limit", type=int, default=20)

    args = p.parse_args(argv)
    {"init": cmd_init, "ingest": cmd_ingest, "qualify": cmd_qualify,
     "draft": cmd_draft, "review": cmd_review, "export": cmd_export,
     "outcome": cmd_outcome, "runs": cmd_runs}[args.cmd](args)


if __name__ == "__main__":
    main()
