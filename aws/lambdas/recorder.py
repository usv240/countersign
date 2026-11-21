"""Records what reached each mock channel, so the website can show the phones.

Subscribed to the test and live SNS topics. The live topic stands in for the
real phone alert system (IPAWS), which needs a FEMA agreement to reach.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import T_LEDGER, now, table, to_ddb  # noqa: E402


def handler(event, _context):
    for r in event.get("Records", []):
        msg = json.loads(r["Sns"]["Message"])
        channel = msg.get("channel", "test")
        table(T_LEDGER).put_item(Item=to_ddb({
            "pk": f"CHANNEL#{msg.get('run_id', 'none')}", "sk": f"{now():012d}#{channel}#{r['Sns']['MessageId']}",
            "channel": channel, "body": msg, "topic": r["Sns"]["TopicArn"].split(":")[-1]}))
    return {"ok": True}
