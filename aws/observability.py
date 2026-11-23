"""Countersign observability and reliability: alarms, a dashboard, a dead-letter queue, traces and access logs.

    python aws/observability.py        # create or update everything below (safe to re-run)

What it sets up, all prefixed cs-:
  * cs-ops SNS topic: where every alarm goes. Subscribe a phone or an inbox to it.
  * cs-dlq SQS queue: async work that failed after its retries (the agent runner, the channel recorder).
  * Step Functions: X-Ray tracing and execution logs for the paging workflow.
  * API Gateway: JSON access logs for every request.
  * CloudWatch alarms on the things that matter for safety, not just uptime, plus one composite alarm.
  * CloudWatch dashboard "Countersign": safety, agent, paging, API and infrastructure in one view, including
    the AgentCore harness's own OpenTelemetry spans (aws/spans) for model and tool calls.

Cost: 14 standard alarms (22 metrics with the metric-math ones) and 1 composite alarm, about USD 2.70 a month
(see evals/cost.py); the dashboard is within the free three.
Custom metrics come from Embedded Metric Format log lines (aws/lambdas/common.py: metric), so no PutMetricData calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy import ACCOUNT, P, REGION, S, arn_fn, log, save, state  # noqa: E402

cw, sqs, sns, lam = S.client("cloudwatch"), S.client("sqs"), S.client("sns"), S.client("lambda")
sfn, apigw, logs = S.client("stepfunctions"), S.client("apigatewayv2"), S.client("logs")
NS = "Countersign"
FUNCTIONS = [f"{P}-{n}" for n in ("tools", "hook", "sender", "api", "runner", "pager", "escalated", "recorder")]


def log_group(name: str, days: int = 30) -> str:
    try:
        logs.create_log_group(logGroupName=name)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName=name, retentionInDays=days)
    return f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{name}"


def ops_topic() -> str:
    # Not encrypted with the AWS-managed SNS key: CloudWatch alarms cannot use that key, so they could not publish.
    return sns.create_topic(Name=f"{P}-ops")["TopicArn"]


def dead_letter_queue() -> str:
    url = sqs.create_queue(QueueName=f"{P}-dlq", Attributes={"MessageRetentionPeriod": str(14 * 86400),
                                                              "SqsManagedSseEnabled": "true"})["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    # The agent runner is never retried: rerunning an agent could draft twice. The recorder is safe to retry.
    for fn, retries in ((f"{P}-runner", 0), (f"{P}-recorder", 2)):
        kw = dict(FunctionName=fn, MaximumRetryAttempts=retries, MaximumEventAgeInSeconds=3600,
                  DestinationConfig={"OnFailure": {"Destination": arn}})
        try:
            lam.put_function_event_invoke_config(**kw)
        except lam.exceptions.ResourceConflictException:
            lam.update_function_event_invoke_config(**kw)
    return arn


def tracing_and_access_logs(st: dict) -> None:
    sm = st["paging_sm_arn"]
    lg = log_group(f"/aws/vendedlogs/states/{P}-paging")
    sfn.update_state_machine(stateMachineArn=sm, tracingConfiguration={"enabled": True}, loggingConfiguration={
        "level": "ALL", "includeExecutionData": False,
        "destinations": [{"cloudWatchLogsLogGroup": {"logGroupArn": lg + ":*"}}]})
    for fn in FUNCTIONS:
        cfg = lam.get_function_configuration(FunctionName=fn)
        if (cfg.get("TracingConfig") or {}).get("Mode") != "Active":
            lam.update_function_configuration(FunctionName=fn, TracingConfig={"Mode": "Active"})
            lam.get_waiter("function_updated_v2").wait(FunctionName=fn)
    alg = log_group(f"/aws/apigateway/{P}-http-api")
    fmt = json.dumps({"requestId": "$context.requestId", "ip": "$context.identity.sourceIp",
                      "time": "$context.requestTime", "method": "$context.httpMethod", "route": "$context.routeKey",
                      "path": "$context.path", "status": "$context.status", "latencyMs": "$context.responseLatency",
                      "integrationMs": "$context.integrationLatency", "bytes": "$context.responseLength",
                      "error": "$context.integrationErrorMessage"})
    apigw.update_stage(ApiId=st["http_api_id"], StageName="$default",
                       AccessLogSettings={"DestinationArn": alg, "Format": fmt})


def m(name: str, stat: str = "Sum", **dims) -> dict:
    return {"Namespace": NS, "MetricName": name, "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
            "Statistic": stat}


def alarms(st: dict, ops: str, dlq_arn: str) -> list[str]:
    a = []

    def alarm(name, desc, metric=None, threshold=1, periods=1, period=300, cmp="GreaterThanOrEqualToThreshold",
              metrics=None, stat=None):
        kw = dict(AlarmName=f"{P}-{name}", AlarmDescription=desc, ActionsEnabled=True, AlarmActions=[ops],
                  OKActions=[ops], EvaluationPeriods=periods, Threshold=threshold, ComparisonOperator=cmp,
                  TreatMissingData="notBreaching")
        if metrics:
            kw["Metrics"] = metrics
        else:
            ex = dict(metric)
            s = ex.pop("Statistic")
            kw.update(ex, Period=period)
            if s.startswith("p"):
                kw["ExtendedStatistic"] = s
            else:
                kw["Statistic"] = s
        cw.put_metric_alarm(**kw)
        a.append(kw["AlarmName"])

    lam_err = lambda fn: {"Namespace": "AWS/Lambda", "MetricName": "Errors",  # noqa: E731
                          "Dimensions": [{"Name": "FunctionName", "Value": fn}], "Statistic": "Sum"}
    alarm("sender-errors", "The only code that can send live failed. An approved alert may not have gone out: "
          "check the sender's log and send by hand if needed.", lam_err(f"{P}-sender"))
    alarm("hook-errors", "The tool-call hook failed. It fails closed, so the agent is blocked until it is fixed: "
          "drafts will stop, paging continues.", lam_err(f"{P}-hook"))
    alarm("lambda-errors", "Five or more errors across Countersign functions in five minutes.", threshold=5, metrics=[
        *[{"Id": f"e{i}", "MetricStat": {"Metric": {k: v for k, v in lam_err(fn).items() if k != "Statistic"},
                                         "Period": 300, "Stat": "Sum"}, "ReturnData": False}
          for i, fn in enumerate(FUNCTIONS)],
        {"Id": "total", "Expression": "SUM(METRICS())", "Label": "Errors, all functions", "ReturnData": True}])
    alarm("sender-refused", "Officers approved a release and the sender refused it (rule engine, solver or the "
          "one-per-warning lock). Nothing went out; someone should look at why.", m("SenderRefused"))
    alarm("hook-deny-spike", "Ten or more tool calls denied in five minutes: someone may be attacking the agent.",
          m("HookDenied"), threshold=10)
    alarm("guardrail-spike", "Five or more prompt attacks blocked by Bedrock Guardrails in five minutes.",
          m("GuardrailIntervened"), threshold=5)
    alarm("escalated", "Nobody at the county answered every round of pages; the state warning point was paged.",
          m("Escalated"))
    alarm("live-sent", "A live alert went out. In a real office every live send tells the whole team.", m("LiveSent"))
    sm_dim = [{"Name": "StateMachineArn", "Value": st["paging_sm_arn"]}]
    alarm("paging-failed", "The paging workflow failed or timed out. Paging must never stop quietly (rule R7).",
          threshold=1, metrics=[
              {"Id": "f", "MetricStat": {"Metric": {"Namespace": "AWS/States", "MetricName": "ExecutionsFailed",
                                                    "Dimensions": sm_dim}, "Period": 300, "Stat": "Sum"}, "ReturnData": False},
              {"Id": "a", "MetricStat": {"Metric": {"Namespace": "AWS/States", "MetricName": "ExecutionsAborted",
                                                    "Dimensions": sm_dim}, "Period": 300, "Stat": "Sum"}, "ReturnData": False},
              {"Id": "total", "Expression": "f + a", "Label": "Paging failures", "ReturnData": True}])
    alarm("api-5xx", "Five or more server errors from the public API in five minutes.",
          {"Namespace": "AWS/ApiGateway", "MetricName": "5xx", "Dimensions": [{"Name": "ApiId", "Value": st["http_api_id"]}],
           "Statistic": "Sum"}, threshold=5)
    alarm("api-latency", "Rule checks are slow: p95 over 3 seconds for 15 minutes.", m("ApiLatency", "p95"),
          threshold=3000, periods=3, cmp="GreaterThanThreshold")
    alarm("dlq", "Background work failed after its retries and is waiting in the dead-letter queue.",
          {"Namespace": "AWS/SQS", "MetricName": "ApproximateNumberOfMessagesVisible",
           "Dimensions": [{"Name": "QueueName", "Value": f"{P}-dlq"}], "Statistic": "Maximum"})
    alarm("check-unavailable", "A safety check could not run: the input guardrail, the solver or the sender was "
          "unreachable. Each fails closed, so drafts or sends are being refused until it is back.",
          m("SafetyCheckUnavailable"))
    alarm("live-unsigned", "A live alert reached the live channel and the recorder found no officer's approval for it. "
          "This should be impossible: the sender is the only role that can publish live, and it refuses without one.",
          m("LiveUnsigned"))
    rule = " OR ".join(f'ALARM("{P}-{n}")' for n in ("sender-errors", "hook-errors", "paging-failed", "dlq",
                                                     "check-unavailable", "live-unsigned"))
    cw.put_composite_alarm(AlarmName=f"{P}-unsafe", AlarmRule=rule, ActionsEnabled=True, AlarmActions=[ops],
                           OKActions=[ops], AlarmDescription="Any failure that could stop an alert or stop paging. "
                           "This is the one to wake someone for.")
    a.append(f"{P}-unsafe")
    return a


def dashboard(st: dict, alarm_names: list[str]) -> None:
    R = REGION

    def ts(title, metrics, x, y, w=8, h=6, stat="Sum", period=3600, view="timeSeries", stacked=False, extra=None):
        p = {"title": title, "region": R, "metrics": metrics, "stat": stat, "period": period, "view": view,
             "stacked": stacked}
        p.update(extra or {})
        return {"type": "metric", "x": x, "y": y, "width": w, "height": h, "properties": p}

    def text(md, y, h=2):
        return {"type": "text", "x": 0, "y": y, "width": 24, "height": h, "properties": {"markdown": md}}

    def search(expr, label=None):
        e = {"expression": f"SEARCH('{expr}', 'Sum', 3600)", "id": "s" + str(abs(hash(expr)) % 10_000)}
        if label:
            e["label"] = label
        return [[e]]

    sm, api = st["paging_sm_arn"], st["http_api_id"]
    w = [text("# Countersign\nAn agent that drafts phone emergency alerts and can never send one alone. "
              "Top row: is anything unsafe. Then the agent, paging, the public API, and the infrastructure.", 0)]
    w.append({"type": "alarm", "x": 0, "y": 2, "width": 24, "height": 3,
              "properties": {"title": "Alarms", "alarms": [f"arn:aws:cloudwatch:{R}:{ACCOUNT}:alarm:{n}" for n in alarm_names],
                             "sortBy": "stateUpdatedTimestamp"}})
    w.append(text("## Safety: what each layer stopped", 5, 1))
    w += [ts("Live sends and refusals", [[NS, "LiveSent", {"label": "Live alerts sent"}],
                                         [NS, "SenderRefused", {"label": "Releases the sender refused"}],
                                         [NS, "SendAttempt", {"label": "Send attempts"}]], 0, 6),
          ts("Tool calls denied by the hook, by reason",
             search('{Countersign,Hook,Reason} MetricName="HookDenied"'), 8, 6, stacked=True),
          ts("Sender refusals, by layer", search('{Countersign,By,Kind} MetricName="SenderRefused"'), 16, 6),
          ts("Prompt attacks blocked by Bedrock Guardrails", [[NS, "GuardrailIntervened"]], 0, 12),
          ts("Rule engine outcomes", search('{Countersign,Outcome,Stage} MetricName="GateOutcome"'), 8, 12, stacked=True),
          ts("Bedrock Guardrails interventions (AWS)", [["AWS/Bedrock/Guardrails", "InvocationsIntervened",
                                                         {"label": "Interventions"}],
                                                        ["AWS/Bedrock/Guardrails", "Invocations", {"label": "Checks"}]],
             16, 12)]
    w.append(text("## The agent (AgentCore managed harness)", 18, 1))
    span_q = ("SOURCE 'aws/spans' | filter resource.attributes.service.name like /harness_cs_alert_desk/ "
              "| filter name like /execute_tool|chat /\n| stats count(*) as calls, "
              "round(avg(durationNano)/1000000) as avg_ms, round(pct(durationNano, 95)/1000000) as p95_ms by name "
              "| sort calls desc")
    w += [ts("Agent runs", search('{Countersign,Status} MetricName="AgentRun"'), 0, 19),
          ts("Agent run duration", [[NS, "AgentDuration", {"stat": "p50", "label": "p50"}],
                                    [NS, "AgentDuration", {"stat": "p95", "label": "p95"}]], 8, 19, stat="p50"),
          ts("Tool calls by tool", search('{Countersign,Result,Tool} MetricName="ToolCall"'), 16, 19, stacked=True),
          {"type": "log", "x": 0, "y": 25, "width": 16, "height": 6, "properties": {
              "title": "AgentCore spans: model and tool calls (OpenTelemetry, aws/spans)", "region": R,
              "query": span_q, "view": "table"}},
          {"type": "log", "x": 16, "y": 25, "width": 8, "height": 6, "properties": {
              "title": "Tokens per hour (invoke_agent spans)", "region": R, "view": "timeSeries", "stacked": False,
              "query": "SOURCE 'aws/spans' | filter resource.attributes.service.name like /harness_cs_alert_desk/ "
                       "and name like /invoke_agent/ | stats sum(attributes.gen_ai.usage.input_tokens) as input, "
                       "sum(attributes.gen_ai.usage.output_tokens) as output by bin(1h)"}}]
    w.append(text("## Paging: never go quiet", 31, 1))
    sd = ["StateMachineArn", sm]
    w += [ts("Pages sent, by round", search('{Countersign,Round} MetricName="PageSent"'), 0, 32, stacked=True),
          ts("Escalated to the state desk", [[NS, "Escalated"]], 8, 32),
          ts("Paging workflow", [["AWS/States", k, *sd] for k in ("ExecutionsStarted", "ExecutionsSucceeded",
                                                                   "ExecutionsFailed", "ExecutionsTimedOut")], 16, 32)]
    w.append(text("## Public API and rule checks", 38, 1))
    w += [ts("Requests by route", search('{Countersign,Route,Status} MetricName="ApiRequest"'), 0, 39, stacked=True),
          ts("Latency", [[NS, "ApiLatency", {"stat": "p50", "label": "p50"}],
                         [NS, "ApiLatency", {"stat": "p95", "label": "p95"}],
                         [NS, "ApiLatency", {"stat": "p99", "label": "p99"}]], 8, 39, stat="p50", period=300),
          ts("API Gateway", [["AWS/ApiGateway", k, "ApiId", api] for k in ("Count", "4xx", "5xx")], 16, 39)]
    w.append(text("## Infrastructure", 45, 1))
    fn = lambda k, s=None: [["AWS/Lambda", k, "FunctionName", f, ({"stat": s} if s else {})] for f in FUNCTIONS]  # noqa: E731
    w += [ts("Lambda errors", fn("Errors"), 0, 46, stacked=True),
          ts("Lambda duration p95", fn("Duration"), 8, 46, stat="p95"),
          ts("Lambda concurrency and throttles", fn("ConcurrentExecutions", "Maximum") + fn("Throttles"), 16, 46),
          ts("DynamoDB throttles", [["AWS/DynamoDB", "ThrottledRequests", "TableName", f"{P}-{t}", "Operation", op]
                                    for t in ("drafts", "ledger") for op in ("PutItem", "UpdateItem", "Query")], 0, 52),
          ts("Dead-letter queue", [["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", f"{P}-dlq"]],
             8, 52, stat="Maximum"),
          {"type": "log", "x": 16, "y": 52, "width": 8, "height": 6, "properties": {
              "title": "Latest sender refusals", "region": R, "view": "table",
              "query": f"SOURCE '/aws/lambda/{P}-sender' | filter event = 'sender_refused' "
                       "| fields @timestamp, by, kind, failed.0 as first_failed_rule, solver | sort @timestamp desc | limit 20"}}]
    cw.put_dashboard(DashboardName="Countersign", DashboardBody=json.dumps({"widgets": w}))


def apply() -> None:
    st = state()
    ops = ops_topic()
    dlq = dead_letter_queue()
    tracing_and_access_logs(st)
    names = alarms(st, ops, dlq)
    dashboard(st, names)
    save(ops_topic=ops, dlq_arn=dlq, alarms=names,
         dashboard=f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards/dashboard/Countersign")
    log("observability:", len(names), "alarms, dashboard Countersign, dlq", dlq)


if __name__ == "__main__":
    apply()
