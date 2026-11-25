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
