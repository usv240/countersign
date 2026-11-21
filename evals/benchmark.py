"""Outside benchmark: every local public alert in FEMA's archive for one year.

No case here was written by this project. The question: of the alerts their
own senders later said were sent in error, how many would the gate have held
for a fix before a live send, and by which rule? And how often does the gate
hold ordinary alerts that were never retracted (the cost of the rules)?

Step 1 (network):  python evals/benchmark.py fetch --year 2025
Step 2 (offline):  python evals/benchmark.py score --year 2025

Only the rules that apply to a historical alert are scored: R3 (area), R4
(wording) and R5 (class). R1 and R2 always ask for two officers on a local alert
with no weather service source, and R6 (staged correction) did not exist then,
so counting them would make every alert "held" and say nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from countersign.cap import from_openfema  # noqa: E402
from countersign.gate import evaluate, proposal_from_cap  # noqa: E402
from countersign.geo import union_jurisdiction  # noqa: E402

BASE = "https://www.fema.gov/api/open/v1/IpawsArchivedAlerts"
OUT = ROOT / "evals" / "benchmark"
ERROR_WORDS = re.compile(r"(disregard|sent in error|in error|by mistake|mistakenly|erroneous|false alarm|"
                         r"sent accidentally|accidental|please ignore|ignore (the|this|that|previous|last)|"
                         r"was a test|was not intended|incorrect(ly)? (sent|issued)|error)", re.I)
SCORED = ("R3", "R4", "R5")
TEST_EVENTS = {"RWT", "RMT", "DMO", "NPT", "NMN"}


def fetch(year: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    compact = []
    for m in range(1, 13):
        start = f"{year}-{m:02d}-01T00:00:00.000Z"
        end = f"{year + (m == 12)}-{(m % 12) + 1:02d}-01T00:00:00.000Z"
        flt = f"sent ge '{start}' and sent lt '{end}' and status eq 'Actual' and cogId ne 200032"
        skip = 0
        while True:
            for attempt in range(5):
                try:
                    r = requests.get(BASE, params={"$filter": flt, "$top": 1000, "$skip": skip, "$orderby": "sent",
                                                   "$select": "id,sent,msgType,cogId,identifier,originalMessage"},
                                     timeout=120)
                    r.raise_for_status()
                    batch = r.json().get("IpawsArchivedAlerts", [])
                    break
                except requests.RequestException:
                    time.sleep(3 * (attempt + 1))
            else:
                raise SystemExit(f"fetch failed at {year}-{m:02d} skip {skip}")
            for rec in batch:
                try:
                    w = from_openfema(rec)
                except Exception:
                    continue
                i = w.en
                compact.append({
                    "archive_id": rec.get("id"), "identifier": w.identifier, "sent": rec["sent"], "cog": rec.get("cogId"),
                    "msg_type": w.msg_type, "references": w.references, "event": i.event,
                    "event_code": i.event_codes.get("SAME", ""), "sender_name": i.sender_name,
                    "text": " ".join(filter(None, [i.headline, i.description, (i.parameters.get("CMAMtext") or [""])[0],
                                                   (i.parameters.get("CMAMlongtext") or [""])[0]]))[:2000],
                    "same": sorted({s for a in i.areas for s in a.same}),
                    "has_polygon": any(a.polygons for a in i.areas),
                    "originalMessage": rec["originalMessage"],
                })
            print(f"{year}-{m:02d} skip {skip}: {len(batch)}", flush=True)
            if len(batch) < 1000:
                break
            skip += 1000
    (OUT / f"alerts-{year}.jsonl").write_text("\n".join(json.dumps(c) for c in compact), encoding="utf-8")
    print("saved", len(compact))


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00").replace(".000", ""))


def label(alerts: list[dict]) -> dict[str, str]:
    """error | cancelled | ordinary, for every Alert or Update."""
    by_cog: dict[int, list[dict]] = defaultdict(list)
    for a in alerts:
        by_cog[a["cog"]].append(a)
    labels: dict[str, str] = {}
    for cog, items in by_cog.items():
        items.sort(key=lambda a: a["sent"])
        for idx, a in enumerate(items):
            if a["msg_type"] not in ("Alert", "Update") or ERROR_WORDS.search(a["text"] or ""):
                continue
            t0 = _t(a["sent"])
            lab = "ordinary"
            for b in items[idx + 1:]:
                if _t(b["sent"]) - t0 > timedelta(hours=24):
                    break
                if b["msg_type"] in ("Alert", "Update") and ERROR_WORDS.search(b["text"] or ""):
                    lab = "error"
                    break
                if b["msg_type"] == "Cancel" and any(a["identifier"] in r for r in b["references"]):
                    lab = "cancelled"
            labels[a["identifier"]] = lab
    return labels


def jurisdiction_for(same: list[str]):
    ids = []
    for s in same:
        s = s.strip()
        if len(s) == 6 and s.isdigit():
            st, co = s[1:3], s[3:]
            ids.append(st if co == "000" else st + co)
    if not ids or len(ids) > 40:
        return None
    try:
        return union_jurisdiction(ids)
    except Exception:
        return None


def score(year: int) -> dict:
    lines = (OUT / f"alerts-{year}.jsonl").read_text(encoding="utf-8").splitlines()
    alerts = [json.loads(x) for x in lines if x.strip()]
    labels = label(alerts)
    rows = []
    for a in alerts:
        lab = labels.get(a["identifier"])
        if not lab:
            continue
        jur = jurisdiction_for(a["same"])
        if not jur:
            continue
        w = from_openfema({"originalMessage": a["originalMessage"], "cogId": a["cog"], "sent": a["sent"]})
        if not (w.en.parameters.get("CMAMtext") or w.en.parameters.get("CMAMlongtext")):
            continue  # not a phone alert (EAS or web feed only)
        if (w.en.event_codes.get("SAME") or "") in TEST_EVENTS or "test" in (w.en.event or "").lower():
            continue  # scheduled system tests (RWT, RMT, DMO), not public alerts
        d = evaluate(proposal_from_cap(w), w, jur)
        fails = [c for c in d.checks if c.rule in SCORED and c.status == "fail" and c.hard]
        confirm = [c for c in fails if c.rule == "R3" and c.evidence.get("needs") == "whole_area_confirmed_by"]
        held = [c for c in fails if c not in confirm]
        status = "held" if held else ("confirm" if confirm else "clear")
        rows.append({"archive_id": a["archive_id"], "sent": a["sent"], "cog": a["cog"], "event": a["event"],
                     "label": lab, "jurisdiction": jur.name, "geoid": jur.geoid, "status": status,
                     "held_by": [c.rule for c in held], "has_polygon": a["has_polygon"],
                     "people_if_code_used": d.facts.get("people_if_code_used"),
                     "messages": {c.rule: c.message for c in held + confirm}})
    summary = summarise(rows)
    summary.update({"year": year, "messages_in_archive": len(alerts)})
    (OUT / f"rows-{year}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    (OUT / f"summary-{year}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return summary


def summarise(rows: list[dict]) -> dict:
    out: dict = {"phone_alerts_scored": len(rows)}
    for lab in ("error", "cancelled", "ordinary"):
        rs = [r for r in rows if r["label"] == lab]
        n = len(rs) or 1
        held = [r for r in rs if r["status"] == "held"]
        conf = [r for r in rs if r["status"] == "confirm"]
        out[lab] = {"n": len(rs), "held": len(held), "held_pct": round(100 * len(held) / n, 1),
                    "confirm_whole_county": len(conf), "confirm_pct": round(100 * len(conf) / n, 1),
                    "clear": len(rs) - len(held) - len(conf),
                    "held_by": dict(Counter(x for r in held for x in r["held_by"]))}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "score"])
    ap.add_argument("--year", type=int, default=2025)
    a = ap.parse_args()
    fetch(a.year) if a.step == "fetch" else score(a.year)
