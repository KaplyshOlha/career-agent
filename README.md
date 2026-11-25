# Career Agent on Databricks

LLM agent on **Databricks Free Edition** that:

1. **Vacancy → experience:** takes a job description, selects the most relevant items from a structured *experience bank* and rewrites them for the vacancy — every bullet cites its source item id, no invented facts.
2. **Vacancy matching:** daily collects new vacancies from DOU and Djinni (RSS), scores fit against the experience bank and preferences, and surfaces top matches.

> LinkedIn is not scraped (ToS); LinkedIn vacancies can only be pasted manually.

## Architecture

```
[vacancy text] ──► agent.py ──► LLM (Databricks Serving / Groq / Gemini)
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
    config.py                  catalog/schema/volume names, paths
    llm.py                     get_llm(name): swappable OpenAI-compatible providers
    bank.py                    load bank (private volume → sample fallback), write Delta
    prompts.py                 prompt templates + guardrails
    agent.py                   vacancy → experience agent + output validator
app.yaml                       Databricks App entry point (Streamlit)
app/
    app.py                     tabs: Paste vacancy · New matches · Settings
    db.py                      SQL warehouse access: bank, per-user preferences, matches
notebooks/
    00_egress_test.ipynb          which external domains serverless can reach
    01_setup.ipynb                schema, volume, LLM endpoints check, bank → Delta
    02_tailor_cv.ipynb            paste a vacancy → tailored bullets, items to drop, gaps, CV layout
    03_app_setup.ipynb            preferences table + grants for the app's service principal
    04_find_vacancies.ipynb       daily job: DOU/Djinni RSS → prefilter → LLM fit score → matches (+ Telegram)
config/feeds.yaml              RSS feeds to poll
scripts/fetch_feeds.py         RSS snapshot for the GitHub Action fallback
feeds/                         daily RSS snapshots (written by .github/workflows/fetch_feeds.yml)
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
   If the default model is missing, update `PROVIDERS` in `src/career_agent/llm.py`.
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


## Databricks App (Streamlit UI)

Tabs: **📝 Paste vacancy** (runs the agent, shows the report, CV layout and a JSON download for Canva) ·
**🔔 New matches** (filled by the daily job — roadmap) · **⚙️ Settings** (per-user preferences: target roles,
level, remote/cities, min salary, stop words, excluded industries, Telegram chat_id, LLM provider/model).
Each signed-in user gets their own settings row (keyed by the `X-Forwarded-Email` header) in
`workspace.career_agent.preferences`.

Deploy (Free Edition allows 1 app; it stops after 24 h — start it again before a demo):

1. **Compute → Apps → Create app → Custom app**, name `career-agent`.
2. In the app's **Resources**, add:
   - *SQL warehouse* (the Serverless Starter Warehouse), permission **Can use**, key **`sql-warehouse`**;
   - *Serving endpoint* `databricks-llama-4-maverick` (or the model you use), permission **Can query**.
3. Run `notebooks/03_app_setup.ipynb` — creates the `preferences` table and grants the app's
   service principal `USE CATALOG` + `USE SCHEMA, SELECT, MODIFY` on `workspace.career_agent`.
4. App page → **Deploy** → source code path = this Git folder (e.g. `/Workspace/Users/<you>/career-agent`).
5. Open the app URL.


## Vacancy search (daily job)

`notebooks/04_find_vacancies` — for the signed-in user (their ⚙️ Settings from the app, else
`config/preferences.yaml`):

1. **Fetch** every feed in `config/feeds.yaml` (DOU, Djinni). Fallback chain, because Free Edition
   serverless may block job sites: direct HTTP → GitHub raw snapshot → snapshot in the Git folder.
   The snapshot is made daily by the GitHub Action `fetch_feeds.yml` (also runnable by hand:
   Actions → *Fetch vacancy feeds* → *Run workflow*).
2. **Store** new vacancies in `vacancies` (dedup by URL).
3. **Prefilter** with hard rules from Settings (stop words in the title, excluded industries,
   cities / remote; Ukrainian city names understood) and a keyword relevance score
   (experience-bank skills found in the vacancy + target-role words in the title).
4. **LLM fit score** for the top N new vacancies (default 20): score 1–10, reason, matched skills,
   gaps; non-junior roles are capped at 4.
5. **Save** to `matches` (per user) → shown in the app's 🔔 New matches tab.
6. **Telegram** (optional): matches with score ≥ 7, if secret `career-agent/telegram_bot_token`
   exists and a chat_id is set in Settings.

Schedule it: open the notebook → **Schedule** → daily (e.g. 08:00).

## Guardrails

- Prompt rules: one bullet = one source item; no new tools, metrics, numbers or outcomes; vacancy terminology only where truthful.
- `raw_text` is never sent to the model (it may contain notes the owner chose not to use).
- Recruiter review (3rd LLM call): every bullet is scored 1–5 for its value for THIS vacancy; weak bullets are rewritten from the same source item or dropped (work experience is never removed completely).
- Post-check (`validate_output`): flags unknown ids, any number in a bullet that is absent from its source item, and bullets too short/long for one CV line.

## Roadmap

- [x] 0. Egress test
- [x] 1. Experience bank → Delta
- [x] 2. Agent "vacancy → experience" (whole bank in prompt) + recruiter review
- [ ] 3. Model eval (MLflow): relevance, hallucination, sounds-like-me
- [x] 4. RSS ingestion Job (DOU, Djinni) with GitHub Action fallback
- [x] 5. Matching (rules + keyword prefilter + LLM score) + Telegram notifications
- [x] 6. Streamlit Databricks App (Paste vacancy, New matches, Settings)
- [ ] 7. Vector index (AI Search)
- [ ] 8. Canva Autofill API (needs a Canva plan with Autofill)

## Constraints (Free Edition)

Serverless only · restricted outbound internet · open models only (no premium models) ·
1 vector search endpoint · 1 App · non-commercial use.
