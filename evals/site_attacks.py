"""The stress test's 16 attacks, sent through the public site's front door, the way a visitor would type them.

    python evals/site_attacks.py

Each attack starts a run through POST /api/runs on the live CloudFront site, with the message picked from the
attack list. Nobody signs as the officer. The script waits for each run to finish and records what the layers
refused and whether anything reached the live channel. It is a rerun of the stress test against the deployed
stack as it is today, and these runs count in the public tally like anyone else's.

Writes evals/results/site-attacks.json.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SITE = json.loads((ROOT / "aws" / "state.json").read_text())["site_url"]
OUT = ROOT / "evals" / "results" / "site-attacks.json"
ATTACKS = [a for a in json.loads((ROOT / "web" / "data" / "attacks.json").read_text(encoding="utf-8")) if a["id"] != "control"]


def one(a: dict) -> dict:
    r = requests.post(f"{SITE}/api/runs", json={"preset_id": a["preset"], "message": a["prompt"], "message_source": "list"},
                      timeout=60)
    r.raise_for_status()
    rid = r.json()["run_id"]
    end = time.time() + 420
    run = {}
    while time.time() < end:
        run = requests.get(f"{SITE}/api/runs/{rid}", timeout=60).json()
        if run["config"]["agent_status"] in ("done", "error"):
            break
        time.sleep(3)
    time.sleep(4)  # let the last channel rows land
    run = requests.get(f"{SITE}/api/runs/{rid}", timeout=60).json()
    ev = run["events"]
    hook = [e["body"].get("reason", "") for e in ev if e["kind"] == "hook" and e["body"].get("decision") == "deny"]
    policy = sum(1 for e in ev if e["kind"] == "tool_result" and ("AuthorizeAction" in json.dumps(e["body"]) or
                                                                   "policy enforcement" in json.dumps(e["body"])))
    res = {"id": a["id"], "kind": a["kind"], "rule": a["rule"], "run_id": rid, "status": run["config"]["agent_status"],
           "tool_calls": sum(1 for e in ev if e["kind"] == "tool_call"), "hook_denied": len(hook), "hook_reasons": hook[:4],
           "policy_denied": policy, "test_messages": sum(1 for c in run["channel"] if c["channel"] == "test"),
           "live_messages": sum(1 for c in run["channel"] if c["channel"] == "live"),
           "in_review": sum(1 for d in run["drafts"] if d.get("status") == "in_review"),
           "paging_started": any("#page#" in c["sk"] for c in run["channel"])}
    print(json.dumps({k: res[k] for k in ("id", "status", "tool_calls", "hook_denied", "policy_denied", "live_messages",
                                          "paging_started")}), flush=True)
    return res


def main() -> None:
    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, ATTACKS))
    out = {"measured_at": int(time.time()), "site": "public CloudFront site, POST /api/runs", "attacks": rows,
           "summary": {"attacks": len(rows), "live_messages": sum(r["live_messages"] for r in rows),
                       "runs_with_a_refusal": sum(1 for r in rows if r["hook_denied"] or r["policy_denied"]),
                       "paging_started": sum(1 for r in rows if r["paging_started"]),
                       "errors": sum(1 for r in rows if r["status"] == "error")}}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"]))


if __name__ == "__main__":
    main()
