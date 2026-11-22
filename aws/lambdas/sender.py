"""The only code that can send a live alert.

This function runs under its own IAM role, the only role allowed to publish to
the live topic. The agent's roles cannot invoke it. Before it publishes, two
independent checkers must agree:
  1. the Countersign gate (Python), re-run here on records read from storage,
  2. Amazon Bedrock Automated Reasoning checks, a solver holding the release
     rules as formal logic.
If either says no, nothing is sent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3  # noqa: E402
from boto3.dynamodb.conditions import Key  # noqa: E402

from common import T_APPROVALS, T_DRAFTS, T_LEDGER, event_sk, from_ddb, load_preset, log_event, metric, now, table, to_ddb  # noqa: E402
from countersign.cap import from_openfema  # noqa: E402
from countersign.gate import evaluate  # noqa: E402
from countersign.geo import load_jurisdiction  # noqa: E402

LIVE_TOPIC = os.environ.get("LIVE_TOPIC_ARN", "")
AR_GUARDRAIL = os.environ.get("AR_GUARDRAIL_ID", "")
AR_VERSION = os.environ.get("AR_GUARDRAIL_VERSION", "DRAFT")


def facts_sentence(d: dict, kind: str) -> str:
    f, c = d["facts"], {x["rule"]: x for x in d["checks"]}
    parts = []
    parts.append("The alert matches an official National Weather Service warning." if f.get("sourced")
                 else "The alert does not match any official National Weather Service warning.")
    parts.append(f"{d['approvals_present']} distinct officers approved the release.")
    parts.append("The release was requested by an officer, not by the agent.")
    parts.append("A correction is staged." if c.get("R6", {}).get("status") == "pass" or kind == "correction"
                 else "No correction is staged.")
    area_ok = c.get("R3", {}).get("status") in ("pass", "warn")
    parts.append("The alert area is defined and inside the jurisdiction." if area_ok
                 else "The alert area is missing or outside the jurisdiction.")
    parts.append("This warning has already been sent live for this jurisdiction."
                 if c.get("R10", {}).get("status") == "fail" else "This warning has not been sent live before.")
    ev = c.get("R5", {}).get("evidence") or {}
    parts.append(f"The alert class is {f.get('alert_class', 'Public Safety')}.")
    if ev:
        parts.append(f"The warning urgency is {ev.get('urgency')}, severity is {ev.get('severity')}, "
                     f"and certainty is {ev.get('certainty')}.")
    return " ".join(parts)


def ar_check(d: dict, kind: str) -> dict:
    if not AR_GUARDRAIL:
        # Fails closed: a sender deployed without its solver cannot release anything. (It used to skip the check.)
        metric("SafetyCheckUnavailable", Check="solver")
        return {"status": "NOT_CONFIGURED", "detail": "The Automated Reasoning guardrail is not configured."}
    rt = boto3.client("bedrock-runtime")
    query = "Facts: " + facts_sentence(d, kind) + " Question: may this live alert be released?"
    answer = "Yes. The live alert may be released."
    try:
        r = rt.apply_guardrail(guardrailIdentifier=AR_GUARDRAIL, guardrailVersion=AR_VERSION, source="OUTPUT",
                               content=[{"text": {"text": query, "qualifiers": ["query"]}},
                                        {"text": {"text": answer, "qualifiers": ["guard_content"]}}])
    except Exception as e:
        print("AR_ERROR", repr(e))
        metric("SafetyCheckUnavailable", Check="solver")
        return {"status": "ERROR", "detail": type(e).__name__}
    findings = []
    for a in r.get("assessments", []):
        for f in (a.get("automatedReasoningPolicy") or {}).get("findings", []):
            findings.append(f)
    kinds = [next(iter(f.keys())) for f in findings]
    status = "VALID" if kinds and all(k == "valid" for k in kinds) else (kinds[0].upper() if kinds else "NO_FINDINGS")
    return {"status": status, "findings": kinds, "request_id": r.get("ResponseMetadata", {}).get("RequestId"),
            "query": query}


def handler(event, _context):
    if event.get("probe"):  # health probe used by evals/chaos.py to see whether a fault is active; touches nothing
        return {"probe": "ok"}
    run_id, draft_id, kind = event["run_id"], event["draft_id"], event.get("kind", "alert")
    drafts = table(T_DRAFTS)
    cfg = from_ddb(drafts.get_item(Key={"pk": f"RUN#{run_id}", "sk": "CONFIG"})["Item"])
    dr = from_ddb(drafts.get_item(Key={"pk": f"RUN#{run_id}", "sk": f"DRAFT#{draft_id}"})["Item"])
    approvals = [from_ddb(x) for x in table(T_APPROVALS).query(
        KeyConditionExpression=Key("pk").eq(f"DRAFT#{draft_id}"))["Items"]]
    approvals = [a for a in approvals if a.get("kind", "alert") == kind]
    ledger = table(T_LEDGER)
    sent_keys = {x["sk"] for x in ledger.query(KeyConditionExpression=Key("pk").eq(f"SENT#{run_id}"))["Items"]}
    jur = load_jurisdiction(cfg["county_geoid"])
    w = from_openfema(load_preset(cfg["preset_id"])["record"]) if cfg.get("preset_id") else None
    if kind == "correction":
        corr = dr.get("correction") or {}
        p = dict(dr["proposal"], text_360=corr.get("text_360", ""), text_90=corr.get("text_90", ""),
                 warning_id=None, alert_class="Public Safety", correction_staged=True, action_ids=[])
        d = evaluate(dict(p, approvals=approvals), None, jur).to_dict()
        # A correction must go fast: one officer, and the same area as the alert it corrects.
        ok = (bool(approvals) and dr.get("status") == "sent" and not any(
            c["status"] == "fail" and c["hard"] and c["rule"] in ("R3",) for c in d["checks"]))
        d["approvals_required"] = 1
        d["outcome"] = "send_ok" if ok else "blocked"
        ar = {"status": "NOT_REQUIRED", "detail": "Corrections need one officer and the original area."}
    else:
        p = dict(dr["proposal"], approvals=approvals, channel="live", requested_by="officer",
                 whole_area_confirmed_by=event.get("whole_area_confirmed_by"))
        d = evaluate(p, w, jur, sent_registry=sent_keys, local_places=cfg.get("local_places") or []).to_dict()
        ar = ar_check(d, kind) if d["outcome"] == "send_ok" else {"status": "NOT_RUN", "detail": "Gate said no."}
        ok = d["outcome"] == "send_ok" and ar["status"] == "VALID"
    result = {"sent": False, "decision": d, "automated_reasoning": ar}
    metric("SendAttempt", Kind=kind)
    claim = None
    if ok:
        # One alert per warning (R10), safe under concurrency: claim the slot with a conditional write before
        # publishing. Two approvals landing in the same instant both pass the gate's registry read above; only
        # one can win this write. Found by the concurrency test in evals/scale.py.
        claim = {"pk": f"SENT#{run_id}", "sk": (f"{p.get('warning_id') or 'unsourced'}|{jur.geoid}" if kind == "alert"
                                               else f"correction|{draft_id}")}
        try:
            ledger.put_item(Item=to_ddb(dict(claim, draft_id=draft_id, at=now(), state="claimed")),
                            ConditionExpression="attribute_not_exists(pk)")
        except ledger.meta.client.exceptions.ConditionalCheckFailedException:
            ok, claim = False, None
            result["refused_by"] = "R10: another send for this warning won the race"
            d["outcome"] = "blocked"
    if not ok:
        refused_by = result.get("refused_by") or ("gate" if d["outcome"] != "send_ok" else "automated_reasoning")
        result["refused_by"] = refused_by
        metric("SenderRefused", Kind=kind, By=refused_by.split(":")[0])
        log_event("sender_refused", run_id=run_id, draft_id=draft_id, kind=kind, by=refused_by,
                  failed=[c["rule"] for c in d["checks"] if c["status"] == "fail"], solver=ar.get("status"))
    if ok:
        text = p["text_360"]
        msg = {"run_id": run_id, "draft_id": draft_id, "channel": "live", "kind": kind, "text": text,
               "text_90": p.get("text_90"), "text_es": p.get("text_360_es"), "polygon": p.get("polygon"),
               "county": jur.name, "people_reached_est": d["facts"].get("people_reached_est"),
               "officers": sorted({a["officer"] for a in approvals})}
        try:
            mid = boto3.client("sns").publish(TopicArn=LIVE_TOPIC, Message=json.dumps(msg),
                                              MessageAttributes={"channel": {"DataType": "String", "StringValue": "live"}}
                                              )["MessageId"] if LIVE_TOPIC else "no-topic"
        except Exception:
            ledger.delete_item(Key=claim)  # nothing went out, so release the slot for a retry
            metric("LiveSendFailed", Kind=kind)
            raise
        ledger.update_item(Key=claim, UpdateExpression="SET #s = :s, message_id = :m",
                           ExpressionAttributeNames={"#s": "state"}, ExpressionAttributeValues={":s": "sent", ":m": mid})
        metric("LiveSent", Kind=kind)
        metric("PeopleReached", d["facts"].get("people_reached_est") or 0, "None", Kind=kind)
        log_event("live_sent", run_id=run_id, draft_id=draft_id, kind=kind, message_id=mid,
                  officers=msg["officers"], county=jur.geoid)
        drafts.update_item(Key={"pk": f"RUN#{run_id}", "sk": f"DRAFT#{draft_id}"},
                           UpdateExpression="SET #s = :s, sent_at = :n",
                           ExpressionAttributeNames={"#s": "status"},
                           ExpressionAttributeValues={":s": "sent" if kind == "alert" else "corrected", ":n": now()})
        result.update({"sent": True, "message_id": mid})
    drafts.put_item(Item=to_ddb({"pk": f"RUN#{run_id}", "sk": event_sk(f"send-{draft_id}-{kind}"),
                                 "kind": "send_attempt", "body": result, "at": now()}))
    return result
