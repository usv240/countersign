"""What one warning costs to draft, from a real run's tokens and AWS's own price list.

Tokens come from the exported trace (web/data/trace.json, AgentCore Observability spans). Prices come from the AWS
Price List API at the moment you run this, for Claude Sonnet 4.6 through a US cross-region inference profile, which
is how the harness calls it. Lambda, Step Functions, DynamoDB and SNS prices are AWS's published us-east-1 on-demand
rates, applied to what one run actually does.

    python evals/cost.py      # writes evals/results/cost.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "results" / "cost.json"
TRACE = json.loads((ROOT / "web" / "data" / "trace.json").read_text())


def bedrock_prices() -> dict:
    """Per-million-token prices for Claude Sonnet 4.6, us-east-1, the cross-region (Geo) rows."""
    p = boto3.client("pricing", region_name="us-east-1")
    rows = []
    for page in p.get_paginator("get_products").paginate(
            ServiceCode="AmazonBedrockFoundationModels",
            Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": "us-east-1"}]):
        for it in page["PriceList"]:
            d = json.loads(it)
            a = d["product"]["attributes"]
            if "Sonnet 4.6" not in json.dumps(a):
                continue
            for term in d["terms"].get("OnDemand", {}).values():
                for dim in term["priceDimensions"].values():
                    rows.append({"usagetype": a.get("usagetype", ""), "desc": dim["description"],
                                 "usd": float(dim["pricePerUnit"]["USD"]), "unit": dim["unit"]})
    # The harness calls a "us." cross-region inference profile, billed at the Regional CRIS rows.
    exact = {"input": "USE1_InputTokenCount-Units", "output": "USE1_OutputTokenCount-Units",
             "cache_read": "USE1_CacheReadInputTokenCount-Units", "cache_write": "USE1_CacheWriteInputTokenCount-Units"}
    return {k: next((r for r in rows if r["usagetype"].endswith(":" + v)), None) for k, v in exact.items()}


def guardrail_prices() -> dict:
    p = boto3.client("pricing", region_name="us-east-1")
    want = {"content": "USE1-Guardrail-ContentPolicyUnitsConsumed", "topic": "USE1-Guardrail-TopicPolicyUnitsConsumed",
            "reasoning": "USE1-Guardrail-AutomatedReasoningPolicyUnitsConsumed"}
    out = {}
    for k, ut in want.items():
        for it in p.get_products(ServiceCode="AmazonBedrock", Filters=[
                {"Type": "TERM_MATCH", "Field": "usagetype", "Value": ut}])["PriceList"]:
            d = json.loads(it)
            for term in d["terms"].get("OnDemand", {}).values():
                for dim in term["priceDimensions"].values():
                    out[k] = float(dim["pricePerUnit"]["USD"])  # per text unit (up to 1,000 characters)
    return out


# AWS published on-demand rates, us-east-1 (x86, requests and GB-seconds), checked 2026-11.
LAMBDA_GB_S = 0.0000166667
LAMBDA_REQ = 0.20 / 1e6
SFN_TRANSITION = 0.025 / 1000
DDB_WRU = 0.625 / 1e6  # on-demand write request unit
DDB_RRU = 0.125 / 1e6
SNS_PUBLISH = 0.50 / 1e6


def alarm_month_usd() -> float:
    """CloudWatch bills a standard alarm per metric it watches ($0.10 each, metric math counts every metric) and a
    composite alarm at $0.50 a month (us-east-1 published rates)."""
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    r = cw.describe_alarms(AlarmNamePrefix="cs-", AlarmTypes=["MetricAlarm", "CompositeAlarm"])
    metrics = sum(sum(1 for q in a["Metrics"] if "MetricStat" in q) if a.get("Metrics") else 1 for a in r["MetricAlarms"])
    return round(metrics * 0.10 + len(r["CompositeAlarms"]) * 0.50, 2)


def main() -> dict:
    s = TRACE["summary"]
    agent = next(x for x in TRACE["spans"] if x["kind"] == "agent")
    cache_read = int(agent.get("cache_read_tokens") or 0)
    fresh_in = int(agent["input_tokens"]) - cache_read  # written to the cache or read uncached; priced as a write
    out_tok = int(agent["output_tokens"])
    pr = bedrock_prices()
    usd = lambda k: pr[k]["usd"] if pr.get(k) else None  # noqa: E731
    model = {
        "input_tokens_total": int(agent["input_tokens"]), "cache_read_tokens": cache_read,
        "uncached_input_tokens": fresh_in, "output_tokens": out_tok,
        "usd": round(cache_read / 1e6 * usd("cache_read") + fresh_in / 1e6 * max(usd("cache_write") or 0, usd("input"))
                     + out_tok / 1e6 * usd("output"), 4),
        "usd_without_cache": round(int(agent["input_tokens"]) / 1e6 * usd("input") + out_tok / 1e6 * usd("output"), 4),
        "prices_per_million": {k: (v and {"usd": v["usd"], "usagetype": v["usagetype"]}) for k, v in pr.items()},
    }
    # What the rest of the stack does for one run (counted from the trace and the code paths).
    lambda_gb_s = (0.5 * s["agent_ms"] / 1000          # runner, 512 MB, for the agent's duration
                   + 1.0 * s["tool_ms"] / 1000         # tools, 1024 MB
                   + 0.25 * s["hook_ms"] / 1000        # hooks, 256 MB
                   + 1.0 * 6.0 + 1.5 * 0.5 * 40        # sender with the solver (about 6 s), API calls while the page polls
                   + 0.25 * 0.3 * 8)                   # pager, recorder
    lambda_req = s["tool_calls"] + s["hook_calls"] + 1 + 40 + 8 + 1
    infra = {"lambda_usd": round(lambda_gb_s * LAMBDA_GB_S + lambda_req * LAMBDA_REQ, 5),
             "step_functions_usd": round(12 * SFN_TRANSITION, 5),
             "dynamodb_usd": round(60 * DDB_WRU + 120 * DDB_RRU, 6),
             "sns_usd": round(8 * SNS_PUBLISH, 7)}
    g = guardrail_prices()
    # The input guardrail reads what the person typed (one text unit, two policies). The solver reads one facts
    # sentence of about 700 characters, once per release; it is counted here as two text units to be safe.
    solver = {"input_guardrail_usd": round(g["content"] + g["topic"], 6), "automated_reasoning_usd": round(2 * g["reasoning"], 6),
              "prices_per_text_unit": g}
    infra_total = round(sum(infra.values()) + solver["input_guardrail_usd"] + solver["automated_reasoning_usd"], 4)
    res = {"measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "run_id": TRACE["run_id"],
           "model": model, "infrastructure": infra, "checks": solver,
           "per_warning_usd": round(model["usd"] + infra_total, 3),
           "idle_month_usd": {"alarms": alarm_month_usd(), "dashboard_if_over_free_tier": 3.00, "everything_else": 0.0,
                              "note": "Lambda, Step Functions, DynamoDB on demand and SNS cost nothing between storms."},
           "kerr_night": {"warnings": 14, "source": "data/raw/kerr-county-2025-07-04.json (FEMA IPAWS archive, "
                          "Flash Flood Warnings and updates for Kerr County, 1 AM to noon local, July 4, 2025)",
                          "usd": round(14 * (model["usd"] + infra_total), 2)}}
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
