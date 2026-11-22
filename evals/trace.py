"""Export one real agent run's OpenTelemetry trace from CloudWatch (aws/spans) for the Operations page.

    python evals/trace.py run_74dd1b7866e549ee

The AgentCore managed harness emits spans for the agent loop, every model call and every tool call; the hook
and tool Lambdas add their own. This keeps only a fixed allow-list of fields (span name, timing, parent, model,
tool, token counts, status), so nothing else in the span, such as credentials metadata, leaves the account.
Writes web/data/trace.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
logs = boto3.client("logs", region_name="us-east-1")


def query(qs: str, hours: int = 72) -> list[dict]:
    q = logs.start_query(logGroupName="aws/spans", startTime=int(time.time()) - hours * 3600, endTime=int(time.time()),
                         queryString=qs)["queryId"]
    while True:
        time.sleep(1.5)
        r = logs.get_query_results(queryId=q)
        if r["status"] in ("Complete", "Failed", "Cancelled"):
            return [{f["field"]: f["value"] for f in row} for row in r["results"]]


STATES = {"Start", "PageContact", "NextContact", "EndOfList", "NewRound", "RoundsLeft", "EscalateToState",
          "WaitForState", "Decided"}


def kind_of(name: str, service: str, parent_kind: str | None) -> str:
    if name.startswith("invoke_agent"):
        return "agent"
    if name.startswith("execute_event_loop_cycle"):
        return "cycle"
    if name.startswith("chat us.") or name.startswith("chat amazon"):
        return "model"
    if name == "chat":
        return "model-wrap"
    if name.startswith("execute_tool") or name.startswith("mcp tools/call"):
        return "tool"
    if name in STATES or name.endswith("-paging") or parent_kind == "paging":
        return "paging"
    if name == "Lambda.Invoke" and service.startswith("harness"):
        return "hook"  # the harness invokes a Lambda only for its two hooks; tools go through the Gateway
    return "other"


def main(run_id: str) -> None:
    traces = query(f"fields traceId | filter @message like /{run_id}/ | stats count(*) as n by traceId | sort n desc | limit 1")
    if not traces:
        sys.exit(f"No trace found for {run_id} in the last 72 hours.")
    tid = traces[0]["traceId"]
    rows = query(f"fields @message | filter traceId = '{tid}' | limit 2000")
    spans = []
    for r in rows:
        m = json.loads(r["@message"])
        a = m.get("attributes") or {}
        name = str(m.get("name", ""))
        tool = name.split("alerts___")[-1] if "alerts___" in name else None
        model = name.split(" ", 1)[1] if name.startswith("chat ") else None
        spans.append({
            "id": m.get("spanId"), "parent": m.get("parentSpanId"), "name": name, "kind": None,
            "service": (m.get("resource") or {}).get("attributes", {}).get("service.name", ""),
            "start_ns": int(m.get("startTimeUnixNano", 0)), "dur_ms": round(int(m.get("durationNano", 0)) / 1e6, 1),
            "status": (m.get("status") or {}).get("code", "UNSET"), "tool": tool, "model": model,
            "input_tokens": a.get("gen_ai.usage.input_tokens") or a.get("gen_ai.usage.prompt_tokens"),
            "output_tokens": a.get("gen_ai.usage.output_tokens") or a.get("gen_ai.usage.completion_tokens"),
            "cache_read_tokens": a.get("gen_ai.usage.cache_read_input_tokens") or a.get("gen_ai.usage.cache_read.input_tokens"),
            "function": a.get("aws.function_name") if str(a.get("aws.function_name", "")).startswith("cs-") else None,
        })
    # one span name can appear twice when both the harness and the Lambda report it; keep unique span ids
    uniq = {s["id"]: s for s in spans if s["id"]}
    spans = sorted(uniq.values(), key=lambda s: s["start_ns"])
    by_id = {s["id"]: s for s in spans}
    for s in spans:  # parents start first, so one pass in time order sees the parent's kind
        pk = by_id.get(s["parent"], {}).get("kind")
        s["kind"] = kind_of(s["name"], s["service"], pk)
    t0 = spans[0]["start_ns"]
    for s in spans:
        s["start_ms"] = round((s.pop("start_ns") - t0) / 1e6, 1)
    total = max(s["start_ms"] + s["dur_ms"] for s in spans)
    model = [s for s in spans if s["kind"] == "model"]
    tools = [s for s in spans if s["name"].startswith("execute_tool")]
    hooks = [s for s in spans if s["kind"] == "hook"]
    agent = next((s for s in spans if s["kind"] == "agent"), {})
    paging = [s for s in spans if s["kind"] == "paging" and s["name"] == "PageContact"]
    out = {"run_id": run_id, "trace_id": tid, "source": "CloudWatch aws/spans (AgentCore Observability, OpenTelemetry)",
           "exported_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "total_ms": total,
           "summary": {"spans": len(spans), "model_calls": len(model), "tool_calls": len(tools), "hook_calls": len(hooks),
                       "pages": len(paging), "agent_ms": round(agent.get("dur_ms", 0)),
                       "agent_start_ms": agent.get("start_ms"),
                       "model_ms": round(sum(s["dur_ms"] for s in model)), "tool_ms": round(sum(s["dur_ms"] for s in tools)),
                       "hook_ms": round(sum(s["dur_ms"] for s in hooks)),
                       "input_tokens": int(agent.get("input_tokens") or 0), "output_tokens": int(agent.get("output_tokens") or 0),
                       "errors": len([s for s in spans if s["status"] == "ERROR"])},
           "spans": spans}
    (ROOT / "web" / "data" / "trace.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"], indent=1), "total", total, "ms")
    print(sorted({(s["kind"], s["name"][:50]) for s in spans}))


if __name__ == "__main__":
    main(sys.argv[1])
