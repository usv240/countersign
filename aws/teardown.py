"""Remove every AWS resource Countersign created. Reads aws/state.json.

    python aws/teardown.py           # dry run: lists what would be deleted
    python aws/teardown.py --yes     # deletes it. Permanent: tables, logs and the site go with it.

CloudFront distributions must be disabled before they can be deleted, which takes
about 15 minutes; run the script a second time to finish that step.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
STATE = Path(__file__).resolve().parent / "state.json"
P = "cs"
S = boto3.Session(region_name=REGION)
DO = "--yes" in sys.argv


def step(what: str, fn, *a, **k):
    if not DO:
        print("would delete:", what)
        return
    try:
        fn(*a, **k)
        print("deleted:", what)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"skipped: {what} ({code})")


def main():
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    acc, bed = S.client("bedrock-agentcore-control"), S.client("bedrock")
    lam, sfn, sns, ddb = S.client("lambda"), S.client("stepfunctions"), S.client("sns"), S.client("dynamodb")
    iam, api, s3, cf, logs = S.client("iam"), S.client("apigatewayv2"), S.client("s3"), S.client("cloudfront"), S.client("logs")

    if st.get("harness_id"):
        step(f"harness {st['harness_id']}", acc.delete_harness, harnessId=st["harness_id"])
    if st.get("gateway_id"):
        if st.get("gateway_target_id"):
            step("gateway target", acc.delete_gateway_target, gatewayIdentifier=st["gateway_id"], targetId=st["gateway_target_id"])
            if DO:
                time.sleep(10)
        step(f"gateway {st['gateway_id']}", acc.delete_gateway, gatewayIdentifier=st["gateway_id"])
    if st.get("policy_engine_id"):
        for pid in (st.get("policies") or {}).values():
            step(f"policy {pid}", acc.delete_policy, policyEngineId=st["policy_engine_id"], policyId=pid)
        if DO:
            time.sleep(10)
        step(f"policy engine {st['policy_engine_id']}", acc.delete_policy_engine, policyEngineId=st["policy_engine_id"])
    for key in ("agent_guardrail_id", "ar_guardrail_id"):
        if st.get(key):
            step(f"guardrail {st[key]}", bed.delete_guardrail, guardrailIdentifier=st[key])
    if st.get("ar_policy_arn"):
        step("automated reasoning policy", bed.delete_automated_reasoning_policy, policyArn=st["ar_policy_arn"], force=True)

    for fn in ("tools", "hook", "sender", "api", "runner", "pager", "escalated", "recorder"):
        step(f"lambda {P}-{fn}", lam.delete_function, FunctionName=f"{P}-{fn}")
        step(f"log group /aws/lambda/{P}-{fn}", logs.delete_log_group, logGroupName=f"/aws/lambda/{P}-{fn}")
    if st.get("paging_sm_arn"):
        step("state machine", sfn.delete_state_machine, stateMachineArn=st["paging_sm_arn"])
    for k, arn in (st.get("topics") or {}).items():
        step(f"sns topic {k}", sns.delete_topic, TopicArn=arn)
    for t in ("drafts", "approvals", "ledger", "keys"):
        step(f"table {P}-{t}", ddb.delete_table, TableName=f"{P}-{t}")
    if st.get("http_api_id"):
        step("http api", api.delete_api, ApiId=st["http_api_id"])

    for name in (st.get("roles") or {}).values():
        rname = name.split("/")[-1]

        def drop(r=rname):
            for pol in iam.list_role_policies(RoleName=r)["PolicyNames"]:
                iam.delete_role_policy(RoleName=r, PolicyName=pol)
            iam.delete_role(RoleName=r)
        step(f"iam role {rname}", drop)

    if st.get("site_bucket"):
        def empty_and_delete(b=st["site_bucket"]):
            res = S.resource("s3").Bucket(b)
            res.objects.all().delete()
            res.delete()
        step(f"s3 bucket {st['site_bucket']}", empty_and_delete)

    if st.get("cf_id"):
        d = cf.get_distribution_config(Id=st["cf_id"])
        cfg, etag = d["DistributionConfig"], d["ETag"]
        if cfg["Enabled"]:
            def disable():
                cfg["Enabled"] = False
                cf.update_distribution(Id=st["cf_id"], IfMatch=etag, DistributionConfig=cfg)
            step("disable cloudfront (run again in ~15 minutes to delete it)", disable)
        else:
            step("cloudfront distribution", cf.delete_distribution, Id=st["cf_id"], IfMatch=etag)
    if not DO:
        print("\nDry run only. Run with --yes to delete.")


if __name__ == "__main__":
    main()
