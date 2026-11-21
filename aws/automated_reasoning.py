"""The release rules as formal logic, for Amazon Bedrock Automated Reasoning checks.

Written by hand as an exact policy definition (variables and SMT-LIB rules), not
extracted from a document, so the logic is exactly what the rules file says.
The solver is the second, independent checker before a live send: the Python
gate and the solver must both agree.

    python aws/automated_reasoning.py create     # create or update policy, version, guardrail
    python aws/automated_reasoning.py probe      # run the probe suite against the guardrail
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3

REGION = "us-east-1"
NAME = "countersign-live-release"
GUARDRAIL = "countersign-release-check"
STATE = Path(__file__).resolve().parent / "state.json"

VARIABLES = [
    ("matchesOfficialWarning", "BOOL", "True if the alert matches an official National Weather Service warning that "
                                       "was fetched by code. False if there is no such official warning."),
    ("distinctOfficerApprovals", "INT", "The number of distinct officers who approved the release of the alert."),
    ("requestedByAgent", "BOOL", "True if the release was requested by the AI agent rather than by an officer."),
    ("correctionStaged", "BOOL", "True if a correction message is staged and ready before the alert is sent."),
    ("areaDefinedInsideJurisdiction", "BOOL", "True if the alert area is defined and lies inside the jurisdiction."),
    ("alreadySentForWarning", "BOOL", "True if this warning has already been sent live for this jurisdiction."),
    ("alertClassIsImminentThreat", "BOOL", "True if the alert class is Imminent Threat. False if it is Public Safety."),
    ("urgencyImmediateOrExpected", "BOOL", "True if the warning urgency is Immediate or Expected."),
    ("severityExtremeOrSevere", "BOOL", "True if the warning severity is Extreme or Severe."),
    ("certaintyObservedOrLikely", "BOOL", "True if the warning certainty is Observed or Likely."),
    ("requiredApprovals", "INT", "The number of distinct officer approvals required to release the alert."),
    ("mayBeReleased", "BOOL", "True if the live alert may be released to the public."),
]

RULES = [
    "(=> matchesOfficialWarning (= requiredApprovals 1))",
    "(=> (not matchesOfficialWarning) (= requiredApprovals 2))",
    "(=> (and alertClassIsImminentThreat (not (and urgencyImmediateOrExpected severityExtremeOrSevere certaintyObservedOrLikely))) (not mayBeReleased))",
    "(= mayBeReleased (and (not requestedByAgent) correctionStaged areaDefinedInsideJurisdiction "
    "(not alreadySentForWarning) (>= distinctOfficerApprovals requiredApprovals) "
    "(or (not alertClassIsImminentThreat) (and urgencyImmediateOrExpected severityExtremeOrSevere certaintyObservedOrLikely))))",
    "(>= distinctOfficerApprovals 0)",
]

PROBES = [
    ("one officer, official warning, all set", "The alert matches an official National Weather Service warning. 1 "
     "distinct officers approved the release. The release was requested by an officer, not by the agent. A correction "
     "is staged. The alert area is defined and inside the jurisdiction. This warning has not been sent live before. "
     "The alert class is Imminent Threat. The warning urgency is Immediate, severity is Severe, and certainty is Likely.",
     "valid"),
    ("one officer, no official warning", "The alert does not match any official National Weather Service warning. 1 "
     "distinct officers approved the release. The release was requested by an officer, not by the agent. A correction "
     "is staged. The alert area is defined and inside the jurisdiction. This warning has not been sent live before. "
     "The alert class is Public Safety.", "invalid"),
    ("two officers, no official warning", "The alert does not match any official National Weather Service warning. 2 "
     "distinct officers approved the release. The release was requested by an officer, not by the agent. A correction "
     "is staged. The alert area is defined and inside the jurisdiction. This warning has not been sent live before. "
     "The alert class is Public Safety.", "valid"),
    ("agent requested", "The alert matches an official National Weather Service warning. 1 distinct officers approved "
     "the release. The release was requested by the agent. A correction is staged. The alert area is defined and "
     "inside the jurisdiction. This warning has not been sent live before. The alert class is Public Safety.", "invalid"),
    ("no correction", "The alert matches an official National Weather Service warning. 1 distinct officers approved "
     "the release. The release was requested by an officer, not by the agent. No correction is staged. The alert area "
     "is defined and inside the jurisdiction. This warning has not been sent live before. The alert class is Public "
     "Safety.", "invalid"),
    ("class does not qualify", "The alert matches an official National Weather Service warning. 1 distinct officers "
     "approved the release. The release was requested by an officer, not by the agent. A correction is staged. The "
     "alert area is defined and inside the jurisdiction. This warning has not been sent live before. The alert class is "
     "Imminent Threat. The warning urgency is Future, severity is Moderate, and certainty is Possible.", "invalid"),
    ("already sent", "The alert matches an official National Weather Service warning. 1 distinct officers approved "
     "the release. The release was requested by an officer, not by the agent. A correction is staged. The alert area "
     "is defined and inside the jurisdiction. This warning has already been sent live for this jurisdiction. The alert "
     "class is Public Safety.", "invalid"),
]


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=1))


def create() -> None:
    b = boto3.client("bedrock", region_name=REGION)
    st = load_state()
    definition = {"version": "1.0", "types": [],
                  "variables": [{"name": n, "type": t, "description": d} for n, t, d in VARIABLES],
                  "rules": [{"id": f"R{i:02d}XXXXXXXX"[:12], "expression": e} for i, e in enumerate(RULES, 1)]}
    for r, e in zip(definition["rules"], RULES):
        r["id"] = ("CSRULE" + str(RULES.index(e) + 1).zfill(6))[:12]
    arn = st.get("ar_policy_arn")
    if not arn:
        existing = [p for p in b.list_automated_reasoning_policies()["automatedReasoningPolicySummaries"] if p["name"] == NAME]
        arn = existing[0]["policyArn"] if existing else None
    if arn:
        b.update_automated_reasoning_policy(policyArn=arn, policyDefinition=definition, name=NAME)
    else:
        arn = b.create_automated_reasoning_policy(name=NAME, description="Countersign live alert release rules.",
                                                  policyDefinition=definition)["policyArn"]
    v = b.create_automated_reasoning_policy_version(policyArn=arn, lastUpdatedDefinitionHash=
                                                    b.get_automated_reasoning_policy(policyArn=arn)["definitionHash"])
    varn = v["policyArn"] if "policyArn" in v else f"{arn}:{v['version']}"
    st.update({"ar_policy_arn": arn, "ar_policy_version_arn": varn})
    cfg = {"policies": [varn], "confidenceThreshold": 0.5}
    gid = st.get("ar_guardrail_id")
    msg = "Countersign could not verify this release."
    if gid:
        b.update_guardrail(guardrailIdentifier=gid, name=GUARDRAIL, blockedInputMessaging=msg,
                           blockedOutputsMessaging=msg, automatedReasoningPolicyConfig=cfg,
                           crossRegionConfig={"guardrailProfileIdentifier": "us.guardrail.v1:0"})
    else:
        g = b.create_guardrail(name=GUARDRAIL, description="Second, independent check before any live send.",
                               blockedInputMessaging=msg, blockedOutputsMessaging=msg,
                               automatedReasoningPolicyConfig=cfg,
                               crossRegionConfig={"guardrailProfileIdentifier": "us.guardrail.v1:0"})
        gid = g["guardrailId"]
    time.sleep(3)
    ver = b.create_guardrail_version(guardrailIdentifier=gid, description="release check")["version"]
    st.update({"ar_guardrail_id": gid, "ar_guardrail_version": ver})
    save_state(st)
    print(json.dumps({k: st[k] for k in ("ar_policy_version_arn", "ar_guardrail_id", "ar_guardrail_version")}, indent=1))


def probe() -> list[dict]:
    st = load_state()
    rt = boto3.client("bedrock-runtime", region_name=REGION)
    rows = []
    for name, facts, expect in PROBES:
        t0 = time.time()
        r = rt.apply_guardrail(guardrailIdentifier=st["ar_guardrail_id"], guardrailVersion=st["ar_guardrail_version"],
                               source="OUTPUT", content=[
                                   {"text": {"text": "Facts: " + facts + " Question: may this live alert be released?",
                                             "qualifiers": ["query"]}},
                                   {"text": {"text": "Yes. The live alert may be released.",
                                             "qualifiers": ["guard_content"]}}])
        kinds = [next(iter(f)) for a in r.get("assessments", [])
                 for f in (a.get("automatedReasoningPolicy") or {}).get("findings", [])]
        got = "valid" if kinds and all(k == "valid" for k in kinds) else (kinds[0] if kinds else "none")
        rows.append({"probe": name, "expect": expect, "got": got, "all": kinds, "seconds": round(time.time() - t0, 1),
                     "ok": got == expect})
        print(rows[-1])
    out = Path(__file__).resolve().parents[1] / "evals" / "results" / "automated-reasoning-probes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    return rows


if __name__ == "__main__":
    {"create": create, "probe": probe}[sys.argv[1]]()
