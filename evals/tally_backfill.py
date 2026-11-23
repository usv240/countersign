"""Count the runs and live alerts recorded before the public tally existed, with the same checks the live counters use.

    python evals/tally_backfill.py

The runner and the recorder count from the day the tally was deployed. Everything before that is still in DynamoDB
(runs are kept for 7 days, the channel record for good), so this counts it once and writes the result to its own
rows, TALLY/agent-backfill and TALLY/live-backfill. It sets them rather than adding, so running it twice changes
nothing, and it will not overwrite rows already written (runs expire after 7 days, so a later count would be
smaller) unless you pass --force. The API adds the backfill rows to the live ones.

  * Runs: every run config without a message_source (runs started after the tally carry one and are counted live).
    Refusals come from the run's recorded events: hook denials, Cedar denials, tool-code refusals.
  * Live alerts: every live-channel row that arrived before the recorder became the witness, each checked against the
    approvals table on its own, exactly as the recorder does.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "aws" / "lambdas"))
from common import T_APPROVALS, T_DRAFTS, T_LEDGER, from_ddb, now  # noqa: E402

DEFAULT = "Do your job."
ATTACKS = [a["prompt"].strip() for a in json.loads((ROOT / "web" / "data" / "attacks.json").read_text(encoding="utf-8"))
           if a.get("prompt")]
db = boto3.resource("dynamodb", region_name="us-east-1")


def scan(table, **kw):
    t = db.Table(table)
    while True:
        r = t.scan(**kw)
        yield from r["Items"]
        if "LastEvaluatedKey" not in r:
            return
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def source_of(text: str) -> str:
    body = text.split(". ", 1)[1] if text.startswith("Your run_id is ") and ". " in text else text
    if body.strip().endswith(DEFAULT) and "A warning may have arrived for" in body:
        return "none"
    return "list" if any(body.strip() == a for a in ATTACKS) else "typed"


def runs() -> dict:
    by_run: dict = {}
    for i in scan(T_DRAFTS):
        if not str(i.get("pk", "")).startswith("RUN#"):
            continue
        by_run.setdefault(i["pk"], []).append(from_ddb(i))
    out = {"runs": 0, "msg_typed": 0, "msg_list": 0, "runs_refused": 0, "msg_runs_refused": 0,
           "refused_hook": 0, "refused_policy": 0, "refused_tool": 0, "since": None}
    for items in by_run.values():
        cfg = next((i for i in items if i["sk"] == "CONFIG"), None)
        if not cfg or "message_source" in cfg or cfg.get("agent_status") not in ("done", "error"):
            continue
        ev = [i for i in items if i["sk"].startswith("EVENT#")]
        hook = sum(1 for e in ev if e["kind"] == "hook" and (e.get("body") or {}).get("decision") == "deny")
        policy = tool = 0
        for e in ev:
            if e["kind"] != "tool_result":
                continue
            r = json.dumps((e.get("body") or {}).get("result", ""))
            if "AuthorizeAction" in r or "policy enforcement" in r:
                policy += 1
            elif '"error"' in r or '\\"error\\"' in r:
                tool += 1
        user = next((e for e in ev if e["kind"] == "user"), None)
        src = source_of((user.get("body") or {}).get("text", "")) if user else "none"
        refused = 1 if hook + policy + tool else 0
        out["runs"] += 1
        out["refused_hook"] += hook
        out["refused_policy"] += policy
        out["refused_tool"] += tool
        out["runs_refused"] += refused
        if src != "none":
            out[f"msg_{src}"] += 1
            out["msg_runs_refused"] += refused
        out["since"] = min(x for x in (out["since"], int(cfg.get("created") or now())) if x)
    return out


def live(cutoff: int) -> dict:
    appr = db.Table(T_APPROVALS)
    out = {"arrived": 0, "signed": 0, "unsigned": 0, "since": None, "until": cutoff, "unsigned_rows": []}
    for i in scan(T_LEDGER):
        if not str(i.get("pk", "")).startswith("CHANNEL#") or i.get("channel") != "live":
            continue
        if not str(i.get("topic", "")).endswith("-live-channel"):
            continue
        at = int(str(i["sk"]).split("#")[0])
        if at >= cutoff:
            continue
        b = from_ddb(i.get("body") or {})
        did, kind = str(b.get("draft_id") or ""), b.get("kind", "alert")
        items = appr.query(KeyConditionExpression=Key("pk").eq(f"DRAFT#{did}"))["Items"] if did else []
        ok = any(x.get("kind", "alert") == kind and x.get("decision") == "approve" for x in items)
        out["arrived"] += 1
        out["signed" if ok else "unsigned"] += 1
        if not ok:
            out["unsigned_rows"].append({"pk": i["pk"], "sk": i["sk"], "draft_id": did, "kind": kind})
        out["since"] = min(x for x in (out["since"], at) if x)
    return out


def main() -> None:
    if db.Table(T_LEDGER).get_item(Key={"pk": "TALLY", "sk": "live-backfill"}).get("Item") and "--force" not in sys.argv:
        print("Backfill rows already written; nothing changed. Pass --force to recount.")
        return
    lam = boto3.client("lambda", region_name="us-east-1")
    from datetime import datetime
    cutoff = int(datetime.fromisoformat(lam.get_function_configuration(FunctionName="cs-recorder")["LastModified"]
                                        .replace("+0000", "+00:00")).timestamp())
    a, lv = runs(), live(cutoff)
    db.Table(T_DRAFTS).put_item(Item={"pk": "TALLY", "sk": "agent-backfill", **{k: v for k, v in a.items() if v is not None},
                                      "written": now()})
    db.Table(T_LEDGER).put_item(Item={"pk": "TALLY", "sk": "live-backfill",
                                      **{k: v for k, v in lv.items() if k != "unsigned_rows" and v is not None},
                                      "written": now()})
    print(json.dumps({"agent": a, "live": lv}, indent=1, default=str))


if __name__ == "__main__":
    main()
