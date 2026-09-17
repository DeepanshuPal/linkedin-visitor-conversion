# lvc - LinkedIn Visitor Conversion

[Marketerloop](https://github.com/DeepanshuPal/marketerloop) template #3: turn
your LinkedIn page visitors and followers into a qualified, human-approved
outreach queue. CLI-first, BYOK, local-first.

**Manual in, human-approved out.** This tool never scrapes LinkedIn and never
sends anything for you.

## Read this first: what LinkedIn actually gives you

There is no legitimate automated way to get your page visitors out of LinkedIn:

- LinkedIn shows **identifiable recent page visitors only to Premium Company
  Page admins**, and only for members whose privacy settings allow it.
- **Anonymous visitors are unresolvable.** Anyone browsing in private mode
  shows up as a demographic aggregate, not a person.
- Scraping visitor data violates LinkedIn's terms and gets accounts restricted.
  This tool does not do it, and there is no clever workaround built in.

So the input to v0 is **manual**: you export or copy the visitor/follower data
yourself (Premium Company Page visitor list, your followers list, or any CSV
you already have), and feed it in as a CSV or a paste. The tool takes over from
there: dedupe, qualify, draft, collect your approval, and hand you a clean
batch to send by hand.

Equally important on the output side: **no automated sending, no browser
automation.** Bulk automated connection requests are the fastest way to lose a
LinkedIn account. The tool produces a CSV of approved notes; you send them
yourself, at human pace, inside the daily cap you configure.

## The pipeline

```
CSV/paste (name, headline, company, profile URL, visit date)
  -> normalize + dedupe on profile URL
  -> ICP qualification 0-100 with a one-line reason (BYOK LLM via OpenRouter)
  -> personalized connection note / follow-up draft (value-first, <=300 chars)
  -> terminal approval queue: approve / edit / reject
       (edits are stored with diffs and fed back as few-shot examples,
        so drafts drift toward the style you actually approve)
  -> export approved batch as CSV for manual sending
  -> backfill outcomes later (sent? connected? replied?)
```

Every stage appends a run record: inputs, model and prompt versions, item
counts, token usage and cost. Nothing is silently overwritten.

## Install

```bash
pip install -r requirements.txt
pip install .
# or without installing: PYTHONPATH=src python -m lvc ...
```

Docker is optional convenience, not a requirement:

```bash
echo "OPENROUTER_API_KEY=sk-or-..." > .env
docker compose run --rm lvc --help
```

## Quickstart (offline, no API key)

The `mock` provider is deterministic and free - it exists so you can verify
the whole pipeline before spending a token:

```bash
lvc init                      # writes config.yaml, creates lvc.db
# edit config.yaml: set `provider: mock` for the dry run
lvc ingest --csv samples/visitors.sample.csv
lvc qualify
lvc draft
lvc review                    # approve/edit/reject each note
lvc export --out approved.csv
lvc runs                      # the append-only audit trail
```

Then flip `provider: openrouter`, fill in `icp_blurb`,
`qualification_criteria` and `voice_notes`, set `OPENROUTER_API_KEY`, and run
the same commands on your real export.

## Input format

CSV with a header row. Only a profile URL column is strictly required; column
names are matched loosely (`url`, `profile`, `linkedin_url`, ... all work).

```csv
name,headline,company,profile_url,visit_date
Jane Example,VP Growth at ExampleCo,ExampleCo,https://www.linkedin.com/in/jane-example,2026-09-10
```

Paste mode: `lvc ingest` with no `--csv` reads the same CSV from stdin.

## Config (single yaml)

See `config.example.yaml`. The fields that matter:

| key | what it does |
| --- | --- |
| `icp_blurb` | who buys from you - drives qualification |
| `qualification_criteria` | extra scoring rules |
| `voice_notes` | your writing style for drafts |
| `provider` / `model` | `openrouter` (BYOK) or `mock` (offline) |
| `qualification_threshold` | score >= this moves a lead to drafting |
| `draft_kind` | `connection_note` (300-char limit) or `followup` |
| `max_new_notes_per_day` | **rate cap** - the queue stops approving past this per day |
| `few_shot_examples` | how many of your past edits shape new drafts |

Your OpenRouter key stays in an environment variable
(`OPENROUTER_API_KEY` by default). It is never written to config or the
database.

## Schema (SQLite, day one)

- `leads` - one row per person, deduped on normalized profile URL; carries
  score, reason and status (`new -> qualified -> drafted -> approved -> exported`).
- `drafts` - every drafted note with model + prompt version and review state.
- `edits` - your edits with unified diffs; the few-shot source for future drafts.
- `outcomes` - backfill table: `sent_at`, `connected_at`, `replied_at`, notes.
- `runs` - **append-only** audit: kind, inputs, model, prompt version, item
  counts, token usage, cost. Rows are only ever inserted.

## Rate caps and account safety

`max_new_notes_per_day` caps how many notes the approval queue will let you
approve in a day, and export warns/refuses beyond it. LinkedIn's own limits
for new connection requests are roughly 100-200/week and they tighten without
warning - keep the cap conservative. Sending is manual, at your pace.

## Honesty box: verified vs stubbed

Verified end to end in this repo's test suite and demo run, with the **mock**
provider: ingest, dedupe, qualify, draft, approve/edit/reject, edit diffs,
few-shot reuse, export, outcome backfill, run records, persistence.

Stubbed / unverified:

- **OpenRouter live calls** - real code path, but untested in this build
  (no API key available at build time). First real run may need prompt tweaks;
  the runs table records model + prompt version so you can see what changed.
- **LinkedIn-side export instructions** - the exact Premium Company Page
  visitor export UX changes often; expect to copy-paste rather than download
  a CSV.

## Upgrade path: enrichment before scoring

v0 scores on the headline text you paste in. The named upgrade is an
enrichment step in front of qualification: resolve each profile URL to public
company and role context with **Exa** or **Firecrawl**, then qualify on that
evidence instead of a one-line headline. It slots in as a stage between
ingest and qualify, costs cents per lead, and stays optional - the manual CSV
path remains the verified default. Not implemented in v0.1; the `runs` table
already records the evidence window each decision used, so nothing about the
audit trail changes when it lands.

## License

MIT - see [LICENSE](LICENSE).
