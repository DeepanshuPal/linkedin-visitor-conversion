"""LLM access.

Providers:
- openrouter: real BYOK calls against OpenRouter's chat-completions API.
  Key comes from the env var named by config `openrouter_api_key_env`
  (default OPENROUTER_API_KEY). Never stored in config or the DB.
- mock: deterministic, offline, no key, no network. Exists so the full
  pipeline (ingest -> qualify -> draft -> review -> export) is verifiable
  end to end without spending tokens. Scores/drafts from a mock are
  clearly marked as such in their reason text.
"""
import json
import os
import urllib.request

PROMPT_VERSION_QUALIFY = "qualify-v1"
PROMPT_VERSION_DRAFT = "draft-v1"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

BUYER_KEYWORDS = [
    "founder", "co-founder", "ceo", "cto", "cmo", "coo", "vp", "head of",
    "director", "owner", "growth", "marketing", "sales", "revenue",
    "demand gen", "product marketing", "partnerships", "business development",
    "gtm", "revops",
]


class LLMError(Exception):
    pass


def _mock_chat(system, user):
    """Deterministic offline completion. Looks at the system prompt to
    decide whether this is a qualification or a drafting call."""
    if "ICP_QUALIFY" in system:
        text = user.lower()
        hits = [k for k in BUYER_KEYWORDS if k in text]
        if hits:
            score = min(95, 55 + 10 * len(hits))
            reason = f"mock: buyer-ish signals in headline/company ({', '.join(hits[:3])})"
        else:
            score = 25
            reason = "mock: no buyer keywords in headline/company"
        return {"text": json.dumps({"score": score, "reason": reason}),
                "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    if "DRAFT_NOTE" in system:
        # user payload is JSON with lead fields
        lead = json.loads(user)
        first = (lead.get("name") or "there").split()[0]
        company = lead.get("company") or "your team"
        limit = lead.get("char_limit", 300)
        body = (f"Hi {first} - noticed you stopped by our LinkedIn page. "
                f"We help teams like {company} turn page visits into warm "
                f"conversations. Happy to share the 1-page playbook if useful.")
        return {"text": body[:limit],
                "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    raise LLMError("mock provider: unrecognized prompt")


def _openrouter_chat(cfg, system, user, json_mode=False):
    key_env = cfg.get("openrouter_api_key_env", "OPENROUTER_API_KEY")
    api_key = os.environ.get(key_env)
    if not api_key:
        raise LLMError(
            f"missing API key: set ${key_env} (BYOK via OpenRouter), "
            f"or set provider: mock for an offline dry run")
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # network or API error - surface raw
        raise LLMError(f"openrouter call failed: {e}") from e
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"unexpected openrouter response: {data}") from e
    usage = data.get("usage") or {}
    return {
        "text": text,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost_usd": usage.get("cost"),
    }


def chat(cfg, system, user, json_mode=False):
    if cfg.get("provider") == "mock":
        return _mock_chat(system, user)
    return _openrouter_chat(cfg, system, user, json_mode=json_mode)


def qualify_lead(cfg, lead):
    """Score one lead 0-100 against the ICP. Returns (score, reason, usage)."""
    system = (
        "ICP_QUALIFY. You qualify LinkedIn page visitors/followers against an "
        "ideal customer profile. Reply with strict JSON: "
        '{"score": <int 0-100>, "reason": "<one line>"}. '
        "Score the likelihood this person is a buyer or strong fit, based on "
        "their headline and company only. Do not invent facts.\n"
        f"ICP: {cfg.get('icp_blurb') or '(not configured - use general B2B judgment)'}\n"
        f"Qualification criteria: {cfg.get('qualification_criteria') or '(none configured)'}"
    )
    user = json.dumps({
        "name": lead["name"], "headline": lead["headline"],
        "company": lead["company"], "profile_url": lead["profile_url"],
    })
    out = chat(cfg, system, user, json_mode=True)
    try:
        parsed = json.loads(out["text"])
        score = int(parsed["score"])
        reason = str(parsed["reason"]).strip()
    except (ValueError, KeyError, TypeError) as e:
        raise LLMError(f"could not parse qualification response: {out['text']!r}") from e
    return max(0, min(100, score)), reason, out


def draft_message(cfg, lead, examples):
    """Draft a personalized note. examples = list of (original, edited) pairs
    from past human edits, used few-shot so drafts drift toward accepted style."""
    kind = cfg.get("draft_kind", "connection_note")
    limit = int(cfg.get("connection_note_char_limit", 300))
    example_block = ""
    if examples:
        pairs = "\n".join(
            f"  before: {o}\n  after (human edit): {e}" for o, e in examples)
        example_block = (
            "\nThe human edited past drafts like this - match the 'after' style:\n"
            f"{pairs}\n")
    system = (
        "DRAFT_NOTE. You draft LinkedIn messages for a founder. Value-first, "
        "not salesy: reference why they showed up, offer something useful, no "
        "pitch, no hype, no emojis. Plain text only, no quotes around it.\n"
        f"Kind: {kind}. Hard limit: {limit} characters"
        + (" (LinkedIn connection-note limit)." if kind == "connection_note" else ".")
        + f"\nVoice notes: {cfg.get('voice_notes') or '(none configured)'}"
        + example_block
    )
    user = json.dumps({
        "name": lead["name"], "headline": lead["headline"],
        "company": lead["company"], "char_limit": limit,
    })
    out = chat(cfg, system, user)
    body = out["text"].strip().strip('"')
    truncated = False
    if kind == "connection_note" and len(body) > limit:
        body = body[: limit - 1].rstrip() + "…"
        truncated = True
    return body, out, truncated
