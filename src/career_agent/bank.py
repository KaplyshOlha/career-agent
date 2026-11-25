"""Experience bank: load from private Volume (or public sample) and write to Delta."""
import json
import os

from .config import REAL_BANK_PATH, SAMPLE_BANK_PATH, table

COLUMNS = [
    "id", "type", "title", "role", "company", "period", "domain",
    "context", "action", "result_metric", "skills", "raw_text", "source",
]


def load_bank_json(prefer_real: bool = True) -> list[dict]:
    """Load the real bank from the private Volume; fall back to the public sample."""
    use_real = prefer_real and os.path.exists(REAL_BANK_PATH)
    path = REAL_BANK_PATH if use_real else SAMPLE_BANK_PATH
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    print(f"Loaded {len(items)} items from {'REAL' if use_real else 'SAMPLE'} bank: {path}")
    return [{c: item.get(c, [] if c == "skills" else "") for c in COLUMNS} for item in items]


def validate(items: list[dict]) -> None:
    """Basic checks: unique non-empty ids, action present."""
    ids = [i["id"] for i in items]
    assert all(ids), "Every item needs an id"
    dupes = {x for x in ids if ids.count(x) > 1}
    assert not dupes, f"Duplicate ids: {dupes}"
    missing = [i["id"] for i in items if not i["action"]]
    assert not missing, f"Items without action: {missing}"


def write_bank_table(spark, items: list[dict], name: str = "experience_bank") -> str:
    """Overwrite the Delta table with the bank (CDF on, for a future vector index)."""
    validate(items)
    df = spark.createDataFrame(items)
    full = table(name)
    (df.write.mode("overwrite")
       .option("overwriteSchema", "true")
       .saveAsTable(full))
    spark.sql(f"ALTER TABLE {full} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    return full


def read_bank(spark, name: str = "experience_bank") -> list[dict]:
    return [r.asDict() for r in spark.table(table(name)).collect()]
