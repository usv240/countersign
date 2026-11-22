"""Shared helpers for every Countersign Lambda function."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
T_DRAFTS = os.environ.get("T_DRAFTS", "cs-drafts")
T_APPROVALS = os.environ.get("T_APPROVALS", "cs-approvals")
T_LEDGER = os.environ.get("T_LEDGER", "cs-ledger")
T_KEYS = os.environ.get("T_KEYS", "cs-keys")
PRESETS = Path(__file__).resolve().parent / "presets"
UA = "countersign/1.0 (hackathon demo; contact via project repository)"

_ddb = None


def ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=REGION)
    return _ddb


def table(name: str):
    return ddb().Table(name)


def now() -> int:
    return int(time.time())


_event_seq = 0


def event_sk(tag: str = "") -> str:
    """Sort key for a run's event, in the order it happened: second, millisecond, then a per-process counter.
    The second stays first so older events (second plus a random suffix) still sort with these."""
    global _event_seq
    _event_seq += 1
    t = time.time()
    return f"EVENT#{int(t):012d}#{int(t * 1000) % 1000:03d}{_event_seq:06d}{tag or uuid.uuid4().hex[:4]}"


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:16]


NAMESPACE = "Countersign"


def metric(name: str, value: float = 1, unit: str = "Count", **dims: str) -> None:
    """One CloudWatch metric, written as an Embedded Metric Format log line.

    No API call and no added latency: CloudWatch Logs turns the line into a metric. Each metric is recorded
    twice, once as a total and once split by its dimensions, so the dashboard can show both. Dimension values
    are short fixed codes, never user text, so the number of metric series stays small."""
    dims = {k: str(v)[:40] for k, v in dims.items() if v is not None}
    sets = [[]] + ([sorted(dims)] if dims else [])
    print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": NAMESPACE, "Dimensions": sets, "Metrics": [{"Name": name, "Unit": unit}]}]},
        name: value, **dims}))


def log_event(event: str, **fields: Any) -> None:
    """A structured JSON log line, searchable in CloudWatch Logs Insights by its event field."""
    print(json.dumps({"event": event, **fields}, default=str))


def to_ddb(obj: Any) -> Any:
    """DynamoDB cannot store floats; store numbers as Decimal and big blobs as JSON strings."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_ddb(obj: Any) -> Any:
    if isinstance(obj, list):
        return [from_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def http_json(url: str, *, headers: dict | None = None, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def nws_active_for_county(geoid: str) -> list[dict]:
    """Active National Weather Service alerts that list this county's SAME code. Code does the fetch."""
    same = f"0{geoid}"
    state_fips = geoid[:2]
    st = STATE_ABBR.get(state_fips)
    if not st:
        return []
    fc = http_json(f"https://api.weather.gov/alerts/active?area={st}")
    out = []
    for f in fc.get("features", []):
        codes = ((f.get("properties") or {}).get("geocode") or {}).get("SAME") or []
        if same in codes:
            out.append(f)
    return out


def nws_alert(alert_id: str) -> dict:
    if not alert_id.startswith("https://api.weather.gov/alerts/"):
        alert_id = "https://api.weather.gov/alerts/" + urllib.parse.quote(alert_id.split("/")[-1])
    return http_json(alert_id)


def load_preset(pid: str) -> dict:
    safe = "".join(ch for ch in pid if ch.isalnum() or ch in "-_")
    return json.loads((PRESETS / f"{safe}.json").read_text(encoding="utf-8"))


def list_presets() -> list[dict]:
    out = []
    for p in sorted(PRESETS.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({k: d.get(k) for k in ("id", "title", "kind", "summary", "county_geoid", "county_name",
                                           "failure", "what_to_expect", "modified")})
    return out


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "cs_live_" + base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")


STATE_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
    "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
    "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}
