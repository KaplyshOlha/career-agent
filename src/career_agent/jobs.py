"""Vacancy search: RSS feeds (DOU, Djinni) -> parse -> rule prefilter -> LLM fit scoring.

Pure Python (no Spark) so the same code runs in the Databricks job, the app and locally.
Fetching falls back in order: direct HTTP -> GitHub raw snapshot -> local repo snapshot,
because Databricks Free Edition serverless may block outbound access to job sites.
"""
import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone

import feedparser
import requests
import yaml

from .agent import parse_json
from .config import REPO_ROOT
from .llm import chat

FEEDS_PATH = os.path.join(REPO_ROOT, "config", "feeds.yaml")
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "feeds")
GITHUB_RAW = "https://raw.githubusercontent.com/KaplyshOlha/career-agent/main/feeds"
UA = {"User-Agent": "Mozilla/5.0 (career-agent; personal non-commercial job search)"}


# ------------------------------------------------------------------- fetching --
def load_feed_urls() -> list[tuple[str, str]]:
    """[(source, url)] from config/feeds.yaml."""
    with open(FEEDS_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return [(src, url) for src, urls in cfg.items() for url in (urls or [])]


def snapshot_name(source: str, url: str) -> str:
    """Stable file name shared with scripts/fetch_feeds.py (GitHub Action)."""
    return f"{source}_{hashlib.md5(url.encode()).hexdigest()[:10]}.xml"


def _get(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 200 and r.content.strip().startswith(b"<"):
            return r.content
    except Exception:
        pass
    return None


def fetch_feed(source: str, url: str) -> tuple[bytes | None, str]:
    """Returns (xml bytes, how) where how is 'direct' | 'github' | 'local' | 'failed'."""
    data = _get(url)
    if data:
        return data, "direct"
    name = snapshot_name(source, url)
    data = _get(f"{GITHUB_RAW}/{name}")
    if data:
        return data, "github"
    local = os.path.join(SNAPSHOT_DIR, name)
    if os.path.exists(local):
        with open(local, "rb") as f:
            return f.read(), "local"
    return None, "failed"


# -------------------------------------------------------------------- parsing --
def clean_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</li>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _split_dou_title(title: str) -> tuple[str, str, str]:
    """DOU titles look like 'Junior Data Analyst в Company, Kyiv, remote'."""
    m = re.match(r"^(.*?)\s+в\s+(.+)$", title)
    if not m:
        return title, "", ""
    role, rest = m.group(1), m.group(2)
    company, _, location = rest.partition(",")
    return role.strip(), company.strip(), location.strip()


def parse_feed(source: str, xml: bytes) -> list[dict]:
    feed = feedparser.parse(xml)
    out = []
    for e in feed.entries:
        title = clean_html(e.get("title", ""))
        company, location = "", ""
        if source == "dou":
            title, company, location = _split_dou_title(title)
        published = None
        if e.get("published_parsed"):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        out.append({
            "url": e.get("link", "").split("?")[0],
            "source": source,
            "title": title,
            "company": company,
            "location": location,
            "description": clean_html(e.get("description", ""))[:6000],
            "published": published,
        })
    return [v for v in out if v["url"]]


def fetch_all() -> tuple[list[dict], list[dict]]:
    """All vacancies from all feeds (deduplicated by url) + a fetch log."""
    seen, vacancies, log = set(), [], []
    for source, url in load_feed_urls():
        xml, how = fetch_feed(source, url)
        items = parse_feed(source, xml) if xml else []
        log.append({"source": source, "url": url, "how": how, "items": len(items)})
        for v in items:
            if v["url"] not in seen:
                seen.add(v["url"])
                vacancies.append(v)
    return vacancies, log


# ------------------------------------------------------------------ prefilter --
def _norm(s: str) -> str:
    return (s or "").lower()


CITY_ALIASES = {  # DOU writes cities in Ukrainian
    "kyiv": ["київ", "kiev"], "lviv": ["львів"], "kharkiv": ["харків"], "odesa": ["одеса", "odessa"],
    "dnipro": ["дніпро"], "ivano-frankivsk": ["івано-франківськ"], "vinnytsia": ["вінниця"],
}


def _city_variants(cities: list[str]) -> set[str]:
    out = set()
    for c in cities:
        c = c.lower().strip()
        out.add(c)
        for k, vs in CITY_ALIASES.items():
            if c == k or c in vs:
                out.update([k, *vs])
    return out


def rule_filter(v: dict, prefs: dict) -> str | None:
    """Return a rejection reason, or None if the vacancy passes the hard rules."""
    title = _norm(v["title"])
    text = _norm(" ".join([v["title"], v.get("company") or "", v["description"]]))
    for w in prefs.get("stop_words") or []:
        if re.search(rf"\b{re.escape(w.lower())}\b", title):
            return f"stop word in title: {w}"
    for ind in prefs.get("excluded_industries") or []:
        if ind and ind.lower() in text:
            return f"excluded industry: {ind}"
    cities = _city_variants(prefs.get("cities") or [])
    loc = _norm(v.get("location")) + " " + text[:600]
    remote_words = ("remote", "віддалено", "віддалена", "удаленно")
    location_known = bool(v.get("location"))
    if location_known and cities and not any(c in loc for c in cities):
        if not (prefs.get("remote", True) and any(w in loc for w in remote_words)):
            return f"location: {v.get('location')}"
    return None


def keyword_score(v: dict, bank: list[dict], prefs: dict) -> float:
    """Cheap relevance: bank skills found in the vacancy + bonus for target-role words in the title."""
    text = _norm(v["title"] + " " + v["description"])
    skills = {s.lower() for r in bank for s in (r.get("skills") or []) if isinstance(s, str) and len(s) > 2}
    hits = sum(1 for s in skills if re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", text))
    role_words = {w for r in prefs.get("target_roles") or [] for w in r.lower().split() if len(w) > 3}
    title_bonus = 3 * sum(1 for w in role_words if w in _norm(v["title"]))
    return hits + title_bonus


# -------------------------------------------------------------------- scoring --
SCORE_SYSTEM = """You are a career advisor. Score how well ONE vacancy fits the candidate.
Use only the candidate profile below; do not assume skills that are not listed.

Return ONLY valid JSON:
{"score": int 1-10, "reason": str (max 25 words, why it fits or not),
 "matched": [str] (candidate skills/experience the vacancy asks for, max 6),
 "gaps": [str] (requirements the candidate lacks, max 4),
 "level_ok": bool (seniority suits a junior/intern candidate)}

Scoring guide: 9-10 = apply today, almost all must-haves met; 7-8 = strong fit, 1-2 gaps;
5-6 = partial fit; 1-4 = wrong role, seniority or domain. If level_ok is false, score <= 4."""


def profile_text(bank: list[dict], prefs: dict) -> str:
    lines = [f"Target roles: {', '.join(prefs.get('target_roles') or [])}",
             f"Level: {', '.join(prefs.get('level') or [])}", "Experience:"]
    for r in bank:
        if r.get("type") in ("skills",):
            lines.append(f"- Skills: {r.get('action', '')}")
            continue
        skills = ", ".join((r.get("skills") or [])[:8]) if not isinstance(r.get("skills"), str) else r["skills"]
        lines.append(f"- [{r.get('type')}] {r.get('title')} ({r.get('role', '')}): {r.get('action', '')[:160]} | {skills}")
    return "\n".join(lines)


def score_vacancy(v: dict, profile: str, llm: str = "databricks", model: str | None = None) -> dict:
    res = parse_json(chat(llm, [
        {"role": "system", "content": SCORE_SYSTEM + "\n\nCANDIDATE PROFILE:\n" + profile},
        {"role": "user", "content": f"VACANCY: {v['title']} | {v.get('company', '')} | {v.get('location', '')}\n\n"
                                    f"{v['description'][:3500]}"},
    ], model=model, temperature=0.0, max_tokens=500))
    score = int(res.get("score", 0) or 0)
    if res.get("level_ok") is False:
        score = min(score, 4)
    return {"score": max(1, min(10, score)), "reason": res.get("reason", ""),
            "matched": res.get("matched", [])[:6], "gaps": res.get("gaps", [])[:4]}


# --------------------------------------------------------------- notification --
def telegram_message(matches: list[dict]) -> str:
    lines = ["🧭 New vacancy matches"]
    for m in matches:
        lines.append(f"\n<b>{html.escape(m['title'])}</b> — {html.escape(m.get('company') or m['source'])} "
                     f"· {m['score']}/10\n{html.escape(m['reason'])}\n{m['url']}")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        return r.ok
    except Exception:
        return False


def as_json(x) -> str:
    return json.dumps(x, ensure_ascii=False)
