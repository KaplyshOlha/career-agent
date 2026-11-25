# Career Agent on Databricks

LLM agent on **Databricks Free Edition** that:

1. **Vacancy → experience:** takes a job description, selects the most relevant items from a structured *experience bank* and rewrites them for the vacancy — every bullet cites its source item id, no invented facts.
2. **Vacancy matching:** daily collects new vacancies from DOU and Djinni (RSS), scores fit against the experience bank and preferences, and surfaces top matches.

> LinkedIn is not scraped (ToS); LinkedIn vacancies can only be pasted manually.

## Architecture

```
[vacancy text] ──► agent.ipynb ──► LLM (Databricks Serving / Groq / Gemini)
                      │ ▲
                      ▼ │
         Delta: workspace.career_agent.experience_bank
                      │
                      ▼
       tailored bullets + source ids

[daily Job] ─► DOU/Djinni RSS ─► vacancies ─► LLM scoring ─► matches ─► notification
```

## Repository layout

```
config/preferences.yaml        target roles, level, location, stop-words
data/experience_bank.sample.json   fictional sample bank (public)
src/career_agent/
    config.ipynb                  catalog/schema/volume names, paths
    llm.ipynb                     get_llm(name): swappable OpenAI-compatible providers
    bank.ipynb                    load bank (private volume → sample fallback), write Delta
    prompts.ipynb                 prompt templates + guardrails
    agent.ipynb                   vacancy → experience agent + output validator
notebooks/
    00_egress_test.ipynb          which external domains serverless can reach
    01_setup.ipynb                schema, volume, LLM endpoints check, bank → Delta
    02_tailor_cv.ipynb          paste a vacancy → tailored bullets, items to drop, gaps, CV layout
outputs/                       agent results as JSON (written by 02_tailor_cv.ipynb)
```

Logic lives in `src/`; notebooks only orchestrate, so the same code is reused by Jobs and the App.

## Privacy

The author's real experience bank is **not** in this repo. It is uploaded to a private Unity Catalog volume
(`/Volumes/workspace/career_agent/raw/experience_bank.json`). If it is absent, the code automatically
falls back to `data/experience_bank.sample.json`, so the project runs end-to-end for anyone.

## How to run

1. **Databricks Free Edition** → Workspace → Create → *Git folder* → clone this repo.
2. Run `notebooks/00_egress_test.ipynb` on Serverless compute.
3. Run `notebooks/01_setup.ipynb`: creates `workspace.career_agent` schema + `raw` volume, lists LLM endpoints,
   loads the bank into `workspace.career_agent.experience_bank`.
   If the default model is missing, update `PROVIDERS` in `src/career_agent/llm.ipynb`.
4. Open `notebooks/02_tailor_cv.ipynb`, choose provider/model in the widgets, paste a vacancy into `VACANCY`, Run all.
   Output: tailored bullets with source ids, items to remove for this vacancy, skill gaps,
   and validation warnings (unknown ids, numbers not present in the source item).
   The result is also grouped into a one-page CV layout (sections → entries → ≤2 bullets + skills)
   and saved to `outputs/<date>_<company>-<role>.json`. The layout is then filled into a Canva CV
   template (currently via Claude with the Canva connector; Canva Autofill API is on the roadmap).
5. *(Optional, for external LLMs / notifications)* create secrets in a notebook **outside** the repo:

   ```python
   from databricks.sdk import WorkspaceClient
   w = WorkspaceClient()
   w.secrets.create_scope("career-agent")
   w.secrets.put_secret("career-agent", "groq_api_key", string_value="...")
   # also: gemini_api_key, telegram_bot_token, telegram_chat_id
   ```

## Guardrails

- Prompt rules: one bullet = one source item; no new tools, metrics, numbers or outcomes; vacancy terminology only where truthful.
- `raw_text` is never sent to the model (it may contain notes the owner chose not to use).
- Post-check (`validate_output`): flags unknown ids and any number in a bullet that is absent from its source item.

## Roadmap

- [ ] 0. Egress test
- [ ] 1. Experience bank → Delta
- [ ] 2. Agent "vacancy → experience" (whole bank in prompt)
- [ ] 3. Model eval (MLflow): relevance, hallucination, sounds-like-me
- [ ] 4. RSS ingestion Job (DOU, Djinni)
- [ ] 5. Matching + notifications
- [ ] 6. Streamlit Databricks App
- [ ] 7. Vector index (AI Search)
- [ ] 8. Canva Autofill API (needs a Canva plan with Autofill)

## Constraints (Free Edition)

Serverless only · restricted outbound internet · open models only (no premium models) ·
1 vector search endpoint · 1 App · non-commercial use.
