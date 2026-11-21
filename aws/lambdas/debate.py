"""Two models argue send versus hold, a third concludes. Shown next to the rules.

This is the baseline the project measures itself against, not part of the
decision. Two models agreeing is still an opinion; the rules decide. The debate
is never shown on the approval screen (rule R9).

Research note: multi-agent debate does not reliably beat a single model
(Smit et al., "Should we be going MAD?", ICML 2024, arXiv:2311.17371).
"""
from __future__ import annotations

import json
import os
import re

import boto3

SEND_MODEL = os.environ.get("DEBATE_SEND_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
HOLD_MODEL = os.environ.get("DEBATE_HOLD_MODEL", "us.amazon.nova-pro-v1:0")
JUDGE_MODEL = os.environ.get("DEBATE_JUDGE_MODEL", "us.anthropic.claude-sonnet-4-6")

BRIEF = """You are in a structured debate at a county emergency office about whether this phone alert should go out
to the public right now. Argue your assigned side as strongly and honestly as you can, in at most 90 words. Point to
specific facts in the warning and the draft. No headings, no lists."""


def _converse(model: str, system: str, prompt: str, max_tokens: int = 300) -> str:
    rt = boto3.client("bedrock-runtime")
    r = rt.converse(modelId=model, system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": prompt}]}],
                    inferenceConfig={"maxTokens": max_tokens, "temperature": 0.4})
    return "".join(b.get("text", "") for b in r["output"]["message"]["content"]).strip()


def case_text(warning_summary: dict, proposal: dict, county: str, people: int | None) -> str:
    return json.dumps({"county": county, "warning": warning_summary, "draft_alert": {
        "text": proposal.get("text_360"), "short_text": proposal.get("text_90"), "class": proposal.get("alert_class"),
        "area": "drawn polygon" if proposal.get("polygon") else ("county code only" if proposal.get("geocodes") else "none"),
        "people_reached_estimate": people, "correction_ready": bool(proposal.get("correction_staged"))}}, indent=1)


def debate(warning_summary: dict, proposal: dict, county: str, people: int | None) -> dict:
    case = case_text(warning_summary, proposal, county, people)
    send1 = _converse(SEND_MODEL, BRIEF + " Your side: SEND IT NOW.", "The case:\n" + case)
    hold1 = _converse(HOLD_MODEL, BRIEF + " Your side: HOLD IT.", "The case:\n" + case + "\n\nThe other side said:\n" + send1)
    send2 = _converse(SEND_MODEL, BRIEF + " Your side: SEND IT NOW. This is your rebuttal.",
                      "The case:\n" + case + "\n\nThe other side said:\n" + hold1)
    verdict_raw = _converse(JUDGE_MODEL, "You moderate a debate at a county emergency office. Read the case and the "
                            "arguments. Reply with JSON only: {\"verdict\": \"SEND\" or \"HOLD\", \"reason\": \"one "
                            "sentence\"}.",
                            f"The case:\n{case}\n\nSend side:\n{send1}\n\nHold side:\n{hold1}\n\nSend rebuttal:\n{send2}",
                            max_tokens=200)
    m = re.search(r"\{.*\}", verdict_raw, re.S)
    try:
        verdict = json.loads(m.group(0)) if m else {}
    except ValueError:
        verdict = {}
    return {"models": {"send": SEND_MODEL, "hold": HOLD_MODEL, "moderator": JUDGE_MODEL},
            "turns": [{"side": "send", "model": SEND_MODEL, "text": send1},
                      {"side": "hold", "model": HOLD_MODEL, "text": hold1},
                      {"side": "send", "model": SEND_MODEL, "text": send2}],
            "conclusion": {"verdict": (verdict.get("verdict") or "UNCLEAR").upper(),
                           "reason": verdict.get("reason") or verdict_raw[:300]}}
