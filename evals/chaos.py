"""Break the deployed stack on purpose and check the two promises still hold:
nothing reaches the live channel without an officer, and a warning is never left unanswered.

Faults come from two places:
  * AWS Fault Injection Service (FIS) experiments, using its Lambda extension, make a function fail or stall on
    real invocations, exactly as an outage would look to everything that calls it.
  * An explicit IAM deny, attached for the length of one test and then removed, takes a dependency away from one
    role: the Automated Reasoning solver, the input guardrail, the send ledger.

    python evals/chaos.py setup     # FIS config bucket, experiment role, extension layer on the target functions
    python evals/chaos.py run       # every experiment; writes evals/results/chaos.json
    python evals/chaos.py run sender_down pager_down   # only some
    python evals/chaos.py clean     # remove the layer, the variables, the bucket, the roles' read access

Every experiment ends with recovery: the fault is removed and the same action is tried again.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
os.environ.setdefault("AWS_REGION", "us-east-1")

import boto3  # noqa: E402
import requests  # noqa: E402
from boto3.dynamodb.conditions import Key  # noqa: E402

from scale import approved_draft  # noqa: E402  (also puts aws/lambdas on sys.path)
from common import T_DRAFTS, T_LEDGER, table  # noqa: E402

STATE = json.loads((ROOT / "aws" / "state.json").read_text())
OUT = ROOT / "evals" / "results" / "chaos.json"
R = "us-east-1"
S = boto3.Session(region_name=R)
lam, iam, fis, s3, sfn, cw, ssm = (S.client(x) for x in ("lambda", "iam", "fis", "s3", "stepfunctions", "cloudwatch", "ssm"))
ACCOUNT = S.client("sts").get_caller_identity()["Account"]
BUCKET = f"cs-fis-config-{ACCOUNT}"
PREFIX = "FisConfigs/"
FIS_ROLE = "cs-fis-role"
TARGETS = {"cs-sender": "sender", "cs-pager": "pager", "cs-escalated": "pager", "cs-hook": "hook"}
SITE = STATE["site_url"]
CONTACTS = [{"name": "Officer A", "role": "Duty officer"}, {"name": "Officer B", "role": "Deputy coordinator"},
            {"name": "Officer C", "role": "Emergency management coordinator"}]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wait_updated(fn):
    lam.get_waiter("function_updated_v2").wait(FunctionName=fn)


# ---------------- setup and clean ----------------

def setup():
    try:
        s3.create_bucket(Bucket=BUCKET)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "fis.amazonaws.com"},
             "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}]}
    try:
        iam.create_role(RoleName=FIS_ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Description="Countersign chaos tests: FIS writes Lambda fault configurations")
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    iam.put_role_policy(RoleName=FIS_ROLE, PolicyName="fis-lambda", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:DeleteObject"], "Resource": f"arn:aws:s3:::{BUCKET}/{PREFIX}*"},
            {"Effect": "Allow", "Action": ["lambda:GetFunction"], "Resource": [STATE["lambdas"][f] for f in TARGETS]},
            {"Effect": "Allow", "Action": ["tag:GetResources"], "Resource": "*"}]}))
    read = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{BUCKET}",
         "Condition": {"StringLike": {"s3:prefix": [f"{PREFIX}*"]}}},
        {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{BUCKET}/{PREFIX}*"}]}
    for role in set(TARGETS.values()):
        iam.put_role_policy(RoleName=STATE["roles"][role].split("/")[-1], PolicyName="fis-chaos-read",
                            PolicyDocument=json.dumps(read))
    layer = ssm.get_parameter(Name="/aws/service/fis/lambda-extension/AWS-FIS-extension-x86_64/1.x.x")["Parameter"]["Value"]
    for fn in TARGETS:
        cfg = lam.get_function_configuration(FunctionName=fn)
        env = (cfg.get("Environment") or {}).get("Variables", {})
        env.update(AWS_FIS_CONFIGURATION_LOCATION=f"arn:aws:s3:::{BUCKET}/{PREFIX}",
                   AWS_LAMBDA_EXEC_WRAPPER="/opt/aws-fis/bootstrap", AWS_FIS_SLOW_POLL_INTERVAL_SECONDS="20",
                   # these functions often run for a few milliseconds; without a wait the extension's S3 poll is
                   # frozen mid-request and never completes (its log says "Connection error ... ListObjectsV2")
                   AWS_FIS_POLL_MAX_WAIT_MILLISECONDS="1500",
                   AWS_FIS_LOG_LEVEL="WARN")
        layers = [x["Arn"] for x in cfg.get("Layers", []) if "aws-fis-extension" not in x["Arn"]] + [layer]
        lam.update_function_configuration(FunctionName=fn, Layers=layers, Environment={"Variables": env})
        wait_updated(fn)
        log("FIS extension on", fn)
    time.sleep(10)
    for fn in TARGETS:  # every function still answers normally with the extension in place
        assert probe(fn)[0], f"{fn} fails with the extension and no fault"
    log("setup done")


def clean():
    for fn in TARGETS:
        cfg = lam.get_function_configuration(FunctionName=fn)
        env = {k: v for k, v in (cfg.get("Environment") or {}).get("Variables", {}).items() if not k.startswith("AWS_FIS")
               and k != "AWS_LAMBDA_EXEC_WRAPPER"}
        # the wrapper variable goes first, in the same update as the layer, so no invocation runs without one
        lam.update_function_configuration(FunctionName=fn, Environment={"Variables": env},
                                          Layers=[x["Arn"] for x in cfg.get("Layers", []) if "aws-fis-extension" not in x["Arn"]])
        wait_updated(fn)
        log("FIS extension removed from", fn)
    for role in set(TARGETS.values()):
        for pn in ("fis-chaos-read",) + tuple(DENIES):
            try:
                iam.delete_role_policy(RoleName=STATE["roles"][role].split("/")[-1], PolicyName=pn)
            except iam.exceptions.NoSuchEntityException:
                pass
    for t in fis.list_experiment_templates().get("experimentTemplates", []):
        if (t.get("tags") or {}).get("project") == "countersign":
            fis.delete_experiment_template(id=t["id"])
    try:
        for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=o["Key"])
        s3.delete_bucket(Bucket=BUCKET)
    except Exception as e:
        log("bucket", type(e).__name__)
    try:
        iam.delete_role_policy(RoleName=FIS_ROLE, PolicyName="fis-lambda")
        iam.delete_role(RoleName=FIS_ROLE)
    except iam.exceptions.NoSuchEntityException:
        pass
    log("clean done")


# ---------------- faults ----------------

def probe(fn: str) -> tuple[bool, float]:
    t = time.time()
    try:
        r = lam.invoke(FunctionName=fn, Payload=json.dumps({"probe": 1}).encode())
        ok = not r.get("FunctionError")
    except Exception:
        ok = False
    return ok, time.time() - t


class FisFault:
    """An FIS experiment against some functions, active until stopped."""

    def __init__(self, fns: list[str], action: str, params: dict, label: str):
        self.fns, self.action, self.params, self.label = fns, action, params, label
        self.delay = action.endswith("add-delay")

    def __enter__(self):
        role = iam.get_role(RoleName=FIS_ROLE)["Role"]["Arn"]
        self.tpl = fis.create_experiment_template(
            clientToken=uuid.uuid4().hex, description=f"Countersign chaos: {self.label}", roleArn=role,
            stopConditions=[{"source": "none"}], tags={"project": "countersign"},
            targets={"fns": {"resourceType": "aws:lambda:function", "selectionMode": "ALL",
                             "resourceArns": [STATE["lambdas"][f] for f in self.fns]}},
            actions={"fault": {"actionId": self.action, "targets": {"Functions": "fns"},
                               "parameters": dict(self.params, duration="PT10M")}})["experimentTemplate"]["id"]
        self.exp = fis.start_experiment(clientToken=uuid.uuid4().hex, experimentTemplateId=self.tpl,
                                        tags={"project": "countersign"})["experiment"]["id"]
        t0 = time.time()
        while time.time() - t0 < 240:  # wait until a real invocation shows the fault
            ok, took = probe(self.fns[0])
            if (self.delay and took > 9) or (not self.delay and not ok):
                break
            time.sleep(3)
        else:
            fis.stop_experiment(id=self.exp)
            fis.delete_experiment_template(id=self.tpl)
            raise RuntimeError(f"fault {self.label} never became active")
        self.active_after_s = round(time.time() - t0, 1)
        log("fault active:", self.label, self.exp, f"after {self.active_after_s}s")
        return self

    def __exit__(self, *exc):
        try:
            fis.stop_experiment(id=self.exp)
        except Exception as e:
            log("stop", type(e).__name__)
        t0 = time.time()
        while time.time() - t0 < 240:
            st = fis.get_experiment(id=self.exp)["experiment"]["state"]["status"]
            if st in ("stopped", "completed", "failed") and all(probe(f)[0] and probe(f)[1] < 9 for f in self.fns):
                break
            time.sleep(4)
        self.cleared_after_s = round(time.time() - t0, 1)
        fis.delete_experiment_template(id=self.tpl)
        log("fault cleared:", self.label, f"after {self.cleared_after_s}s")

    def evidence(self) -> dict:
        return {"method": "AWS FIS experiment", "action": self.action, "experiment_id": self.exp,
                "functions": self.fns, "fault_active_after_s": self.active_after_s}


DENIES = {
    "chaos-deny-solver": ("sender", ["bedrock:ApplyGuardrail", "bedrock:InvokeAutomatedReasoningPolicy"]),
    "chaos-deny-guardrail": ("hook", ["bedrock:ApplyGuardrail"]),
    "chaos-deny-ledger": ("sender", ["dynamodb:PutItem", "dynamodb:UpdateItem"]),
}


class IamFault:
    """An explicit deny on one role, for the length of one test. A deny beats every allow."""

    def __init__(self, name: str):
        self.name = name
        self.role, self.actions = DENIES[name]
        self.role_name = STATE["roles"][self.role].split("/")[-1]

    def __enter__(self):
        res = "*" if self.name != "chaos-deny-ledger" else f"arn:aws:dynamodb:{R}:{ACCOUNT}:table/cs-ledger"
        iam.put_role_policy(RoleName=self.role_name, PolicyName=self.name, PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Action": self.actions, "Resource": res}]}))
        time.sleep(20)  # IAM is eventually consistent; new denies reach Lambda within seconds
        log("deny attached:", self.name)
        return self

    def __exit__(self, *exc):
        iam.delete_role_policy(RoleName=self.role_name, PolicyName=self.name)
        time.sleep(20)
        log("deny removed:", self.name)

    def evidence(self) -> dict:
        return {"method": "explicit IAM deny on one role", "role": self.role_name, "denied": self.actions}


# ---------------- what we observe ----------------

def channel(run: str) -> list[dict]:
    return table(T_LEDGER).query(KeyConditionExpression=Key("pk").eq(f"CHANNEL#{run}"))["Items"]


def live_count(run: str) -> int:
    return sum(1 for x in channel(run) if x.get("channel") == "live")


def decide(run: str, did: str, officer="Officer A") -> dict:
    r = requests.post(f"{SITE}/api/runs/{run}/decide", timeout=60, json={
        "draft_id": did, "officer": officer, "decision": "approve", "kind": "alert", "officer_token": "x"})
    return {"http": r.status_code, **(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}


def wait_live(run: str, want: int, secs: int = 20) -> int:
    t0 = time.time()
    while time.time() - t0 < secs:
        n = live_count(run)
        if n >= want:
            return n
        time.sleep(2)
    return live_count(run)


def end_paging(run: str) -> None:
    it = table(T_LEDGER).get_item(Key={"pk": f"PAGING#{run}", "sk": "CURRENT"}).get("Item") or {}
    if it.get("token"):
        try:
            sfn.send_task_success(taskToken=it["token"], output=json.dumps({"why": "chaos test finished"}))
        except Exception:
            pass


def start_agent(note: str) -> dict:
    r = requests.post(f"{SITE}/api/runs", json={"preset_id": "kerr-2025-0114", "page_timeout": 60}, timeout=30)
    r.raise_for_status()
    log("agent run", r.json()["run_id"], note)
    return r.json()


def watch_agent(run: str, secs: int = 240) -> dict:
    t0, first_page = time.time(), None
    while time.time() - t0 < secs:
        items = table(T_DRAFTS).query(KeyConditionExpression=Key("pk").eq(f"RUN#{run}"))["Items"]
        cfg = next((i for i in items if i["sk"] == "CONFIG"), {})
        pages = [x for x in channel(run) if x.get("channel") == "pager"]
        if pages and first_page is None:
            first_page = round(time.time() - t0, 1)
        if cfg.get("agent_status") in ("done", "error") and first_page is not None:
            break
        time.sleep(3)
    items = table(T_DRAFTS).query(KeyConditionExpression=Key("pk").eq(f"RUN#{run}"))["Items"]
    cfg = next((i for i in items if i["sk"] == "CONFIG"), {})
    ev = [i for i in items if i["sk"].startswith("EVENT#")]
    ch = channel(run)
    return {"agent_status": cfg.get("agent_status"), "drafts": sum(1 for i in items if i["sk"].startswith("DRAFT#")),
            "tool_calls": sum(1 for e in ev if e.get("kind") == "tool_call"),
            "hook_events": sum(1 for e in ev if e.get("kind") == "hook"),
            "test_messages": sum(1 for x in ch if x.get("channel") == "test"),
            "live_messages": sum(1 for x in ch if x.get("channel") == "live"),
            "pages": sum(1 for x in ch if x.get("channel") == "pager"), "first_page_s": first_page}


# ---------------- experiments ----------------

def sender_down() -> dict:
    run, did = approved_draft()
    with FisFault(["cs-sender"], "aws:lambda:invocation-error", {"preventExecution": "true"}, "the sender fails") as f:
        during = decide(run, did)
        live_during = wait_live(run, 1, 10)
    after = decide(run, did)
    again = decide(run, did)  # a second tap after recovery
    live_after = wait_live(run, 1, 20)
    time.sleep(5)
    live_after = live_count(run)
    s = during.get("send") or {}
    return {"fault": f.evidence(), "run_id": run,
            "during": {"http": during["http"], "sent": s.get("sent"), "error": s.get("error"),
                       "told_officer": (s.get("decision") or {}).get("summary"), "live_messages": live_during},
            "after_recovery": {"first_retry_sent": (after.get("send") or {}).get("sent"),
                               "second_retry_sent": (again.get("send") or {}).get("sent"),
                               "second_retry_refused_by": (again.get("send") or {}).get("refused_by"),
                               "live_messages": live_after},
            "held": live_during == 0 and s.get("sent") is False and live_after == 1,
            "expect": "No live alert during the outage, the officer is told and the approval kept; after recovery "
                      "one retry sends exactly one alert and a second tap is refused.",
            "alarms": ["cs-sender-errors", "cs-check-unavailable", "cs-unsafe"]}


def pager_down() -> dict:
    run = "run_" + uuid.uuid4().hex[:16]
    with FisFault(["cs-pager", "cs-escalated"], "aws:lambda:invocation-error", {"preventExecution": "true"},
                  "both paging functions fail") as f:
        t0 = time.time()
        arn = sfn.start_execution(stateMachineArn=STATE["paging_sm_arn"], name=run, input=json.dumps(
            {"run_id": run, "warning_title": "Chaos test: flash flood warning", "contacts": CONTACTS, "timeout": 20,
             "max_rounds": 1}))["executionArn"]
        page_s = None
        while time.time() - t0 < 240:
            if any(x.get("body", {}).get("last_resort") for x in channel(run)) and page_s is None:
                page_s = round(time.time() - t0, 1)
            st = sfn.describe_execution(executionArn=arn)["status"]
            if st != "RUNNING" and page_s is not None:
                break
            time.sleep(2)
        d = sfn.describe_execution(executionArn=arn)
        path = [e["stateEnteredEventDetails"]["name"] for e in
                sfn.get_execution_history(executionArn=arn, maxResults=200)["events"] if "stateEnteredEventDetails" in e]
    return {"fault": f.evidence(), "run_id": run, "execution_status": d["status"], "execution_error": d.get("error"),
            "last_resort_page_s": page_s, "states": path,
            "held": page_s is not None and d["status"] == "FAILED",
            "expect": "With every paging function down, the workflow itself records and sends a page to the state "
                      "desk through SNS, then fails loudly so the paging-failed alarm fires.",
            "alarms": ["cs-paging-failed", "cs-unsafe"]}


def _hook_fault(action: str, params: dict, label: str) -> dict:
    with FisFault(["cs-hook"], action, params, label) as f:
        run = start_agent(label)["run_id"]
        seen = watch_agent(run)
    end_paging(run)
    return {"fault": f.evidence(), "run_id": run, **seen,
            "held": seen["drafts"] == 0 and seen["test_messages"] == 0 and seen["live_messages"] == 0
            and seen["pages"] >= 1,
            "alarms": ["cs-hook-errors", "cs-unsafe"]}


def hook_down() -> dict:
    r = _hook_fault("aws:lambda:invocation-error", {"preventExecution": "true"}, "the tool-call check fails")
    r["expect"] = ("The harness's hooks fail closed: the agent drafts nothing and publishes nothing, while paging, "
                   "which does not depend on the agent, still wakes the officer.")
    return r


def hook_slow() -> dict:
    r = _hook_fault("aws:lambda:invocation-add-delay", {"startupDelayMilliseconds": "15000"},
                    "the tool-call check takes 15 seconds (its limit is 10)")
    r["expect"] = "A slow check is a failed check: past its 10-second limit the hook denies, so the agent does nothing."
    return r


def solver_unreachable() -> dict:
    run, did = approved_draft()
    with IamFault("chaos-deny-solver") as f:
        during = decide(run, did)
        live_during = wait_live(run, 1, 10)
    after = decide(run, did)
    live_after = wait_live(run, 1, 20)
    s = during.get("send") or {}
    return {"fault": f.evidence(), "run_id": run,
            "during": {"sent": s.get("sent"), "refused_by": s.get("refused_by"),
                       "solver": (s.get("automated_reasoning") or {}).get("status"), "live_messages": live_during},
            "after_recovery": {"sent": (after.get("send") or {}).get("sent"),
                               "solver": ((after.get("send") or {}).get("automated_reasoning") or {}).get("status"),
                               "live_messages": live_after},
            "held": live_during == 0 and s.get("sent") is False and live_after == 1,
            "expect": "An approved, rule-passing alert is still refused when the solver cannot be asked; it goes out "
                      "once the solver is back.",
            "alarms": ["cs-check-unavailable", "cs-sender-refused", "cs-unsafe"]}


def guardrail_unreachable() -> dict:
    with IamFault("chaos-deny-guardrail") as f:
        run = start_agent("input guardrail unreachable")["run_id"]
        seen = watch_agent(run)
    end_paging(run)
    return {"fault": f.evidence(), "run_id": run, **seen,
            "held": seen["drafts"] == 0 and seen["live_messages"] == 0 and seen["pages"] >= 1,
            "expect": "When the input guardrail cannot be reached, the before-invocation hook refuses the request "
                      "instead of waving it through. Before this round it would have allowed it.",
            "alarms": ["cs-check-unavailable", "cs-unsafe"]}


def retry_until_sent(run: str, did: str, cap: int = 240) -> dict:
    """After a fault is removed, try again every 20 seconds, as an officer would. A removed IAM deny can take a
    minute or more to reach a function that is already running (its credentials are cached)."""
    t0, tries = time.time(), 0
    while True:
        tries += 1
        r = (decide(run, did).get("send") or {})
        if r.get("sent") or time.time() - t0 > cap:
            return {"sent": bool(r.get("sent")), "tries": tries, "seconds_after_fault_removed": round(time.time() - t0)}
        time.sleep(20)


# Removing an explicit deny took over four minutes to reach DynamoDB authorization for the sender's role in the
# run of 2026-11-22 (it sent on a retry about 12 minutes later), so recovery is reported, not required for "held".
def ledger_unwritable() -> dict:
    run, did = approved_draft()
    with IamFault("chaos-deny-ledger") as f:
        during = decide(run, did)
        again = decide(run, did)  # the officer tries again while the fault is still there
        live_during = wait_live(run, 1, 10)
    after = retry_until_sent(run, did)
    live_after = wait_live(run, 1, 20)
    time.sleep(5)
    live_after = live_count(run)
    s = during.get("send") or {}
    return {"fault": f.evidence(), "run_id": run,
            "during": {"sent": s.get("sent"), "error": s.get("error"), "second_try_sent": (again.get("send") or {}).get("sent"),
                       "live_messages": live_during},
            "after_recovery": dict(after, live_messages=live_after),
            "held": live_during == 0 and s.get("sent") is False,
            "expect": "If the sender cannot record the send, it cannot claim the warning, so it does not publish.",
            "alarms": ["cs-sender-errors", "cs-check-unavailable", "cs-unsafe"]}


EXPERIMENTS = {"sender_down": sender_down, "pager_down": pager_down, "hook_down": hook_down, "hook_slow": hook_slow,
               "solver_unreachable": solver_unreachable, "guardrail_unreachable": guardrail_unreachable,
               "ledger_unwritable": ledger_unwritable}


def alarm_transitions(since: float) -> list[dict]:
    out = []
    names = [a["AlarmName"] for a in cw.describe_alarms(AlarmNamePrefix="cs-", AlarmTypes=["MetricAlarm", "CompositeAlarm"])[
        "MetricAlarms"]] + [a["AlarmName"] for a in cw.describe_alarms(AlarmNamePrefix="cs-", AlarmTypes=["CompositeAlarm"])[
        "CompositeAlarms"]]
    import datetime as dt
    start = dt.datetime.fromtimestamp(since, dt.timezone.utc)
    for n in sorted(set(names)):
        for h in cw.describe_alarm_history(AlarmName=n, HistoryItemType="StateUpdate", StartDate=start, MaxRecords=50,
                                           AlarmTypes=["MetricAlarm", "CompositeAlarm"])["AlarmHistoryItems"]:
            d = json.loads(h["HistoryData"])
            if d.get("newState", {}).get("stateValue") == "ALARM":
                out.append({"alarm": n, "at": h["Timestamp"].timestamp()})
    return sorted(out, key=lambda x: x["at"])


# The alarms each fault should raise. cs-unsafe is the OR of sender-errors, hook-errors, paging-failed, dlq and
# check-unavailable, so it is in ALARM whenever one of those is.
EXPECT = {"sender_down": ["cs-sender-errors", "cs-check-unavailable"], "pager_down": ["cs-paging-failed"],
          "hook_down": ["cs-hook-errors"], "hook_slow": ["cs-hook-errors"],
          "solver_unreachable": ["cs-check-unavailable", "cs-sender-refused"],
          "guardrail_unreachable": ["cs-check-unavailable"], "ledger_unwritable": ["cs-sender-errors"]}
ORDER = ["sender_down", "pager_down", "hook_down", "solver_unreachable", "hook_slow", "ledger_unwritable",
         "guardrail_unreachable"]


def states(names: list[str]) -> dict:
    r = cw.describe_alarms(AlarmNames=names, AlarmTypes=["MetricAlarm", "CompositeAlarm"])
    return {a["AlarmName"]: a["StateValue"] for a in r["MetricAlarms"] + r["CompositeAlarms"]}


def wait_quiet(names: list[str], cap: int = 600) -> None:
    """Give the alarms this fault should raise up to ten minutes to return to OK. A custom metric that stops
    reporting keeps its alarm in ALARM while the last datapoint is inside CloudWatch's evaluation range, which can
    be about 25 minutes; an alarm still firing at the start is recorded as such rather than waited out."""
    t0 = time.time()
    while time.time() - t0 < cap:
        bad = [n for n, v in states(names).items() if v == "ALARM"]
        if not bad:
            return
        log("waiting for alarms to clear:", bad)
        time.sleep(30)


def alarm_report(e: dict, tr: list[dict]) -> dict:
    out = {}
    for a in e.get("alarms_expected", []):
        t = next((x["at"] for x in tr if x["alarm"] == a and e["started"] <= x["at"] <= e["started"] + e["seconds"] + 600), None)
        if t:
            out[a] = round(t - e["started"])
        elif (e.get("alarm_state_at_start") or {}).get(a) == "ALARM":
            out[a] = "already firing"
        else:
            out[a] = None
    return out


SIGNAL = {
    "sender_down": ("AWS/Lambda", "Errors", {"FunctionName": "cs-sender"}),
    "pager_down": ("AWS/States", "ExecutionsFailed", {"StateMachineArn": STATE["paging_sm_arn"]}),
    "hook_down": ("AWS/Lambda", "Errors", {"FunctionName": "cs-hook"}),
    "hook_slow": ("AWS/Lambda", "Errors", {"FunctionName": "cs-hook"}),
    "solver_unreachable": ("Countersign", "SafetyCheckUnavailable", {"Check": "solver"}),
    "guardrail_unreachable": ("Countersign", "SafetyCheckUnavailable", {"Check": "guardrail"}),
    "ledger_unwritable": ("AWS/Lambda", "Errors", {"FunctionName": "cs-sender"}),
}


def signal(name: str, e: dict) -> dict:
    import datetime as dt
    ns, metric, dims = SIGNAL[name]
    t0 = dt.datetime.fromtimestamp(e["started"] - 60, dt.timezone.utc)
    t1 = dt.datetime.fromtimestamp(e["started"] + e["seconds"] + 60, dt.timezone.utc)
    r = cw.get_metric_statistics(Namespace=ns, MetricName=metric, StartTime=t0, EndTime=t1, Period=60, Statistics=["Sum"],
                                 Dimensions=[{"Name": k, "Value": v} for k, v in dims.items()])
    return {"metric": f"{ns} {metric} {list(dims.values())[0].split(':')[-1]}",
            "count": int(sum(p["Sum"] for p in r["Datapoints"]))}


def unsafe_during(e: dict, tr: list[dict]) -> bool:
    if (e.get("alarm_state_at_start") or {}).get("cs-unsafe") == "ALARM":
        return True
    return any(x["alarm"] == "cs-unsafe" and e["started"] <= x["at"] <= e["started"] + e["seconds"] + 600 for x in tr)


def finish(cur: dict) -> dict:
    tr = alarm_transitions(min(e["started"] for e in cur["experiments"].values() if "started" in e) - 60)
    for name, e in cur["experiments"].items():
        if "started" in e:
            e["alarm_seconds"] = alarm_report(e, tr)
            e["cs_unsafe_in_alarm"] = unsafe_during(e, tr)
            e["signal"] = signal(name, e)
    exps = cur["experiments"].values()
    cur["summary"] = {"experiments": len(cur["experiments"]), "held": sum(1 for e in exps if e.get("held")),
                      "live_alerts_during_faults": sum(int((e.get("during") or {}).get("live_messages", e.get("live_messages", 0)) or 0)
                                                       for e in exps)}
    OUT.write_text(json.dumps(cur, indent=1, default=str))
    log("summary", cur["summary"])
    return cur


def run(names: list[str]) -> dict:
    cur = json.loads(OUT.read_text()) if OUT.exists() else {"experiments": {}}
    for n in names:
        exp = EXPECT.get(n, [])
        wait_quiet(exp)
        log("=== experiment", n)
        at_start = states(exp + ["cs-unsafe"])
        t0 = time.time()
        try:
            res = EXPERIMENTS[n]()
        except Exception as e:
            res = {"held": None, "error": f"{type(e).__name__}: {e}"[:400]}
        res.pop("alarms", None)
        res.update(started=t0, seconds=round(time.time() - t0, 1), alarms_expected=exp, alarm_state_at_start=at_start,
                   measured_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t0)))
        log(n, "held" if res.get("held") else "DID NOT HOLD", json.dumps({k: v for k, v in res.items()
                                                                          if k not in ("states",)}, default=str)[:900])
        cur["experiments"][n] = res
        OUT.write_text(json.dumps(cur, indent=1, default=str))
    log("waiting 6 minutes for the last alarms to evaluate")
    time.sleep(360)
    return finish(cur)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "setup":
        setup()
    elif cmd == "clean":
        clean()
    elif cmd == "alarms":
        finish(json.loads(OUT.read_text()))
    else:
        run(sys.argv[2:] or ORDER)
