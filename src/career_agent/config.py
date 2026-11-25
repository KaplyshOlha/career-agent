"""Central project settings (Unity Catalog locations, secret scope)."""
import os

import yaml

CATALOG = "workspace"          # default catalog on Databricks Free Edition
SCHEMA = "career_agent"
VOLUME = "raw"

VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
REAL_BANK_PATH = f"{VOLUME_PATH}/experience_bank.json"   # private, uploaded manually

SECRET_SCOPE = "career-agent"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE_BANK_PATH = os.path.join(REPO_ROOT, "data", "experience_bank.sample.json")
PREFERENCES_PATH = os.path.join(REPO_ROOT, "config", "preferences.yaml")


def table(name: str) -> str:
    """Fully qualified table name, e.g. table('experience_bank')."""
    return f"{CATALOG}.{SCHEMA}.{name}"


def load_preferences() -> dict:
    with open(PREFERENCES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def default_prefs() -> dict:
    """config/preferences.yaml flattened to the schema used by the app's Settings tab."""
    try:
        y = load_preferences() or {}
    except Exception:
        y = {}
    loc = y.get("location") or {}
    return {
        "target_roles": y.get("target_roles") or [], "level": y.get("level") or [],
        "remote": loc.get("remote", True), "cities": loc.get("cities") or [],
        "languages": y.get("languages") or [], "stop_words": y.get("stop_words") or [],
        "excluded_industries": y.get("excluded_industries") or [],
        "min_salary_usd": y.get("min_salary_usd"), "telegram_chat_id": "",
        "llm_provider": "databricks", "llm_model": "",
    }
