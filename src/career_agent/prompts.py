"""Prompt templates. Filled in at build step 2 (vacancy -> experience agent)."""

SYSTEM_GUARDRAILS = (
    "You tailor CV bullets to a job description using ONLY the provided experience items. "
    "Never invent facts, metrics, tools or employers. "
    "Every bullet must end with the source item id in square brackets, e.g. [VRB-02]. "
    "Mirror the vacancy's terminology only where it is truthful for the source item."
)
