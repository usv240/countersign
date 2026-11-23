"""Records what reached each mock channel, so the website can show the phones.

Subscribed to the test and live SNS topics. The live topic stands in for the
real phone alert system (IPAWS), which needs a FEMA agreement to reach.

It is also the witness at the phone end. For every live alert that arrives, it
looks up the officers' approvals for that draft on its own, without trusting the
message or the sender, and counts the alert as signed or unsigned. The public
tally on the website reads those counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from boto3.dynamodb.conditions import Key  # noqa: E402

from common import T_APPROVALS, T_LEDGER, metric, now, table, to_ddb  # noqa: E402


def signed(draft_id: str, kind: str) -> bool:
    """True when an officer's approval of this kind was on record for the draft when the alert arrived."""
    if not draft_id:
        return False
    items = table(T_APPROVALS).query(KeyConditionExpression=Key("pk").eq(f"DRAFT#{draft_id}"))["Items"]
    return any(i.get("kind", "alert") == kind and i.get("decision") == "approve" for i in items)


def witness(msg: dict) -> None:
    ok = signed(str(msg.get("draft_id") or ""), msg.get("kind", "alert"))
    table(T_LEDGER).update_item(Key={"pk": "TALLY", "sk": "live"},
                                UpdateExpression="ADD arrived :one, #w :one SET since = if_not_exists(since, :n)",
                                ExpressionAttributeNames={"#w": "signed" if ok else "unsigned"},
                                ExpressionAttributeValues={":one": 1, ":n": now()})
    if not ok:
        metric("LiveUnsigned")  # an alarm watches this; it has never fired
        print("LIVE_UNSIGNED", json.dumps({k: msg.get(k) for k in ("run_id", "draft_id", "kind")}))


def handler(event, _context):
    for r in event.get("Records", []):
        msg = json.loads(r["Sns"]["Message"])
        channel = msg.get("channel", "test")
        table(T_LEDGER).put_item(Item=to_ddb({
            "pk": f"CHANNEL#{msg.get('run_id', 'none')}", "sk": f"{now():012d}#{channel}#{r['Sns']['MessageId']}",
            "channel": channel, "body": msg, "topic": r["Sns"]["TopicArn"].split(":")[-1]}))
        # Judged by the topic it arrived on, not by what the message says about itself.
        if r["Sns"]["TopicArn"].split(":")[-1].endswith("-live-channel"):
            witness(msg)
    return {"ok": True}
