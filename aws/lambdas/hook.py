"""before_tool_call hook for the AgentCore harness. Fails closed.

Runs before every tool call the model makes, outside the model, with its own
IAM role (read-only). It denies anything that is not one of the six tools, any
argument that tries to reach the live channel or carry an opinion to the
officer, and any area or class value outside the allowed set.
"""
from __future__ import annotations

import json
import os
import re

import boto3

GUARDRAIL_ID = os.environ.get("AGENT_GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("AGENT_GUARDRAIL_VERSION", "DRAFT")

ALLOWED = {"list_warnings", "get_warning", "draft_alert", "publish_test", "stage_correction", "submit_for_review"}
AREAS = {"warning_polygon", "whole_county"}
CLASSES = {"Imminent Threat", "Public Safety"}
LIVE = re.compile(r"(\"channel\"\s*:\s*\"live\"|send[_ ]?live|requested_by|approv(e|al)s?\"\s*:|officer\"\s*:)", re.I)
OPINION = re.compile(r"\b(i |we )?(recommend|suggest|advise|urge)\b|\byou should\b|\bshould (be )?(sent|approved|go out)\b"
                     r"|\b(approve|send) (it|this|now)\b|\bsafe to send\b", re.I)
RUN = re.compile(r"run_[a-f0-9]{16}")


def deny(reason: str) -> dict:
    print(json.dumps({"hook": "deny", "reason": reason}))
    return {"decision": "deny", "reason": f"Blocked by the Countersign tool-call check: {reason}"}


def last_user_text(ctx: dict) -> str:
    for m in reversed(ctx.get("messages") or []):
        if m.get("role") == "user":
            return " ".join(b.get("text", "") for b in m.get("content") or [] if isinstance(b, dict) and b.get("text"))
    return ""


def before_invocation(ctx: dict) -> dict:
    """Bedrock Guardrails on what the person typed, and only that.

    Attached to the model instead, the prompt attack filter also reads the system prompt, which is a page of
    instructions by design, and blocks every request (found in testing; see FAILURES.md)."""
    if ctx.get("truncated"):
        return deny("the request was too large to check.")
    text = last_user_text(ctx)
    if not text or not GUARDRAIL_ID:
        return {"decision": "allow"}
    r = boto3.client("bedrock-runtime").apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VERSION, source="INPUT",
        content=[{"text": {"text": text[:20000]}}])
    if r.get("action") == "GUARDRAIL_INTERVENED":
        found = []
        for a in r.get("assessments", []):
            found += [f["type"] for f in (a.get("contentPolicy") or {}).get("filters", []) if f.get("action") == "BLOCKED"]
            found += [t["name"] for t in (a.get("topicPolicy") or {}).get("topics", []) if t.get("action") == "BLOCKED"]
        return deny("Bedrock Guardrails blocked this message (" + ", ".join(found or ["policy"]) + ").")
    return {"decision": "allow"}


def handler(event, _context):
    ctx = (event or {}).get("context") or {}
    kind = (event or {}).get("event")
    if kind == "before_invocation":
        return before_invocation(ctx)
    if kind != "before_tool_call":
        return {"decision": "allow"}
    raw_name = str(ctx.get("toolName") or "")
    name = raw_name.split("___")[-1].split("/")[-1]
    args = ctx.get("toolInput")
    if isinstance(args, dict) and args.get("truncated"):
        return deny("the tool input was too large to check.")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return deny("the tool input was not valid JSON.")
    args = args or {}
    if name not in ALLOWED:
        return deny(f"'{raw_name}' is not one of the six Countersign tools.")
    blob = json.dumps(args)
    if LIVE.search(blob):
        return deny("tool arguments may not set a channel, an approval or an officer. Only officers can send live.")
    if not RUN.fullmatch(str(args.get("run_id", ""))):
        return deny("missing or malformed run_id.")
    if name == "draft_alert":
        if args.get("area") not in AREAS:
            return deny("area must be warning_polygon or whole_county.")
        if args.get("alert_class") not in CLASSES:
            return deny("alert_class must be Imminent Threat or Public Safety.")
        if len(str(args.get("text_360", ""))) > 360 or len(str(args.get("text_90", ""))) > 90:
            return deny("alert text is over the federal length limit.")
    if name == "submit_for_review":
        concerns = " ".join(str(c) for c in (args.get("concerns") or []))
        if OPINION.search(concerns):
            return deny("concerns must state facts for the officer, not a recommendation (rule R9).")
    return {"decision": "allow"}
