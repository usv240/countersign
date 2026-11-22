"""Prove who can do the dangerous things, with IAM Access Analyzer's policy checks.

The policy simulator (evals/below_model.py) tries a request and reports what happened. These checks are different:
Access Analyzer reasons over the policy document itself, with automated reasoning, and answers for every possible
request at once. For each of Countersign's ten roles and each dangerous permission it asks one question:
can this policy grant it? Every answer is compared with the one role that should have it, or with none.

    python evals/permissions.py        # writes evals/results/permissions.json

Also runs CheckNoPublicAccess on every trust policy and resource policy, and ValidatePolicy on every policy.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
STATE = json.loads((ROOT / "aws" / "state.json").read_text())
OUT = ROOT / "evals" / "results" / "permissions.json"
S = boto3.Session(region_name="us-east-1")
iam, aa = S.client("iam"), S.client("accessanalyzer")
ACCOUNT = S.client("sts").get_caller_identity()["Account"]
R = "us-east-1"


def table(n: str) -> str:
    return f"arn:aws:dynamodb:{R}:{ACCOUNT}:table/{n}"


# What could end in a live alert, a silenced desk or rewritten rules, and the only role meant to hold it.
ACCESS = [
    {"id": "live", "what": "Publish to the live alert channel", "only": ["sender"],
     "access": {"actions": ["sns:Publish"], "resources": [STATE["topics"]["live"]]}},
    {"id": "sender", "what": "Invoke the sender, the only code that publishes live", "only": ["api"],
     "access": {"actions": ["lambda:InvokeFunction", "lambda:InvokeAsync"], "resources": [STATE["lambdas"]["cs-sender"]]}},
    {"id": "approve", "what": "Write, change or delete an officer's approval", "only": ["api"],
     "access": {"actions": ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
                            "dynamodb:PartiQLInsert", "dynamodb:PartiQLUpdate", "dynamodb:PartiQLDelete"],
                "resources": [table("cs-approvals")]}},
    {"id": "stop_paging", "what": "End the paging (complete or stop the workflow)", "only": ["api"],
     "access": {"actions": ["states:SendTaskSuccess", "states:SendTaskFailure", "states:StopExecution"]}},
    {"id": "silence", "what": "Switch off the alarms or the paging channel", "only": [],
     "access": {"actions": ["cloudwatch:DisableAlarmActions", "cloudwatch:DeleteAlarms", "cloudwatch:SetAlarmState",
                            "cloudwatch:PutMetricAlarm", "cloudwatch:PutCompositeAlarm", "sns:Unsubscribe",
                            "sns:DeleteTopic", "sns:SetTopicAttributes", "states:UpdateStateMachine",
                            "states:DeleteStateMachine"]}},
    {"id": "rewrite", "what": "Change its own rules, code or permissions", "only": [],
     "access": {"actions": ["bedrock:UpdateGuardrail", "bedrock:DeleteGuardrail", "bedrock:CreateGuardrailVersion",
                            "bedrock:UpdateAutomatedReasoningPolicy", "bedrock:DeleteAutomatedReasoningPolicy",
                            "bedrock-agentcore:UpdateHarness", "bedrock-agentcore:DeleteHarness",
                            "bedrock-agentcore:UpdateGateway", "bedrock-agentcore:UpdateGatewayTarget",
                            "bedrock-agentcore:UpdatePolicy", "bedrock-agentcore:DeletePolicy",
                            "bedrock-agentcore:UpdatePolicyEngine", "lambda:UpdateFunctionCode",
                            "lambda:UpdateFunctionConfiguration", "lambda:AddPermission", "iam:PutRolePolicy",
                            "iam:AttachRolePolicy", "iam:DeleteRolePolicy", "iam:UpdateAssumeRolePolicy",
                            "iam:CreatePolicyVersion", "iam:PassRole"]}},
]


def role_policies(role_arn: str) -> tuple[list[tuple[str, str]], str]:
    name = role_arn.split("/")[-1]
    docs = []
    for pn in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        docs.append((f"inline:{pn}", json.dumps(iam.get_role_policy(RoleName=name, PolicyName=pn)["PolicyDocument"])))
    for ap in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
        v = iam.get_policy(PolicyArn=ap["PolicyArn"])["Policy"]["DefaultVersionId"]
        docs.append((f"managed:{ap['PolicyName']}", json.dumps(
            iam.get_policy_version(PolicyArn=ap["PolicyArn"], VersionId=v)["PolicyVersion"]["Document"])))
    trust = json.dumps(iam.get_role(RoleName=name)["Role"]["AssumeRolePolicyDocument"])
    return docs, trust


def call(fn, **kw):
    for i in range(6):
        try:
            return fn(**kw)
        except aa.exceptions.ThrottlingException:
            time.sleep(2 ** i)
    return fn(**kw)


def main() -> dict:
    roles = STATE["roles"]
    pol = {k: role_policies(v) for k, v in roles.items()}
    matrix, wrong = [], 0
    for a in ACCESS:
        row = {"id": a["id"], "what": a["what"], "only": a["only"], "roles": {}}
        for k in roles:
            docs, _ = pol[k]
            granted, reasons = False, []
            for label, doc in docs:
                r = call(aa.check_access_not_granted, policyDocument=doc, access=[a["access"]],
                         policyType="IDENTITY_POLICY")
                if r["result"] == "FAIL":
                    granted = True
                    reasons += [x.get("description", "") for x in r.get("reasons", [])][:2]
            expected = k in a["only"]
            ok = granted == expected
            wrong += 0 if ok else 1
            row["roles"][k] = {"granted": granted, "expected": expected, "ok": ok, "reasons": reasons}
        matrix.append(row)
        print(a["id"], {k: ("can" if v["granted"] else "no") + ("" if v["ok"] else " WRONG") for k, v in row["roles"].items()})

    # nothing public: every role's trust policy and every resource policy on the path to a live alert
    public = []
    for k, (_, trust) in pol.items():
        r = call(aa.check_no_public_access, policyDocument=trust, resourceType="AWS::IAM::AssumeRolePolicyDocument")
        public.append({"resource": f"{k} role trust policy", "result": r["result"]})
    sns = S.client("sns")
    for t in ("live", "pager", "test"):
        doc = sns.get_topic_attributes(TopicArn=STATE["topics"][t])["Attributes"].get("Policy")
        if doc:
            r = call(aa.check_no_public_access, policyDocument=doc, resourceType="AWS::SNS::Topic")
            public.append({"resource": f"{t} topic policy", "result": r["result"]})
    lam = S.client("lambda")
    for fn in ("cs-sender", "cs-tools", "cs-hook", "cs-api"):
        try:
            doc = lam.get_policy(FunctionName=fn)["Policy"]
        except lam.exceptions.ResourceNotFoundException:
            public.append({"resource": f"{fn} function policy", "result": "PASS", "note": "no resource policy"})
            continue
        r = call(aa.check_no_public_access, policyDocument=doc, resourceType="AWS::Lambda::Function")
        public.append({"resource": f"{fn} function policy", "result": r["result"]})
    try:
        doc = S.client("s3").get_bucket_policy(Bucket=STATE["site_bucket"])["Policy"]
        r = call(aa.check_no_public_access, policyDocument=doc, resourceType="AWS::S3::Bucket")
        public.append({"resource": "site bucket policy (CloudFront only)", "result": r["result"]})
    except Exception as e:
        public.append({"resource": "site bucket policy", "result": "ERROR", "note": type(e).__name__})
    for p in public:
        print("public:", p)

    # lint every policy document
    lint = {"ERROR": 0, "SECURITY_WARNING": 0, "WARNING": 0, "SUGGESTION": 0}
    lint_rows = []
    for k, (docs, trust) in pol.items():
        for label, doc in docs + [("trust", trust)]:
            kw = {"policyDocument": doc, "policyType": "IDENTITY_POLICY" if label != "trust" else "RESOURCE_POLICY"}
            if label == "trust":
                kw["validatePolicyResourceType"] = "AWS::IAM::AssumeRolePolicyDocument"
            fs = call(aa.validate_policy, **kw)["findings"]
            for f in fs:
                lint[f["findingType"]] = lint.get(f["findingType"], 0) + 1
                lint_rows.append({"role": k, "policy": label, "type": f["findingType"], "issue": f["issueCode"],
                                  "detail": f["findingDetails"][:200]})
    print("validate:", lint)
    res = {"measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "method": "IAM Access Analyzer CheckAccessNotGranted on every policy of every role; CheckNoPublicAccess; "
                     "ValidatePolicy",
           "roles": list(roles), "matrix": matrix, "checks": len(matrix) * len(roles), "wrong": wrong,
           "public": public, "lint": lint, "lint_findings": lint_rows}
    OUT.write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = main()
    print(f"{r['checks'] - r['wrong']} of {r['checks']} role and permission pairs as intended")
    sys.exit(1 if r["wrong"] else 0)
