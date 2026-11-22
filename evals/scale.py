"""Scale and concurrency tests on the deployed stack.

    python evals/scale.py race      # 12 officers approve the same alert at once: exactly one live send
    python evals/scale.py load      # ramp the public API: 5, 10, 20 and 40 requests a second
    python evals/scale.py fanout    # a statewide storm: 50 counties paged at the same time
    python evals/scale.py all

Writes evals/results/scale.json (each test updates its own section).
`race --old <file>` runs the same race against an older copy of the sender, locally, to measure the bug it fixed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AWS_REGION", "us-east-1")
sys.path.insert(0, str(ROOT / "aws" / "lambdas"))
sys.path.insert(0, str(ROOT / "engine"))
STATE = json.loads((ROOT / "aws" / "state.json").read_text())
OUT = ROOT / "evals" / "results" / "scale.json"

import boto3  # noqa: E402
import requests  # noqa: E402
from boto3.dynamodb.conditions import Key  # noqa: E402

from common import T_APPROVALS, T_DRAFTS, T_LEDGER, hash_key, load_preset, now, table, to_ddb  # noqa: E402

S = boto3.Session(region_name="us-east-1")
lam, sfn = S.client("lambda"), S.client("stepfunctions")
SITE = STATE["site_url"]


def save(section: str, data: dict) -> None:
    cur = json.loads(OUT.read_text()) if OUT.exists() else {}
    cur[section] = dict(data, measured_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
    OUT.write_text(json.dumps(cur, indent=1))


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1)


# ---------------- race ----------------

def tool(name: str, args: dict) -> dict:
    import base64
    cc = base64.b64encode(json.dumps({"custom": {"bedrockAgentCoreToolName": f"alerts___{name}"}}).encode()).decode()
    return json.loads(lam.invoke(FunctionName="cs-tools", Payload=json.dumps(args).encode(), ClientContext=cc)["Payload"].read())


def approved_draft() -> tuple[str, str]:
    """A real, sendable draft: official Kerr County warning, correction staged, submitted, one officer approved."""
    p = load_preset("kerr-2025-0114")
    run = "run_" + uuid.uuid4().hex[:16]
    table(T_DRAFTS).put_item(Item=to_ddb({"pk": f"RUN#{run}", "sk": "CONFIG", "run_id": run, "preset_id": "kerr-2025-0114",
                                           "county_geoid": p["county_geoid"], "county_name": p["county_name"],
                                           "title": p["title"], "created": now(), "secret_hash": hash_key("x"),
                                           "local_places": [], "ttl_at": now() + 7 * 86400, "probe": "scale-race"}))
    wid = tool("list_warnings", {"run_id": run})["warnings"][0]["warning_id"]
    d = tool("draft_alert", {"run_id": run, "warning_id": wid, "area": "warning_polygon", "alert_class": "Imminent Threat",
                             "text_360": "Kerr County Emergency Management: NWS Flash Flood Warning for Kerr County until "
                                         "4:15 AM CDT. Move to higher ground now.",
                             "text_90": "NWS: Flash Flood Warning in Kerr County til 4:15 AM CDT. Move to higher ground now.",
                             "action_ids": ["flood-higher-ground"]})
    did = d["draft_id"]
    tool("stage_correction", {"run_id": run, "draft_id": did, "text_360": "Kerr County Emergency Management: the previous "
                              "flood alert was sent in error. Disregard it.", "text_90": "Disregard the previous alert. Sent in error."})
    tool("submit_for_review", {"run_id": run, "draft_id": did})
    table(T_APPROVALS).put_item(Item={"pk": f"DRAFT#{did}", "sk": "alert#Officer A", "officer": "Officer A",
                                      "decision": "approve", "kind": "alert", "at": now()})
    return run, did


def race(n: int = 12, old: str | None = None) -> dict:
    run, did = approved_draft()
    payload = {"run_id": run, "draft_id": did, "kind": "alert"}
    gate = threading.Barrier(n)
    if old:  # an older sender, run locally with the same AWS resources
        os.environ.update(LIVE_TOPIC_ARN=STATE["topics"]["live"], AR_GUARDRAIL_ID=STATE["ar_guardrail_id"],
                          AR_VERSION=str(STATE["ar_guardrail_version"]), AR_GUARDRAIL_VERSION=str(STATE["ar_guardrail_version"]))
        spec = importlib.util.spec_from_file_location("sender_old", old)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def one(_):
            gate.wait()
            return mod.handler(dict(payload), None)
    else:
        def one(_):
            gate.wait()
            return json.loads(lam.invoke(FunctionName="cs-sender", Payload=json.dumps(payload).encode())["Payload"].read())
    t0 = time.time()
    with ThreadPoolExecutor(n) as ex:
        outs = list(ex.map(one, range(n)))
    took = round(time.time() - t0, 1)
    time.sleep(6)  # the recorder writes the live channel asynchronously
    live = [x for x in table(T_LEDGER).query(KeyConditionExpression=Key("pk").eq(f"CHANNEL#{run}"))["Items"]
            if x.get("channel") == "live"]
    res = {"concurrent_approvals": n, "sender": "old copy, run locally" if old else "deployed cs-sender",
           "sent_true": sum(1 for o in outs if o.get("sent")), "live_messages_on_channel": len(live),
           "refused": sorted({o.get("refused_by") or o["decision"]["outcome"] for o in outs if not o.get("sent")}),
           "seconds": took, "run_id": run}
    print(json.dumps(res, indent=1))
    return res


# ---------------- load ----------------

def load(stages=(5, 10, 20, 40), seconds: int = 20) -> dict:
    key = requests.post(f"{SITE}/api/keys", json={"label": "scale test"}, timeout=20).json()["api_key"]
    body = json.dumps({"preset_id": "kerr-2025-0114"})
    hdr = {"content-type": "application/json", "x-api-key": key}
    results = []
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64))

    def call(_):
        t = time.time()
        try:
            r = sess.post(f"{SITE}/v1/evaluate", data=body, headers=hdr, timeout=30)
            return r.status_code, (time.time() - t) * 1000
        except requests.RequestException:
            return 0, (time.time() - t) * 1000

    for rps in stages:
        n = rps * seconds
        futs = []
        with ThreadPoolExecutor(64) as ex:
            t0 = time.time()
            for i in range(n):
                delay = t0 + i / rps - time.time()
                if delay > 0:
                    time.sleep(delay)
                futs.append(ex.submit(call, i))
            got = [f.result() for f in futs]
        ok = [ms for c, ms in got if c == 200]
        row = {"target_rps": rps, "requests": n, "ok_200": len(ok), "throttled_429": sum(1 for c, _ in got if c == 429),
               "server_5xx": sum(1 for c, _ in got if c >= 500), "network_errors": sum(1 for c, _ in got if c == 0),
               "p50_ms": pct(ok, 50), "p95_ms": pct(ok, 95), "p99_ms": pct(ok, 99),
               "mean_ms": round(statistics.mean(ok), 1) if ok else 0}
        print(row)
        results.append(row)
        time.sleep(3)
    res = {"endpoint": "POST /v1/evaluate (Kerr County preset: CAP parse, county geometry, all ten rules)",
           "path": "CloudFront -> API Gateway HTTP API -> Lambda (1536 MB)", "seconds_per_stage": seconds,
           "api_gateway_limit": "20 requests a second, burst 50 (set on purpose)", "stages": results}
    return res


# ---------------- fanout ----------------

def fanout(n: int = 50, timeout: int = 20) -> dict:
    contacts = [{"name": "Officer A", "role": "Duty officer"}, {"name": "Officer B", "role": "Deputy coordinator"},
                {"name": "Officer C", "role": "Emergency management coordinator"}]
    runs = ["run_" + uuid.uuid4().hex[:16] for _ in range(n)]
    started = {}

    def start(r):
        started[r] = time.time()
        sfn.start_execution(stateMachineArn=STATE["paging_sm_arn"], name=r, input=json.dumps(
            {"run_id": r, "warning_title": "Scale test: statewide flash flood", "contacts": contacts,
             "timeout": timeout, "max_rounds": 1}))

    with ThreadPoolExecutor(25) as ex:
        list(ex.map(start, runs))
    led = table(T_LEDGER)
    first, done = {}, {}
    deadline = time.time() + timeout * 3 + 90
    while time.time() < deadline and len(done) < n:
        for r in runs:
            if r in done:
                continue
            items = led.query(KeyConditionExpression=Key("pk").eq(f"CHANNEL#{r}"))["Items"]
            pages = [i for i in items if i.get("channel") == "pager"]
            if pages and r not in first:
                first[r] = time.time() - started[r]
            if any("#page#state" in i["sk"] for i in items):
                done[r] = {"pages": len(pages), "s": time.time() - started[r]}
        time.sleep(2)
    # end every workflow the way an officer would, through its task token, so none is left waiting
    for r in runs:
        it = led.get_item(Key={"pk": f"PAGING#{r}", "sk": "CURRENT"}).get("Item") or {}
        if it.get("token"):
            try:
                sfn.send_task_success(taskToken=it["token"], output=json.dumps({"why": "scale test finished"}))
            except Exception:
                pass
    time.sleep(5)
    for r in done:  # count once every workflow has finished, so a page still being written is not missed
        items = led.query(KeyConditionExpression=Key("pk").eq(f"CHANNEL#{r}"))["Items"]
        done[r]["pages"] = len([i for i in items if i.get("channel") == "pager"])
    fp = list(first.values())
    res = {"counties": n, "timeout_per_person_s": timeout, "first_page_p50_s": pct(fp, 50), "first_page_p95_s": pct(fp, 95),
           "first_page_max_s": round(max(fp), 1) if fp else None, "reached_first_person": len(first),
           "escalated_to_state_after_no_answer": len(done),
           "pager_entries_each": sorted({d["pages"] for d in done.values()}),
           "sequence": "Officer A, Officer B, Officer C, escalation notice, page to the state warning point",
           "escalation_p50_s": pct([d["s"] for d in done.values()], 50)}
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("race", "all"):
        if "--old" in sys.argv:
            save("race_before_fix", race(old=sys.argv[sys.argv.index("--old") + 1]))
        else:
            save("race", race())
    if what in ("load", "all"):
        save("load", load())
    if what in ("fanout", "all"):
        save("fanout", fanout())
