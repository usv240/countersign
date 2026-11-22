"""Public HTTP API for the Countersign website and for developers.

Routes (Amazon API Gateway HTTP API, payload v2):
  GET  /api/health
  GET  /api/presets                 list demo presets
  GET  /api/presets/{id}            one preset, downloadable
  POST /api/evaluate                check your own warning and draft against the rules (no key needed, rate limited)
  POST /api/keys                    get a free developer API key (shown once)
  POST /v1/evaluate                 the same check, with x-api-key
  GET  /v1/usage                    your key's usage
  POST /api/runs                    start the agent on a preset or on live weather for a county
  GET  /api/runs/{id}               transcript, drafts, channels and paging state
  POST /api/runs/{id}/decide        an officer approves or rejects (the approval service)
  POST /api/debate                  two models argue send versus hold; shown next to the rules
  GET  /api/live?county=48265       active National Weather Service warnings for a county
  GET  /api/results                 measured results: benchmark and stress test
  GET  /api/status                  live operating numbers from CloudWatch: metrics, alarms, latency
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3  # noqa: E402
from boto3.dynamodb.conditions import Key  # noqa: E402

from common import (T_APPROVALS, T_DRAFTS, T_KEYS, T_LEDGER, from_ddb, hash_key, list_presets,  # noqa: E402
                    load_preset, metric, new_api_key, new_id, now, nws_active_for_county, table, to_ddb)
from countersign.cap import from_nws_feature, from_openfema, load_any  # noqa: E402
from countersign.gate import evaluate, proposal_from_cap  # noqa: E402
from countersign.geo import clip_to, load_jurisdiction  # noqa: E402

RUNNER = os.environ.get("RUNNER_FN", "cs-runner")
SENDER = os.environ.get("SENDER_FN", "cs-sender")
PAGING_SM = os.environ.get("PAGING_SM_ARN", "")
RESULTS = Path(__file__).resolve().parent / "results"
DAILY_RUNS = int(os.environ.get("DAILY_RUN_CAP", "300"))
DAILY_IP_EVALS = int(os.environ.get("DAILY_IP_EVAL_CAP", "500"))
KEY_DAILY = int(os.environ.get("KEY_DAILY_CAP", "2000"))
CONTACTS = [{"name": "Officer A", "role": "Duty officer"}, {"name": "Officer B", "role": "Deputy coordinator"},
            {"name": "Officer C", "role": "Emergency management coordinator"}]


def resp(code: int, body, headers: dict | None = None):
    h = {"content-type": "application/json", "cache-control": "no-store"}
    h.update(headers or {})
    return {"statusCode": code, "headers": h, "body": json.dumps(body, default=str)}


def err(code: int, msg: str, **extra):
    return resp(code, {"error": msg, **extra})


def body_of(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    if len(raw) > 400_000:
        raise ValueError("Request body is over 400 KB.")
    return json.loads(raw)


def ip_of(event) -> str:
    ip = (event.get("requestContext") or {}).get("http", {}).get("sourceIp", "0")
    return hashlib.sha256(("cs-salt-" + ip).encode()).hexdigest()[:16]


def bump(counter: str, cap: int) -> bool:
    """Atomic daily counter. Returns False when the cap is reached."""
    day = now() // 86400
    try:
        table(T_LEDGER).update_item(Key={"pk": f"COUNT#{counter}", "sk": str(day)},
                                    UpdateExpression="ADD n :one SET ttl_at = :t",
                                    ConditionExpression="attribute_not_exists(n) OR n < :cap",
                                    ExpressionAttributeValues={":one": 1, ":cap": cap, ":t": (day + 3) * 86400})
        return True
    except table(T_LEDGER).meta.client.exceptions.ConditionalCheckFailedException:
        return False


# ---------- evaluate (bring your own data) ----------

def build_case(b: dict):
    """Returns (warning, jurisdiction, proposal, source_note). User data is never treated as official."""
    if b.get("preset_id"):
        p = load_preset(b["preset_id"])
        w = from_openfema(p["record"])
        jur = load_jurisdiction(b.get("county_geoid") or p["county_geoid"])
        prop = dict(b.get("proposal") or p.get("proposal") or proposal_from_cap(w))
        if "warning_id" not in prop or (prop.get("warning_id") and prop["warning_id"] != w.identifier):
            # The warning id always comes from the stored record, never from the caller.
            prop["warning_id"] = w.identifier if w.official else None
        note = "Preset: " + ("a modified copy of a public record" if p.get("modified") else "an unmodified public record")
        return w, jur, prop, note
    if b.get("live_warning_id") and b.get("county_geoid"):
        jur = load_jurisdiction(str(b["county_geoid"]))
        feats = [f for f in nws_active_for_county(jur.geoid) if (f.get("properties") or {}).get("id") == b["live_warning_id"]]
        if not feats:
            raise ValueError("That warning is not active for this county any more.")
        w = from_nws_feature(feats[0], official=True)
        prop = dict(b.get("proposal") or auto_proposal(w, jur))
        prop["warning_id"] = w.identifier
        return w, jur, prop, "Live National Weather Service warning, fetched by code"
    if not b.get("county_geoid"):
        raise ValueError("county_geoid is required: 2 digits for a state or 5 for a county, for example 48265.")
    jur = load_jurisdiction(str(b["county_geoid"]))
    w = load_any(b["warning"]) if b.get("warning") else None
    prop = b.get("proposal") or (proposal_from_cap(w) if w else None)
    if not prop:
        raise ValueError("Send a warning, a proposal, or both.")
    return w, jur, prop, "Your data. Treated as unsourced: it was not fetched from an official feed."


def auto_proposal(w, jur) -> dict:
    """What the agent would start from for a live warning: the warning's own area, clipped."""
    src = [pt for a in w.en.areas for pt in a.polygons]
    poly = clip_to(src[0], jur) if src else None
    i = w.en
    return {"warning_id": w.identifier, "channel": "live", "requested_by": "officer",
            "alert_class": "Imminent Threat", "polygon": poly, "geocodes": [] if poly else [jur.same],
            "text_360": (i.parameters.get("CMAMlongtext") or [""])[0] or i.headline,
            "text_90": (i.parameters.get("CMAMtext") or [""])[0][:90], "action_ids": [], "correction_staged": False}


def apply_area(prop: dict, mode: str, w, jur) -> dict:
    """The lab's area choice, computed by code the same way the agent's draft tool does."""
    if mode == "warning":
        src = [pt for a in (w.en.areas if w else []) for pt in a.polygons]
        prop["polygon"], prop["geocodes"] = (clip_to(src[0], jur) if src else None), ([] if src else [jur.same])
    elif mode == "code":
        prop["polygon"], prop["geocodes"] = None, [jur.same]
    elif mode == "none":
        prop["polygon"], prop["geocodes"] = None, []
    return prop


def do_evaluate(b: dict) -> dict:
    w, jur, prop, note = build_case(b)
    prop = dict(prop)
    if b.get("area_mode") in ("warning", "code", "none"):
        prop = apply_area(prop, b["area_mode"], w, jur)
    prop.setdefault("channel", "live")
    if prop.get("requested_by") not in ("officer", "agent"):
        prop["requested_by"] = "officer"
    d = evaluate(prop, w, jur, local_places=b.get("local_places") or [])
    metric("GateOutcome", Outcome=d.outcome, Stage="evaluate")
    out = {"decision": d.to_dict(), "source": note, "jurisdiction": {"geoid": jur.geoid, "name": jur.name,
                                                                       "population": jur.population},
           "proposal": prop}
    if b.get("include_geometry"):
        out["geometry"] = {"jurisdiction": jur.to_geojson()["geometry"],
                           "warning": [p for a in (w.en.areas if w else []) for p in a.polygons]}
    return out


# ---------- keys ----------

def check_key(event):
    key = (event.get("headers") or {}).get("x-api-key", "")
    if not key.startswith("cs_live_"):
        return None, err(401, "Missing or invalid x-api-key header. Get a free key at POST /api/keys.")
    item = table(T_KEYS).get_item(Key={"key_hash": hash_key(key)}).get("Item")
    if not item or item.get("revoked"):
        return None, err(401, "Unknown or revoked API key.")
    if not bump(f"key-{item['key_hash'][:16]}", KEY_DAILY):
        return None, err(429, f"Daily limit of {KEY_DAILY} calls reached for this key.")
    table(T_KEYS).update_item(Key={"key_hash": item["key_hash"]}, UpdateExpression="ADD calls :one SET last_used = :n",
                              ExpressionAttributeValues={":one": 1, ":n": now()})
    return from_ddb(item), None


# ---------- runs ----------

def start_run(b: dict, event) -> dict:
    if not bump("runs", DAILY_RUNS):
        raise PermissionError("Today's demo budget for live agent runs is used up. Replays still work.")
    preset_id = b.get("preset_id")
    if preset_id:
        p = load_preset(preset_id)
        geoid, county = p["county_geoid"], p["county_name"]
        title = p["title"]
    else:
        geoid = str(b.get("county_geoid") or "")
        jur = load_jurisdiction(geoid)
        county, title = jur.name, f"Live weather for {jur.name}"
    run_id = new_id("run_")
    secret = new_id()
    timeout = 30 if preset_id == "kerr-no-answer" else int(b.get("page_timeout") or 120)
    timeout = max(20, min(timeout, 600))
    cfg = {"pk": f"RUN#{run_id}", "sk": "CONFIG", "run_id": run_id, "preset_id": preset_id, "county_geoid": geoid,
           "county_name": county, "title": title, "created": now(), "secret_hash": hash_key(secret),
           "agent_status": "queued", "local_places": b.get("local_places") or [], "page_timeout": timeout,
           "ttl_at": now() + 7 * 86400}
    table(T_DRAFTS).put_item(Item=to_ddb(cfg))
    if PAGING_SM:
        boto3.client("stepfunctions").start_execution(
            stateMachineArn=PAGING_SM, name=run_id,
            input=json.dumps({"run_id": run_id, "warning_title": title, "contacts": CONTACTS,
                              "timeout": timeout, "max_rounds": 3}))
    msg = (b.get("message") or "")[:2000]
    boto3.client("lambda").invoke(FunctionName=RUNNER, InvocationType="Event",
                                  Payload=json.dumps({"run_id": run_id, "message": msg}).encode())
    return {"run_id": run_id, "officer_token": secret, "title": title, "page_timeout": timeout}


def get_run(run_id: str) -> dict:
    items = from_ddb(table(T_DRAFTS).query(KeyConditionExpression=Key("pk").eq(f"RUN#{run_id}"))["Items"])
    if not items:
        raise KeyError("run")
    cfg = next((i for i in items if i["sk"] == "CONFIG"), {})
    cfg.pop("secret_hash", None)
    drafts = [i for i in items if i["sk"].startswith("DRAFT#")]
    events = sorted([i for i in items if i["sk"].startswith("EVENT#")], key=lambda i: i["sk"])
    for d in drafts:
        d["approvals"] = [{k: v for k, v in from_ddb(a).items() if k in ("officer", "decision", "at", "kind")} for a in
                          table(T_APPROVALS).query(KeyConditionExpression=Key("pk").eq(f"DRAFT#{d['draft_id']}"))["Items"]]
    led = table(T_LEDGER)
    channel = from_ddb(led.query(KeyConditionExpression=Key("pk").eq(f"CHANNEL#{run_id}"))["Items"])
    paging = from_ddb(led.get_item(Key={"pk": f"PAGING#{run_id}", "sk": "CURRENT"}).get("Item") or {})
    paging.pop("token", None)
    return {"config": cfg, "drafts": drafts, "events": [{"kind": e["kind"], "body": e["body"], "at": e["at"]}
                                                        for e in events],
            "channel": [{"channel": c["channel"], "body": c["body"], "sk": c["sk"]} for c in channel],
            "paging": paging}


def decide(run_id: str, b: dict) -> dict:
    cfg = from_ddb(table(T_DRAFTS).get_item(Key={"pk": f"RUN#{run_id}", "sk": "CONFIG"}).get("Item") or {})
    if not cfg:
        raise KeyError("run")
    if hash_key(str(b.get("officer_token", ""))) != cfg.get("secret_hash"):
        raise PermissionError("This browser is not the officer for this run. Start your own run to act as officer.")
    officer = b.get("officer")
    if officer not in {c["name"] for c in CONTACTS}:
        raise ValueError("officer must be Officer A, Officer B or Officer C.")
    decision, kind = b.get("decision"), b.get("kind", "alert")
    if decision not in ("approve", "reject") or kind not in ("alert", "correction"):
        raise ValueError("decision must be approve or reject; kind must be alert or correction.")
    draft_id = str(b.get("draft_id", ""))
    if not table(T_DRAFTS).get_item(Key={"pk": f"RUN#{run_id}", "sk": f"DRAFT#{draft_id}"}).get("Item"):
        raise KeyError("draft")
    table(T_APPROVALS).put_item(Item=to_ddb({"pk": f"DRAFT#{draft_id}", "sk": f"{kind}#{officer}", "officer": officer,
                                             "decision": decision, "kind": kind, "run_id": run_id, "at": now()}))
    stop_paging(run_id, f"{officer} {decision}d the {kind}")
    out = {"recorded": True, "officer": officer, "decision": decision}
    if decision == "approve":
        payload = {"run_id": run_id, "draft_id": draft_id, "kind": kind}
        if b.get("confirm_whole_county"):
            payload["whole_area_confirmed_by"] = officer
        r = boto3.client("lambda").invoke(FunctionName=SENDER, Payload=json.dumps(payload).encode())
        out["send"] = json.loads(r["Payload"].read())
    return out


def stop_paging(run_id: str, why: str) -> None:
    item = table(T_LEDGER).get_item(Key={"pk": f"PAGING#{run_id}", "sk": "CURRENT"}).get("Item")
    if item and item.get("token") and not item.get("done"):
        try:
            boto3.client("stepfunctions").send_task_success(taskToken=item["token"], output=json.dumps({"why": why}))
        except Exception as e:
            print("PAGING_STOP", repr(e))
        table(T_LEDGER).update_item(Key={"pk": f"PAGING#{run_id}", "sk": "CURRENT"},
                                    UpdateExpression="SET done = :t, decided = :w",
                                    ExpressionAttributeValues={":t": True, ":w": why})


# ---------- status (observability, public) ----------

HTTP_API_ID = os.environ.get("HTTP_API_ID", "")
FUNCTIONS = ["cs-tools", "cs-hook", "cs-sender", "cs-api", "cs-runner", "cs-pager", "cs-escalated", "cs-recorder"]
_status_cache: dict = {}


def status() -> dict:
    """Operating numbers for the last 14 days, read from CloudWatch. Cached for 60 seconds per container so a
    busy status page cannot run up the CloudWatch bill."""
    if _status_cache.get("at", 0) > time.time() - 60:
        return dict(_status_cache["body"], cached=True)
    cw = boto3.client("cloudwatch")
    end = int(time.time()) // 3600 * 3600 + 3600
    start = end - 14 * 86400
    q, labels = [], {}

    def add(qid, label, expr=None, stat=None):
        labels[qid] = label
        if expr:
            q.append({"Id": qid, "Expression": expr, "Period": 86400, "Label": label})
        else:
            q.append({"Id": qid, "MetricStat": {"Metric": stat[0], "Period": 86400, "Stat": stat[1]}, "Label": label})

    def cs(name, dims=None):
        return {"Namespace": "Countersign", "MetricName": name, "Dimensions": dims or []}

    for i, name in enumerate(["LiveSent", "SenderRefused", "SendAttempt", "HookDenied", "HookAllowed",
                              "GuardrailIntervened", "ToolCall", "AgentRun", "PageSent", "Escalated",
                              "ApiRequest", "PeopleReached"]):
        add(f"m{i}", name, stat=(cs(name), "Sum"))
    add("hookby", "${PROP('Dim.Reason')}",
        expr="SEARCH('{Countersign,Hook,Reason} MetricName=\"HookDenied\"', 'Sum', 86400)")
    add("refby", "${PROP('Dim.By')}",
        expr="SEARCH('{Countersign,By,Kind} MetricName=\"SenderRefused\"', 'Sum', 86400)")
    add("gateby", "${PROP('Dim.Outcome')}",
        expr="SEARCH('{Countersign,Outcome,Stage} MetricName=\"GateOutcome\"', 'Sum', 86400)")
    add("lat50", "API latency p50 (ms)", stat=(cs("ApiLatency"), "p50"))
    add("lat95", "API latency p95 (ms)", stat=(cs("ApiLatency"), "p95"))
    add("agentdur", "Agent run p50 (ms)", stat=(cs("AgentDuration"), "p50"))
    for i, fn in enumerate(FUNCTIONS):
        dim = [{"Name": "FunctionName", "Value": fn}]
        add(f"inv{i}", f"{fn} invocations", stat=({"Namespace": "AWS/Lambda", "MetricName": "Invocations",
                                                  "Dimensions": dim}, "Sum"))
        add(f"err{i}", f"{fn} errors", stat=({"Namespace": "AWS/Lambda", "MetricName": "Errors", "Dimensions": dim}, "Sum"))
        add(f"dur{i}", f"{fn} p95 ms", stat=({"Namespace": "AWS/Lambda", "MetricName": "Duration", "Dimensions": dim}, "p95"))
    if PAGING_SM:
        dim = [{"Name": "StateMachineArn", "Value": PAGING_SM}]
        for k in ("ExecutionsStarted", "ExecutionsSucceeded", "ExecutionsFailed", "ExecutionsTimedOut"):
            add(f"sfn{k}", k, stat=({"Namespace": "AWS/States", "MetricName": k, "Dimensions": dim}, "Sum"))
    if HTTP_API_ID:
        dim = [{"Name": "ApiId", "Value": HTTP_API_ID}]
        for k, st in (("Count", "Sum"), ("4xx", "Sum"), ("5xx", "Sum")):
            add(f"gw{k}", f"API Gateway {k}", stat=({"Namespace": "AWS/ApiGateway", "MetricName": k, "Dimensions": dim}, st))
    groups = {"hookby": "hook_denied_by", "refby": "sender_refused_by", "gateby": "gate_by"}
    acc: dict = {}
    token = None
    while True:
        kw = {"MetricDataQueries": q, "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
        if token:
            kw["NextToken"] = token
        r = cw.get_metric_data(**kw)
        for m in r["MetricDataResults"]:
            grp, name = (groups[m["Id"]], m["Label"]) if m["Id"] in groups else ("metrics", labels[m["Id"]])
            bucket = acc.setdefault(grp, {}).setdefault(name, {})
            for t, v in zip(m["Timestamps"], m["Values"]):
                day = t.strftime("%Y-%m-%d")
                bucket[day] = round(bucket.get(day, 0) + v, 1)
        token = r.get("NextToken")
        if not token:
            break
    series = {g: {n: [{"day": d, "v": v} for d, v in sorted(b.items())] for n, b in names.items()}
              for g, names in acc.items()}
    alarms = []
    for page in cw.get_paginator("describe_alarms").paginate(AlarmNamePrefix="cs-", AlarmTypes=["MetricAlarm", "CompositeAlarm"]):
        for a in page["MetricAlarms"] + page.get("CompositeAlarms", []):
            alarms.append({"name": a["AlarmName"], "state": a["StateValue"], "description": a.get("AlarmDescription", ""),
                           "since": a.get("StateUpdatedTimestamp")})
    body = {"window_days": 14, "generated_at": int(time.time()), "series": series, "alarms": alarms,
            "dashboard": "CloudWatch dashboard: Countersign", "cached": False}
    _status_cache.update(at=time.time(), body=body)
    return body


ROUTES = [(r"/api/presets/[a-z0-9-]+", "/api/presets/{id}"), (r"/api/runs/run_[a-f0-9]{16}/decide", "/api/runs/{id}/decide"),
          (r"/api/runs/run_[a-f0-9]{16}", "/api/runs/{id}")]


def route_name(path: str) -> str:
    for pat, name in ROUTES:
        if re.fullmatch(pat, path):
            return name
    return path if path.count("/") <= 3 and len(path) < 40 else "other"


def handler(event, context):
    t0 = time.time()
    out = _route(event, context)
    path = event.get("rawPath", "/")
    metric("ApiRequest", Route=route_name(path), Status=str(out.get("statusCode", 0))[0] + "xx")
    metric("ApiLatency", round((time.time() - t0) * 1000, 1), "Milliseconds", Route=route_name(path))
    return out


# ---------- router ----------

def _route(event, _context):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    q = event.get("queryStringParameters") or {}
    try:
        if path == "/api/health":
            return resp(200, {"ok": True, "service": "countersign", "version": "1.0.0"})
        if path == "/api/presets" and method == "GET":
            return resp(200, {"presets": list_presets()})
        m = re.fullmatch(r"/api/presets/([a-z0-9-]+)", path)
        if m and method == "GET":
            return resp(200, load_preset(m.group(1)),
                        {"content-disposition": f'inline; filename="countersign-{m.group(1)}.json"'})
        if path == "/api/evaluate" and method == "POST":
            if not bump(f"ip-{ip_of(event)}", DAILY_IP_EVALS):
                return err(429, "Daily limit reached for this address. Get an API key for more.")
            return resp(200, do_evaluate(body_of(event)))
        if path == "/api/keys" and method == "POST":
            if not bump(f"keys-{ip_of(event)}", 5):
                return err(429, "Five keys a day per address.")
            b = body_of(event)
            key = new_api_key()
            table(T_KEYS).put_item(Item={"key_hash": hash_key(key), "label": str(b.get("label", ""))[:60],
                                         "created": now(), "calls": 0, "prefix": key[:14]})
            return resp(201, {"api_key": key, "daily_limit": KEY_DAILY,
                              "note": "Shown once. Countersign stores only a SHA-256 hash of it."})
        if path == "/v1/evaluate" and method == "POST":
            item, e = check_key(event)
            if e:
                return e
            out = do_evaluate(body_of(event))
            out["usage"] = {"calls": int(item.get("calls", 0)) + 1, "daily_limit": KEY_DAILY}
            return resp(200, out)
        if path == "/v1/usage" and method == "GET":
            item, e = check_key(event)
            if e:
                return e
            return resp(200, {"prefix": item.get("prefix"), "calls": item.get("calls"), "created": item.get("created")})
        if path == "/api/runs" and method == "POST":
            return resp(201, start_run(body_of(event), event))
        m = re.fullmatch(r"/api/runs/(run_[a-f0-9]{16})", path)
        if m and method == "GET":
            return resp(200, get_run(m.group(1)))
        m = re.fullmatch(r"/api/runs/(run_[a-f0-9]{16})/decide", path)
        if m and method == "POST":
            return resp(200, decide(m.group(1), body_of(event)))
        if path == "/api/debate" and method == "POST":
            if not bump("debates", 300):
                return err(429, "Today's debate budget is used up.")
            from debate import debate as run_debate
            b = body_of(event)
            ev = do_evaluate(b)
            w, jur, prop, _ = build_case(b)
            summary = {"event": w.en.event if w else "none", "sender": w.en.sender_name if w else "none",
                       "official_source": bool(w and w.official), "headline": w.en.headline if w else "",
                       "urgency": w.en.urgency if w else "", "severity": w.en.severity if w else "",
                       "certainty": w.en.certainty if w else ""}
            people = ev["decision"]["facts"].get("people_reached_est") or ev["decision"]["facts"].get("people_if_code_used")
            dbt = run_debate(summary, prop, jur.name, people)
            return resp(200, {"debate": dbt, "rules": ev["decision"]})
        if path == "/api/live" and method == "GET":
            jur = load_jurisdiction(str(q.get("county", "48265")))
            feats = nws_active_for_county(jur.geoid)
            return resp(200, {"county": jur.name, "geoid": jur.geoid, "warnings": [{
                "id": f["properties"].get("id"), "event": f["properties"].get("event"),
                "headline": f["properties"].get("headline"), "sent": f["properties"].get("sent"),
                "expires": f["properties"].get("expires"), "has_polygon": bool(f.get("geometry"))} for f in feats]})
        if path == "/api/status" and method == "GET":
            return resp(200, status(), {"cache-control": "public, max-age=30"})
        if path == "/api/results" and method == "GET":
            out = {}
            for p in RESULTS.glob("*.json"):
                out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            return resp(200, out)
        return err(404, "No such route. See /api/health and the API page.")
    except PermissionError as e:
        return err(403, str(e))
    except KeyError as e:
        return err(404, f"Not found: {e}")
    except (ValueError, json.JSONDecodeError) as e:
        return err(400, str(e))
    except Exception as e:
        traceback.print_exc()
        return err(500, "Internal error", detail=type(e).__name__)
