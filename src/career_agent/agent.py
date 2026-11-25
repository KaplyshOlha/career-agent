"""Vacancy -> experience agent. Implemented at build step 2."""


def tailor_to_vacancy(vacancy_text: str, bank: list[dict], llm: str = "databricks") -> dict:
    """Select the most relevant bank items and rewrite them for the vacancy.

    Returns: {"requirements": [...], "bullets": [{"text": ..., "source_id": ...}], "gaps": [...]}
    """
    raise NotImplementedError("Step 2: implement after the experience bank table exists")
