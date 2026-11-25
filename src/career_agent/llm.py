"""Swappable LLM providers behind one OpenAI-compatible interface.

Usage:
    client, model = get_llm("databricks")
    client.chat.completions.create(model=model, messages=[...])
"""
from openai import OpenAI

from .config import SECRET_SCOPE

# name -> provider settings. Model names must be verified in the Serving tab
# (Databricks) or the provider's docs; they change over time.
PROVIDERS = {
    "databricks": {"kind": "databricks", "model": "databricks-llama-4-maverick"},
    "databricks-qwen": {"kind": "databricks", "model": "databricks-qwen3-next-80b-a3b-instruct"},
    "groq": {
        "kind": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "secret_key": "groq_api_key",
        "model": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "kind": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "secret_key": "gemini_api_key",
        "model": "gemini-2.5-flash",
    },
}


def _get_secret(key: str) -> str:
    """Notebook: dbutils. Databricks App / local: env var (e.g. GROQ_API_KEY) or the Secrets API."""
    import os
    if os.getenv(key.upper()):
        return os.environ[key.upper()]
    try:
        from databricks.sdk.runtime import dbutils  # available inside notebooks
        return dbutils.secrets.get(SECRET_SCOPE, key)
    except Exception:
        import base64
        from databricks.sdk import WorkspaceClient
        value = WorkspaceClient().secrets.get_secret(SECRET_SCOPE, key).value
        return base64.b64decode(value).decode()


def get_llm(name: str = "databricks", model: str | None = None) -> tuple[OpenAI, str]:
    """Return (client, model_name) for a configured provider."""
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Options: {list(PROVIDERS)}")
    cfg = PROVIDERS[name]

    if cfg["kind"] == "databricks":
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient().serving_endpoints.get_open_ai_client()
    else:
        client = OpenAI(base_url=cfg["base_url"], api_key=_get_secret(cfg["secret_key"]))

    return client, model or cfg["model"]


def chat(name: str, messages: list[dict], model: str | None = None, **kwargs) -> str:
    """Convenience wrapper: one chat completion, returns the text."""
    client, model = get_llm(name, model)
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    content = resp.choices[0].message.content
    # Some Databricks endpoints (e.g. gpt-oss) return a list of blocks incl. reasoning.
    if isinstance(content, list):
        content = "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return content or ""
