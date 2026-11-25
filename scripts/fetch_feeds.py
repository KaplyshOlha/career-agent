"""Snapshot RSS feeds into feeds/ (run by .github/workflows/fetch_feeds.yml).

Fallback for Databricks Free Edition: if serverless can't reach DOU/Djinni directly,
the daily job reads these snapshots from GitHub instead.
"""
import hashlib
import os
import sys
import time

import requests
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UA = {"User-Agent": "Mozilla/5.0 (career-agent; personal non-commercial job search)"}


def snapshot_name(source: str, url: str) -> str:  # keep in sync with career_agent.jobs.snapshot_name
    return f"{source}_{hashlib.md5(url.encode()).hexdigest()[:10]}.xml"


def main() -> int:
    with open(os.path.join(ROOT, "config", "feeds.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    os.makedirs(os.path.join(ROOT, "feeds"), exist_ok=True)
    ok = 0
    for source, urls in cfg.items():
        for url in urls or []:
            try:
                r = requests.get(url, headers=UA, timeout=20)
                if r.status_code == 200 and r.content.strip().startswith(b"<"):
                    with open(os.path.join(ROOT, "feeds", snapshot_name(source, url)), "wb") as out:
                        out.write(r.content)
                    ok += 1
                    print(f"OK   {source} {url} ({len(r.content)} bytes)")
                else:
                    print(f"SKIP {source} {url} HTTP {r.status_code}")
            except Exception as e:
                print(f"FAIL {source} {url} {e}")
            time.sleep(1)  # be polite
    print(f"{ok} feeds saved")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
