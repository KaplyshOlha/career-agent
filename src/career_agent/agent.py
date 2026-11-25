"""Vacancy -> experience agent: extract requirements, select & rewrite bank items, validate."""
import json
import re

from .llm import chat
from .prompts import EXTRACT_SYSTEM, TAILOR_SYSTEM

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


MAX_BULLET_CHARS = 120
NON_BULLET_TYPES = {"skills", "education"}
_ID_TAG = re.compile(r"\s*[\(\[]\s*#?[A-Z]{2,5}-\d{1,3}\s*[\)\]]")

_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*([KkMm])?(?![\w])|(\d+(?:\.\d+)?)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


def _numbers(s: str) -> set[float]:
    """Numbers in text; '566,414' -> 566414, '441K' -> 441000."""
    out = set()
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
        if len(item.get("bullet", "")) > MAX_BULLET_CHARS:
            issues.append(f"[{rid}] bullet longer than {MAX_BULLET_CHARS} chars (won't fit one CV line)")
    return issues


def tailor_to_vacancy(vacancy_text: str, bank: list[dict], llm: str = "databricks",
                      model: str | None = None) -> dict:
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
    ], model=model, temperature=0.2, max_tokens=3000))

    # Post-process: strip id tags the model may put into bullet text; drop non-CV item types.
    for it in result.get("selected", []):
        it["bullet"] = _ID_TAG.sub("", it.get("bullet", "")).strip().rstrip(".")
    result["selected"] = [
        it for it in result.get("selected", [])
        if bank_by_id.get(it.get("id"), {}).get("type") not in NON_BULLET_TYPES
    ]
    result["selected"] = sorted(result.get("selected", []), key=lambda x: -int(x.get("relevance", 0) or 0))
    return {"requirements": req, "result": result, "issues": validate_output(result, bank_by_id)}


def render_markdown(out: dict, bank: list[dict]) -> str:
    bank_by_id = {r["id"]: r for r in bank}
    req, res = out["requirements"], out["result"]
    md = [f"## {req.get('title', 'Vacancy')} — {req.get('company', '')}",
          f"**Fit: {res.get('fit_score', '?')}/10.** {res.get('fit_summary', '')}", "",
          "### Tailored bullets"]
    for s in res.get("selected", []):
        src = bank_by_id.get(s.get("id"), {})
        md.append(f"- {s.get('bullet')}  \n  _[{s.get('id')}] {src.get('title', '?')} · "
                  f"relevance {s.get('relevance')}/5 · covers: {', '.join(s.get('matched_requirements', []))}_")
    if res.get("drop"):
        md += ["", "### Remove for this vacancy"] + [f"- [{d.get('id')}] {d.get('reason')}" for d in res["drop"]]
    if res.get("gaps"):
        md += ["", "### Gaps (not in the bank)"] + \
              [f"- **{g.get('requirement')}** — {g.get('note', '')}" for g in res["gaps"]]
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

    for s in out["result"].get("selected", []):
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
