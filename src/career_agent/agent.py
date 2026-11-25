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


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(s or "")}


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
        extra = _numbers(item.get("bullet", "")) - _numbers(src_text)
        if extra:
            issues.append(f"[{rid}] numbers not in source: {sorted(extra)} -> {item['bullet']}")
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
