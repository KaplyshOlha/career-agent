# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 00 · Egress test
# MAGIC Databricks Free Edition limits outbound internet on serverless compute.
# MAGIC This checks which external services the agent can reach.
# MAGIC
# MAGIC **How to read:** any HTTP status (200, 401, 404…) = network OK.
# MAGIC `FAIL ... ConnectionError/ProxyError` = domain blocked → use the GitHub Actions fallback.

# COMMAND ----------

import requests

TARGETS = {
    "DOU RSS":      "https://jobs.dou.ua/vacancies/feeds/?category=Data%20Science",
    "Djinni RSS":   "https://djinni.co/jobs/rss/",
    "GitHub raw":   "https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore",
    "PyPI":         "https://pypi.org/simple/feedparser/",
    "Telegram API": "https://api.telegram.org",
    "Groq API":     "https://api.groq.com/openai/v1/models",
    "Gemini API":   "https://generativelanguage.googleapis.com",
}

results = []
for name, url in TARGETS.items():
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        results.append((name, "OK", r.status_code, len(r.content), ""))
    except Exception as e:
        results.append((name, "FAIL", None, 0, f"{type(e).__name__}: {str(e)[:150]}"))

for name, status, code, size, err in results:
    print(f"{status:4} {name:13} status={code} bytes={size} {err}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### RSS content check
# MAGIC If DOU/Djinni returned 200, verify the feed actually parses.

# COMMAND ----------

# MAGIC %pip install feedparser -q

# COMMAND ----------

import feedparser

for name in ["DOU RSS", "Djinni RSS"]:
    feed = feedparser.parse(TARGETS[name])
    print(f"{name}: {len(feed.entries)} entries, bozo={feed.bozo}")
    for e in feed.entries[:3]:
        print("  -", e.get("title"), "|", e.get("link"))