"""Config loading: single yaml file, sane defaults, env var for secrets."""
import os

import yaml

DEFAULTS = {
    # ICP + voice - fill these in config.yaml, they drive scoring and drafting
    "icp_blurb": "",
    "qualification_criteria": "",
    "voice_notes": "",
    # LLM (BYOK via OpenRouter). provider: openrouter | mock
    "provider": "openrouter",
    "model": "openai/gpt-4o-mini",
    "openrouter_api_key_env": "OPENROUTER_API_KEY",
    # Scoring
    "qualification_threshold": 60,
    # Drafting
    "draft_kind": "connection_note",   # connection_note | followup
    "connection_note_char_limit": 300,  # LinkedIn connection note limit
    "few_shot_examples": 3,
    # Rate cap: max new connection notes approved per day (protects the account)
    "max_new_notes_per_day": 20,
    # Storage
    "db_path": "lvc.db",
}


def load_config(path=None):
    path = path or os.environ.get("LVC_CONFIG", "config.yaml")
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for k, v in loaded.items():
            if v is not None:
                cfg[k] = v
    cfg["_config_path"] = path
    return cfg
