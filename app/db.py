"""SQL access for the Databricks App (runs as the app's service principal via a SQL warehouse)."""
import json
import os
import sys

from databricks import sql
from databricks.sdk.core import Config

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from career_agent.config import load_preferences, table  # noqa: E402

BANK_TABLE = table("experience_bank")
PREFS_TABLE = table("preferences")
MATCHES_TABLE = table("matches")


def _connect():
    cfg = Config()  # picks up the app's service-principal credentials from env
    warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
    return sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        credentials_provider=lambda: cfg.authenticate,
    )


def query(statement: str, params: dict | None = None) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(statement, params or {})
        if cur.description is None:
            return []
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_bank() -> list[dict]:
    rows = query(f"SELECT * FROM {BANK_TABLE}")
    for r in rows:  # ARRAY columns may arrive as numpy arrays / strings
        if r.get("skills") is not None and not isinstance(r["skills"], (list, str)):
            r["skills"] = list(r["skills"])
    return rows


# ---------------------------------------------------------------- preferences --
DEFAULT_PREFS = {
    "target_roles": [], "level": [], "remote": True, "cities": [], "min_salary_usd": None,
    "stop_words": [], "excluded_industries": [], "languages": [],
    "telegram_chat_id": "", "llm_provider": "databricks", "llm_model": "",
}


def default_prefs() -> dict:
    """Defaults from config/preferences.yaml (repo), flattened to the app's schema."""
    try:
        y = load_preferences() or {}
    except Exception:
        y = {}
    loc = y.get("location") or {}
    return {**DEFAULT_PREFS,
            "target_roles": y.get("target_roles") or [],
            "level": y.get("level") or [],
            "remote": loc.get("remote", True),
            "cities": loc.get("cities") or [],
            "languages": y.get("languages") or [],
            "stop_words": y.get("stop_words") or [],
            "excluded_industries": y.get("excluded_industries") or [],
            "min_salary_usd": y.get("min_salary_usd")}


def get_prefs(user_email: str) -> dict:
    rows = query(f"SELECT prefs FROM {PREFS_TABLE} WHERE user_email = :email", {"email": user_email})
    if not rows:
        return default_prefs()
    return {**DEFAULT_PREFS, **json.loads(rows[0]["prefs"])}


def save_prefs(user_email: str, prefs: dict) -> None:
    query(
        f"""MERGE INTO {PREFS_TABLE} t
            USING (SELECT :email AS user_email, :prefs AS prefs) s
            ON t.user_email = s.user_email
            WHEN MATCHED THEN UPDATE SET prefs = s.prefs, updated_at = current_timestamp()
            WHEN NOT MATCHED THEN INSERT (user_email, prefs, updated_at)
                                  VALUES (s.user_email, s.prefs, current_timestamp())""",
        {"email": user_email, "prefs": json.dumps(prefs, ensure_ascii=False)},
    )


# -------------------------------------------------------------------- matches --
def load_matches(limit: int = 50) -> list[dict] | None:
    """Top vacancy matches (filled by the daily job, roadmap step 5). None if the table doesn't exist yet."""
    try:
        return query(f"SELECT * FROM {MATCHES_TABLE} ORDER BY score DESC LIMIT {int(limit)}")
    except Exception:
        return None
