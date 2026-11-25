"""Vacancy -> experience agent: extract requirements, select & rewrite bank items, validate."""
import json
import re

from .llm import chat
from .prompts import EXTRACT_SYSTEM, REVIEW_SYSTEM, TAILOR_SYSTEM

# raw_text is intentionally NOT sent to the model: it may hold notes or numbers
# the owner decided not to use in a CV.
PROMPT_FIELDS = ["context", "action", "result_metric"]


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [s.strip() for s in v.split(",") if s.strip()]
    return list(v)


def format_item(r: dict) -> str:
    lines = [f"[{r['id']}] ({r.get('type', '')}) {r.get('title', '')} | "
             f"{r.get('role', '')} @ {r.get('company', '')} | {r.get('period', '')}"]
    lines += [f"  {f}: {r[f]}" for f in PROMPT_FIELDS if r.get(f)]
    skills = _as_list(r.get("skills"))
    if skills:
        lines.append(f"  skills: {', '.join(skills)}")
    return "\n".join(lines)


def format_bank(bank: list[dict]) -> str:
    return "\n\n".join(format_item(r) for r in bank)


def parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # Qwen3-style reasoning
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON in model output:\n{text[:500]}")
    return json.loads(text[start:end + 1])


MAX_BULLET_CHARS = 110   # one CV line at the template's font size
MIN_BULLET_CHARS = 70    # shorter bullets leave half the line empty
MIN_REVIEW_VALUE = 3     # bullets the recruiter review scores below this are removed...
MIN_TOTAL_BULLETS = 10   # ...but never below this many (a one-page CV needs ~10-13 bullets)
NON_BULLET_TYPES = {"skills", "education"}
_ID_TAG = re.compile(r"\s*[\(\[]\s*#?[A-Z]{2,5}-\d{1,3}\s*[\)\]]")

_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*([KkMm])?(?![\w])|(\d+(?:\.\d+)?)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


_WORD_NUMS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}


def _numbers(s: str) -> set[float]:
    """Numbers in text; '566,414' -> 566414, '441K' -> 441000, 'three' -> 3."""
    out = {float(v) for w, v in _WORD_NUMS.items() if re.search(rf"\b{w}\b", (s or "").lower())}
    for num, suffix, plain in _NUM.findall(_THOUSANDS.sub("", s or "")):
        v = float(num or plain)
        if suffix:
            v *= 1_000 if suffix.lower() == "k" else 1_000_000
        out.add(v)
    return out


def _unsupported_numbers(bullet: str, source: str) -> list[float]:
    """Numbers in the bullet that are not in the source (rounding like 566,414 -> 566K is allowed)."""
    src = _numbers(source)
    bad = []
    for v in _numbers(bullet):
        if v in src:
            continue
        if v >= 1000 and any(abs(n - v) / v < 0.01 for n in src):  # rounded abbreviation
            continue
        bad.append(v)
    return sorted(bad)


def validate_output(result: dict, bank_by_id: dict) -> list[str]:
    """Cheap hallucination guard: unknown ids + numbers not present in the source item."""
    issues = []
    for item in result.get("selected", []):
        rid = item.get("id")
        if rid not in bank_by_id:
            issues.append(f"Unknown id {rid!r}: {item.get('bullet')}")
            continue
        src = bank_by_id[rid]
        src_text = " ".join(str(src.get(k, "")) for k in ["title", "company", "period", *PROMPT_FIELDS])
        extra = _unsupported_numbers(item.get("bullet", ""), src_text)
        if extra:
            issues.append(f"[{rid}] numbers not in source: {sorted(extra)} -> {item['bullet']}")
        n = len(item.get("bullet", ""))
        if n > MAX_BULLET_CHARS:
            issues.append(f"[{rid}] bullet is {n} chars, longer than {MAX_BULLET_CHARS} (won't fit one CV line)")
        elif n < MIN_BULLET_CHARS:
            issues.append(f"[{rid}] bullet is {n} chars, shorter than {MIN_BULLET_CHARS} (leaves the line half empty)")
    return issues


def tailor_to_vacancy(vacancy_text: str, bank: list[dict], llm: str = "databricks",
                      model: str | None = None, review: bool = True) -> dict:
    """Select the most relevant bank items and rewrite them for the vacancy.

    Returns: {"requirements": {...}, "result": {"selected": [...], "drop": [...], "gaps": [...], ...},
              "issues": [...]}
    """
    bank_by_id = {r["id"]: r for r in bank}

    req = parse_json(chat(llm, [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": vacancy_text},
    ], model=model, temperature=0.0, max_tokens=1500))

    result = parse_json(chat(llm, [
        {"role": "system", "content": TAILOR_SYSTEM},
        {"role": "user", "content": (
            f"VACANCY REQUIREMENTS (JSON):\n{json.dumps(req, ensure_ascii=False)}\n\n"
            f"VACANCY TEXT:\n{vacancy_text}\n\n"
            f"EXPERIENCE BANK:\n{format_bank(bank)}"
        )},
    ], model=model, temperature=0.2, max_tokens=4000))

    result["selected"] = _clean(result.get("selected", []), bank_by_id)

    # Step 3: recruiter review - score each bullet's value for THIS vacancy, rewrite or drop weak ones.
    if review:
        result["selected"], result["review"] = review_bullets(
            vacancy_text, req, result["selected"], bank_by_id, llm=llm, model=model)

    result["selected"] = sorted(result["selected"], key=lambda x: -int(x.get("relevance", 0) or 0))
    return {"requirements": req, "result": result, "issues": validate_output(result, bank_by_id)}


def _clean(selected: list[dict], bank_by_id: dict) -> list[dict]:
    """Strip id tags the model may put into bullet text; drop non-CV item types."""
    out = []
    for it in selected:
        it["bullet"] = _ID_TAG.sub("", it.get("bullet", "")).strip().rstrip(".")
        if bank_by_id.get(it.get("id"), {}).get("type") not in NON_BULLET_TYPES and it["bullet"]:
            out.append(it)
    return out


def _accept_rewrite(draft: str, new: str) -> bool:
    """A rewrite must still fill one CV line and keep every number (fact) of the draft."""
    return (MIN_BULLET_CHARS + 5 <= len(new) <= MAX_BULLET_CHARS
            and _numbers(draft) <= _numbers(new))


def review_bullets(vacancy_text: str, req: dict, selected: list[dict], bank_by_id: dict,
                   llm: str = "databricks", model: str | None = None) -> tuple[list[dict], list[dict]]:
    """LLM-as-recruiter pass. Returns (final bullets, review log)."""
    drafts = "\n\n".join(
        f"#{i} id={s['id']}\nBULLET: {s['bullet']}\nSOURCE ITEM:\n{format_item(bank_by_id[s['id']])}"
        for i, s in enumerate(selected) if s.get("id") in bank_by_id
    )
    try:
        rev = parse_json(chat(llm, [
            {"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": (
                f"VACANCY REQUIREMENTS (JSON):\n{json.dumps(req, ensure_ascii=False)}\n\n"
                f"VACANCY TEXT:\n{vacancy_text}\n\nDRAFT BULLETS:\n{drafts}"
            )},
        ], model=model, temperature=0.1, max_tokens=3000))
    except Exception as e:  # review is an improvement step; never lose the draft because of it
        return selected, [{"error": f"review failed: {e}"}]

    dropped: list[dict] = []
    by_index = {int(r.get("index", -1)): r for r in rev.get("bullets", []) if str(r.get("index", "")).lstrip("-").isdigit()}
    final, log = [], []
    for i, s in enumerate(selected):
        r = by_index.get(i)
        if r is None:  # reviewer skipped it -> keep the draft
            final.append(s)
            continue
        value = int(r.get("value", 0) or 0)
        log.append({"id": s["id"], "draft": s["bullet"], "value": value, "action": r.get("action"),
                    "final": r.get("bullet"), "reason": r.get("reason")})
        if r.get("action") == "drop" or value < MIN_REVIEW_VALUE:
            dropped.append({**s, "value": value})
            continue
        if r.get("action") == "rewrite" and r.get("bullet"):
            new = _ID_TAG.sub("", r["bullet"]).strip().rstrip(".")
            if _accept_rewrite(s["bullet"], new):
                s = {**s, "bullet": new, "rewritten": True}
            else:
                log[-1]["action"] = "rewrite_rejected"
        final.append({**s, "value": value})

    # The reviewer may not shrink the CV below MIN_TOTAL_BULLETS: restore the best dropped drafts.
    def shown(bullets):  # bullets that will actually appear in the CV (max 2 per entry)
        per_entry: dict = {}
        for b in bullets:
            t = bank_by_id[b["id"]].get("title")
            per_entry[t] = per_entry.get(t, 0) + 1
        return sum(min(2, n) for n in per_entry.values())

    for d in sorted(dropped, key=lambda x: (-x["value"], -int(x.get("relevance", 0) or 0))):
        if shown(final) >= MIN_TOTAL_BULLETS:
            break
        final.append({**d, "restored": True})

    # Safety net: work experience is never removed completely.
    work_ids = {s["id"] for s in selected if bank_by_id[s["id"]].get("type") == "work"}
    if work_ids and not any(s["id"] in work_ids for s in final):
        best = max((s for s in selected if s["id"] in work_ids), key=lambda x: int(x.get("relevance", 0) or 0))
        final.append(best)
    return final, log


def render_markdown(out: dict, bank: list[dict]) -> str:
    bank_by_id = {r["id"]: r for r in bank}
    req, res = out["requirements"], out["result"]
    md = [f"## {req.get('title', 'Vacancy')} — {req.get('company', '')}",
          f"**Fit: {res.get('fit_score', '?')}/10.** {res.get('fit_summary', '')}", "",
          "### Tailored bullets"]
    for s in res.get("selected", []):
        src = bank_by_id.get(s.get("id"), {})
        md.append(f"- {s.get('bullet')}  \n  _[{s.get('id')}] {src.get('title', '?')} · "
                  f"relevance {s.get('relevance')}/5 · recruiter value {s.get('value', '–')}/5{' · rewritten' if s.get('rewritten') else ''} · covers: {', '.join(s.get('matched_requirements', []))}_")
    if res.get("drop"):
        md += ["", "### Remove for this vacancy"] + [f"- [{d.get('id')}] {d.get('reason')}" for d in res["drop"]]
    if res.get("gaps"):
        md += ["", "### Gaps (not in the bank)"] + \
              [f"- **{g.get('requirement')}** — {g.get('note', '')}" for g in res["gaps"]]
    changed = [r for r in res.get("review", []) if r.get("action") in ("rewrite", "rewrite_rejected", "drop") or "error" in r]
    if changed:
        md += ["", "### Recruiter review (changed bullets)"]
        for r in changed:
            if "error" in r:
                md.append(f"- ⚠️ {r['error']}")
            elif r["action"] == "rewrite_rejected":
                md.append(f"- ↩️ [{r['id']}] rewrite rejected (shorter or lost facts), draft kept: {r['draft']}")
            elif r["action"] == "drop":
                md.append(f"- ❌ [{r['id']}] value {r['value']}/5 — {r.get('reason')}: ~~{r['draft']}~~")
            else:
                md.append(f"- ✏️ [{r['id']}] {r['draft']} → **{r['final']}** ({r.get('reason')})")
    if out["issues"]:
        md += ["", "### ⚠️ Validation issues"] + [f"- {i}" for i in out["issues"]]
    return "\n".join(md)


# ---------------------------------------------------------------- CV layout --
# Maps bank item types to CV sections (matches the one-page Canva template).
SECTION_BY_TYPE = {
    "work": "EXPERIENCE",
    "project": "PROJECTS", "competition": "PROJECTS", "case": "PROJECTS",
    "leadership": "VOLUNTEERING", "volunteering": "VOLUNTEERING",
}
SECTION_ORDER = ["EXPERIENCE", "PROJECTS", "VOLUNTEERING"]
MAX_SKILLS = 6


MAX_SKILLS_LINE_CHARS = 100  # "Skills: a | b | c" must fit one CV line


def _fit_skills(skills: list[str]) -> list[str]:
    """Greedy: keep skills in priority order, skip ones that would overflow the line."""
    out = []
    for sk in skills:
        if len(out) >= MAX_SKILLS:
            break
        if len("Skills: " + " | ".join(out + [sk])) <= MAX_SKILLS_LINE_CHARS:
            out.append(sk)
    return out


def _entry_header(item: dict) -> str:
    if item.get("type") == "work":
        return f"{item.get('role', '')} | {item.get('title', '')}"
    return item.get("title", "")


def build_cv_layout(out: dict, bank: list[dict], vacancy_text: str = "") -> dict:
    """Group selected bullets into CV entries (by item title) and sections.

    Skills per entry come only from the cited items' own skill lists; skills mentioned
    in the vacancy are ranked first.
    """
    bank_by_id = {r["id"]: r for r in bank}
    vac = vacancy_text.lower()
    entries: dict[str, dict] = {}

    ranked = sorted(out["result"].get("selected", []),
                    key=lambda x: (-int(x.get("value") or 0), -int(x.get("relevance") or 0)))
    for s in ranked:
        src = bank_by_id.get(s.get("id"))
        if not src or SECTION_BY_TYPE.get(src.get("type")) is None:
            continue
        key = src.get("title", s["id"])
        e = entries.setdefault(key, {
            "section": SECTION_BY_TYPE[src["type"]],
            "header": _entry_header(src),
            "period": src.get("period", ""),
            "bullets": [], "ids": [], "_skills": [], "relevance": 0,
        })
        if len(e["bullets"]) >= 2:
            continue
        e["bullets"].append({"id": s["id"], "text": s.get("bullet", "")})
        e["ids"].append(s["id"])
        e["relevance"] = max(e["relevance"], int(s.get("relevance", 0) or 0))
        for sk in _as_list(src.get("skills")):
            if sk not in e["_skills"]:
                e["_skills"].append(sk)

    sections = {name: [] for name in SECTION_ORDER}
    for e in sorted(entries.values(), key=lambda x: -x["relevance"]):
        skills = e.pop("_skills")
        e["skills"] = _fit_skills(sorted(skills, key=lambda sk: sk.lower() not in vac))  # stable sort
        sections[e["section"]].append(e)

    return {
        "headline": out["requirements"].get("title", ""),
        "sections": {k: v for k, v in sections.items() if v},
        "drop": out["result"].get("drop", []),
        "gaps": out["result"].get("gaps", []),
        "issues": out["issues"],
    }


def render_cv_text(layout: dict) -> str:
    """Plain text in the order and shape of the CV template (easy to paste or to fill in Canva)."""
    lines = [f"HEADLINE: {layout['headline']}", ""]
    for section, entries in layout["sections"].items():
        lines.append(section)
        for e in entries:
            lines.append(f"{e['header']}    {e['period']}")
            lines += [f"  • {b['text']}  [{b['id']}]" for b in e["bullets"]]
            lines.append(f"  Skills: {' | '.join(e['skills'])}")
        lines.append("")
    return "\n".join(lines)
