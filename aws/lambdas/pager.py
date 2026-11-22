"""Pages one person and parks the workflow until a human decides.

Called by the Step Functions paging workflow with a task token. The token is
stored where only the approval service can read it; only an officer's decision
(through that service) completes it. If nobody answers before the timeout, the
workflow pages the next person. The agent has no Step Functions permission.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3  # noqa: E402

from common import T_LEDGER, metric, now, table, to_ddb  # noqa: E402

PAGER_TOPIC = os.environ.get("PAGER_TOPIC_ARN", "")


def handler(event, _context):
    run_id, contact = event["run_id"], event["contact"]
    page = {"run_id": run_id, "contact": contact, "round": event.get("round", 1),
            "warning": event.get("warning_title", ""), "at": now(),
            "text": f"Countersign page to {contact['name']} ({contact['role']}): an official warning needs a "
                    f"decision. Open the review page."}
    table(T_LEDGER).put_item(Item=to_ddb({"pk": f"PAGING#{run_id}", "sk": "CURRENT", "token": event["token"],
                                          "contact": contact, "round": page["round"], "at": now()}))
    table(T_LEDGER).put_item(Item=to_ddb({"pk": f"CHANNEL#{run_id}", "sk": f"{now():012d}#page#{contact['name']}",
                                          "channel": "pager", "body": page}))
    if PAGER_TOPIC:
        boto3.client("sns").publish(TopicArn=PAGER_TOPIC, Message=json.dumps(page))
    metric("PageSent", Round=str(page["round"]))
    return {"paged": contact["name"]}


def escalated(event, _context):
    """Last step when the call list has been tried every round: escalate to the state warning point."""
    run_id = event["run_id"]
    table(T_LEDGER).put_item(Item=to_ddb({"pk": f"CHANNEL#{run_id}", "sk": f"{now():012d}#page#state",
                                          "channel": "pager", "body": {
                                              "contact": {"name": "State warning point", "role": "24-hour state desk"},
                                              "text": "Nobody at the county answered. The warning and the draft "
                                                      "are escalated to the state warning point.", "at": now()}}))
    metric("Escalated")
    return {"escalated": True}
