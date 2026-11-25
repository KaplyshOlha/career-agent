"""Career Agent — Databricks App (Streamlit).

Tabs:
  • Paste vacancy — tailor the experience bank to a vacancy (same agent as notebooks/02_tailor_cv)
  • New matches   — vacancies scored by the daily job (roadmap)
  • Settings      — per-user job-search preferences, stored in workspace.career_agent.preferences
"""
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import db  # noqa: E402
from career_agent.agent import build_cv_layout, render_cv_text, render_markdown, tailor_to_vacancy  # noqa: E402
from career_agent.llm import PROVIDERS  # noqa: E402

st.set_page_config(page_title="Career Agent", page_icon="🧭", layout="wide")


def current_user() -> str:
    """Databricks Apps put the signed-in user's email into this header."""
    try:
        return st.context.headers.get("X-Forwarded-Email") or "local-user"
    except Exception:
        return "local-user"


@st.cache_data(ttl=300, show_spinner=False)
def cached_bank() -> list[dict]:
    return db.load_bank()


def lines(text: str) -> list[str]:
    """Text area / comma input -> clean list."""
    return [x.strip() for x in text.replace(",", "\n").splitlines() if x.strip()]


user = current_user()
if "prefs" not in st.session_state:
    try:
        st.session_state.prefs = db.get_prefs(user)
    except Exception as e:  # table missing / no permissions -> still let the user work
        st.session_state.prefs = db.default_prefs()
        st.session_state.prefs_error = str(e)
prefs = st.session_state.prefs

st.title("🧭 Career Agent")
st.caption(f"Signed in as **{user}** · experience bank: `{db.BANK_TABLE}`")

tab_vac, tab_matches, tab_settings = st.tabs(["📝 Paste vacancy", "🔔 New matches", "⚙️ Settings"])

# ------------------------------------------------------------------ Paste vacancy --
with tab_vac:
    vacancy = st.text_area("Vacancy text", height=260,
                           placeholder="Paste the full job description (any language)…")
    provider = prefs.get("llm_provider") or "databricks"
    model = prefs.get("llm_model") or PROVIDERS.get(provider, PROVIDERS["databricks"])["model"]
    st.caption(f"Model: `{provider}` / `{model}` · change it in ⚙️ Settings")

    if st.button("Tailor my CV", type="primary", disabled=not vacancy.strip()):
        try:
            with st.spinner("Loading experience bank…"):
                bank = cached_bank()
            with st.spinner("Agent at work: requirements → selection → recruiter review (≈30–60 s)…"):
                out = tailor_to_vacancy(vacancy, bank, llm=provider, model=model)
                layout = build_cv_layout(out, bank, vacancy)
            st.session_state.last = {"out": out, "layout": layout, "bank": bank, "vacancy": vacancy}
        except Exception as e:
            st.error(f"Agent failed: {e}")

    last = st.session_state.get("last")
    if last:
        out, layout, bank = last["out"], last["layout"], last["bank"]
        res = out["result"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Fit score", f"{res.get('fit_score', '?')}/10")
        c2.metric("Bullets", sum(len(e["bullets"]) for es in layout["sections"].values() for e in es))
        c3.metric("Validation issues", len(out["issues"]))

        left, right = st.columns([3, 2])
        with left:
            st.markdown(render_markdown(out, bank))
        with right:
            st.subheader("CV layout")
            st.caption("Same order and shape as the Canva template")
            st.code(render_cv_text(layout), language=None)
            req = out["requirements"]
            fname = f"{req.get('company', 'vacancy')}-{req.get('title', '')}".strip("-").replace(" ", "_")
            st.download_button(
                "⬇️ Download JSON (for Canva)",
                data=json.dumps({"vacancy": last["vacancy"], "agent_output": out, "cv_layout": layout},
                                ensure_ascii=False, indent=2),
                file_name=f"{fname}.json", mime="application/json")

# -------------------------------------------------------------------- New matches --
with tab_matches:
    matches = db.load_matches()
    if matches is None:
        st.info("The daily DOU / Djinni matching job is the next build step. "
                "Once it runs, scored vacancies appear here, filtered by your ⚙️ Settings.")
    elif not matches:
        st.write("No matches yet.")
    else:
        st.dataframe(matches, use_container_width=True)

# ----------------------------------------------------------------------- Settings --
with tab_settings:
    st.subheader("Job-search preferences")
    st.caption("Saved per user. Used to filter and score vacancies and to pick the model for the agent.")
    if st.session_state.get("prefs_error"):
        st.warning("Could not load saved settings (showing defaults). "
                   "Run `notebooks/03_app_setup` to create the table and grant access.\n\n"
                   f"`{st.session_state.prefs_error[:300]}`")

    with st.form("settings"):
        c1, c2 = st.columns(2)
        with c1:
            roles = st.text_area("Target roles (one per line)", "\n".join(prefs["target_roles"]), height=120)
            level = st.multiselect("Level", ["intern", "junior", "middle", "senior"],
                                   default=[x for x in prefs["level"] if x in ["intern", "junior", "middle", "senior"]])
            remote = st.checkbox("Remote OK", value=bool(prefs["remote"]))
            cities = st.text_input("Cities (comma-separated)", ", ".join(prefs["cities"]))
            salary = st.number_input("Minimum salary, USD/month (0 = don't filter)", min_value=0, step=100,
                                     value=int(prefs.get("min_salary_usd") or 0))
        with c2:
            stop = st.text_area("Stop words in title (one per line)", "\n".join(prefs["stop_words"]), height=120)
            excl = st.text_input("Excluded industries (comma-separated)", ", ".join(prefs["excluded_industries"]))
            tg = st.text_input("Telegram chat_id for notifications (optional)", prefs.get("telegram_chat_id", ""),
                               help="Message your bot, then open api.telegram.org/bot<TOKEN>/getUpdates to see it.")
            prov_options = list(PROVIDERS)
            prov = st.selectbox("LLM provider", prov_options,
                                index=prov_options.index(prefs["llm_provider"]) if prefs["llm_provider"] in prov_options else 0)
            mdl = st.text_input("Model / endpoint (empty = provider default)", prefs.get("llm_model", ""),
                                help="Databricks: serving endpoint name from the Serving tab.")
        saved = st.form_submit_button("💾 Save settings", type="primary")

    if saved:
        new = {**prefs,
               "target_roles": lines(roles), "level": level, "remote": remote, "cities": lines(cities),
               "min_salary_usd": int(salary) or None, "stop_words": lines(stop),
               "excluded_industries": lines(excl), "telegram_chat_id": tg.strip(),
               "llm_provider": prov, "llm_model": mdl.strip()}
        try:
            db.save_prefs(user, new)
            st.session_state.prefs = new
            st.session_state.pop("prefs_error", None)
            st.success("Saved ✅")
        except Exception as e:
            st.error(f"Could not save: {e}")
