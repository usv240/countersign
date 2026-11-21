"""Fetch real alert records from FEMA's public IPAWS archive (OpenFEMA).

No API key is needed. FEMA states it does not validate message content.
Dataset: https://www.fema.gov/openfema-data-page/ipaws-archived-alerts-v1

Usage:
    python data/fetch_openfema.py            # fetch every named incident
    python data/fetch_openfema.py --sample   # also fetch the benchmark sample

Personal sender addresses are removed before anything is written to disk.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

BASE = "https://www.fema.gov/api/open/v1/IpawsArchivedAlerts"
RAW = Path(__file__).resolve().parent / "raw"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Each incident is a window of time plus a filter. The records are public,
# but the sender field often holds a named person's address, so it is redacted.
INCIDENTS = {
    "hawaii-2018-01-13": {
        "title": "Hawaii false ballistic missile alert",
        "filter": "sent ge '2018-01-13T18:00:00.000Z' and sent le '2018-01-13T19:00:00.000Z' and cogId eq 200003",
    },
    "los-angeles-2025-01-09": {
        "title": "Los Angeles County Kenneth Fire evacuation alert sent county-wide",
        "filter": "sent ge '2025-01-09T23:40:00.000Z' and sent le '2025-01-10T01:00:00.000Z' and cogId eq 200971",
    },
    "kerr-county-2025-07-04": {
        "title": "Kerr County, Texas: overnight National Weather Service flood warnings",
        # 048265 is the SAME code for Kerr County, Texas.
        "filter": "sent ge '2025-07-04T05:00:00.000Z' and sent le '2025-07-04T17:00:00.000Z' and contains(originalMessage,'048265')",
    },
}


def query(flt: str, top: int = 1000, skip: int = 0) -> list[dict]:
    params = {"$filter": flt, "$top": top, "$skip": skip, "$orderby": "sent"}
    for attempt in range(4):
        try:
            r = requests.get(BASE, params=params, timeout=90)
            r.raise_for_status()
            return r.json().get("IpawsArchivedAlerts", [])
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def redact(rec: dict) -> dict:
    """Remove personal email addresses from a record, keeping everything else."""
    text = json.dumps(rec)
    text = EMAIL.sub("[redacted sender]", text)
    return json.loads(text)


def fetch_incidents() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for key, spec in INCIDENTS.items():
        recs = [redact(r) for r in query(spec["filter"])]
        out = {"incident": key, "title": spec["title"], "source": BASE,
               "filter": spec["filter"], "count": len(recs), "records": recs}
        (RAW / f"{key}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"{key}: {len(recs)} records")


def fetch_sample(year: int = 2025, per_month: int = 400) -> None:
    """A benchmark sample of non-NWS public alerts, one batch per month.

    Retractions are found later by scoring: an alert counts as retracted when
    the same sender issued a Cancel for it, or a follow-up whose text says it
    was sent in error, within 24 hours.
    """
    RAW.mkdir(parents=True, exist_ok=True)
    allrecs: list[dict] = []
    for m in range(1, 13):
        start = f"{year}-{m:02d}-01T00:00:00.000Z"
        end = f"{year + (m == 12)}-{(m % 12) + 1:02d}-01T00:00:00.000Z"
        flt = (f"sent ge '{start}' and sent lt '{end}' and status eq 'Actual' "
               "and not startswith(identifier,'urn:oid:2.49.0.1.840')")
        recs = query(flt, top=per_month)
        allrecs.extend(redact(r) for r in recs)
        print(f"{year}-{m:02d}: {len(recs)}")
    out = {"source": BASE, "year": year, "per_month": per_month,
           "count": len(allrecs), "records": allrecs}
    (RAW / f"sample-{year}.json").write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    fetch_incidents()
    if args.sample:
        fetch_sample()
