# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · One-time setup
# MAGIC 1. Create schema + volume in Unity Catalog
# MAGIC 2. List available LLM serving endpoints and smoke-test one
# MAGIC 3. Load the experience bank into a Delta table
# MAGIC
# MAGIC Secrets (Groq/Gemini/Telegram keys) are created in a separate notebook **outside** this repo — see README.

# COMMAND ----------

# MAGIC %pip install -q -r ../requirements.txt

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
sys.path.append(os.path.abspath("../src"))
%load_ext autoreload
%autoreload 2

from career_agent import config

# COMMAND ----------

# MAGIC %md ## 1. Schema + volume

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {config.CATALOG}.{config.SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {config.CATALOG}.{config.SCHEMA}.{config.VOLUME}")
print("Volume path:", config.VOLUME_PATH)
display(spark.sql(f"SHOW VOLUMES IN {config.CATALOG}.{config.SCHEMA}"))

# COMMAND ----------

# MAGIC %md ## 2. Available LLM endpoints

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
endpoints = [ep.name for ep in w.serving_endpoints.list()]
llm_like = [n for n in endpoints if any(k in n for k in ("llama", "qwen", "gpt", "gemma", "claude", "mixtral"))]
print("LLM-like endpoints:", *llm_like, sep="\n  ")

# COMMAND ----------

from career_agent.llm import chat, PROVIDERS

model = PROVIDERS["databricks"]["model"]
if model not in endpoints:
    print(f"⚠️ '{model}' not found — update PROVIDERS in src/career_agent/llm.py with a name from the list above")
else:
    print(chat("databricks", [{"role": "user", "content": "Reply with one word: ready"}], max_tokens=5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Experience bank → Delta
# MAGIC Upload your real `experience_bank.json` to the volume first
# MAGIC (Catalog → workspace → career_agent → raw → *Upload to this volume*).
# MAGIC Without it, the public sample from `data/` is used.

# COMMAND ----------

from career_agent.bank import load_bank_json, write_bank_table

items = load_bank_json()
tbl = write_bank_table(spark, items)
display(spark.table(tbl).select("id", "type", "title", "role", "skills"))
