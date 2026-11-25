# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02 · Vacancy → tailored CV bullets
# MAGIC Selects the most relevant experience-bank items for a vacancy and rewrites them.
# MAGIC Every bullet cites its source `id`; a validator flags unknown ids and numbers absent from the source.
# MAGIC
# MAGIC Prerequisite: `01_setup.py` has created `workspace.career_agent.experience_bank`.

# COMMAND ----------

# MAGIC %pip install -q -r ../requirements.txt mlflow

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
sys.path.append(os.path.abspath("../src"))

from career_agent.llm import PROVIDERS
from career_agent.bank import read_bank
from career_agent.agent import tailor_to_vacancy, render_markdown

dbutils.widgets.dropdown("provider", "databricks", list(PROVIDERS))
dbutils.widgets.text("model", "")  # empty = provider default from llm.py

provider = dbutils.widgets.get("provider")
model = dbutils.widgets.get("model") or PROVIDERS[provider]["model"]

bank = read_bank(spark)
print(f"{len(bank)} items · provider={provider} · model={model}")

# COMMAND ----------

VACANCY = """
PASTE THE VACANCY TEXT HERE
"""

# COMMAND ----------

out = tailor_to_vacancy(VACANCY, bank, llm=provider, model=model)

import markdown as _md
displayHTML(_md.markdown(render_markdown(out, bank)))

# COMMAND ----------

# MAGIC %md ### Log run to MLflow (used for model comparison in step 3)

# COMMAND ----------

import mlflow

with mlflow.start_run(run_name=f"{provider}:{model}"):
    mlflow.log_params({"provider": provider, "model": model, "n_items": len(bank)})
    mlflow.log_metrics({
        "fit_score": float(out["result"].get("fit_score") or 0),
        "n_selected": len(out["result"].get("selected", [])),
        "n_issues": len(out["issues"]),
    })
    mlflow.log_dict(out, "output.json")
    mlflow.log_text(VACANCY, "vacancy.txt")
