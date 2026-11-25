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
PwC is a global network of more than 370,000 professionals in 149 countries that turns challenges into opportunities. We create innovative solutions in audit, consulting, tax and technology, combining knowledge from all over the world.

PwC SDC Lviv, opened in 2018, is part of this global space. It is a place where technology is combined with team spirit, and ambitious ideas find their embodiment in real projects for Central and Eastern Europe.

What do we guarantee?

Work format: Remote or in a comfortable office in Lviv - you choose. 
Development: Personal development plan, mentoring, English and Polish language courses. 
Stability: Official employment from day one, annual review of salary and career prospects. 
Corporate culture: Events that unite the team and a space where everyone can be themselves. 

We are looking for a Data Analyst to join our data automation team. Our core services currently rely on Alteryx and other low-code solutions, while we are also exploring Databricks as an alternative platform for selected use cases.

The role combines hands-on work with existing data solutions and the opportunity to contribute to the evaluation and potential migration of workflows from Alteryx to Databricks.

Responsibilities:

Work with data processing, transformation, and automation solutions;
Develop and maintain data workflows and notebooks
Work with databases using SQL and Python;
Analyze existing Alteryx workflows and assess opportunities to implement equivalent solutions in Databricks;
Compare Alteryx and Databricks approaches for different use cases;
Support the migration of suitable workflows from Alteryx to Databricks;
Validate migrated solutions and ensure data quality and expected results;
Support, troubleshoot, and improve existing data solutions;
Learn and work with Alteryx and other low-code platforms as part of the team’s core services. 

Requirements:

Basic knowledge of SQL;
Knowledge of Python at any level;
Knowledge of Databricks;
Hands-on Databricks experience from university, courses, personal projects, or commercial work;
Strong analytical and problem-solving skills;
English: Intermediate (B1+) or higher;
Willingness to learn new technologies and work with different data and automation platforms. 

Nice To Have:

Knowledge or practical experience with Alteryx. Previous experience is an advantage but not required, as training will be provided;
Experience with data transformation or data pipelines;
Experience working with larger datasets. 
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