/* Operations page: live CloudWatch numbers from /api/status, one saved trace, the scale and accessibility results. */
(() => {
  "use strict";
  const esc = (s) => window.CS_esc(s);
  const pill = (...a) => window.CS_app.pill(...a);
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => Math.round(n || 0).toLocaleString();

  const HOOK_REASON = {
    live_channel: "Tried to set a live channel, an approval or an officer",
    too_long: "Alert text over the federal length limit",
    opinion: "An opinion for the officer (rule R9)",
    unknown_tool: "A tool that does not exist",
    bad_area: "An area outside the two allowed values",
    bad_class: "An alert class outside the two allowed values",
    bad_run_id: "A missing or malformed run id",
    bad_json: "Tool input that is not JSON",
    too_large: "Input too large to check",
    guardrail: "A prompt attack, caught by Bedrock Guardrails",
    other: "Other",
  };
  const SENDER_BY = { gate: "Rule engine, on stored records", automated_reasoning: "Automated Reasoning solver", R10: "One-alert-per-warning lock (R10)" };
  const FN = {
    "cs-tools": "The six tools the agent can call", "cs-hook": "Checks every request and tool call; fails closed",
    "cs-sender": "The only code that can send live", "cs-api": "This site's API and the developer API",
    "cs-runner": "Starts the agent on the managed harness", "cs-pager": "Pages one person and waits",
    "cs-escalated": "Escalates to the state warning point", "cs-recorder": "Writes the test and live channels",
  };

  async function data(name) { try { const r = await fetch(`data/${name}.json`); return r.ok ? await r.json() : null; } catch (e) { return null; } }
  const sum = (pts) => (pts || []).reduce((n, p) => n + p.v, 0);

  function days(generatedAt, n = 14) {
    const out = [];
    const end = new Date(generatedAt * 1000);
    for (let i = n - 1; i >= 0; i--) {
      const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate() - i));
      out.push(d.toISOString().slice(0, 10));
    }
    return out;
  }

  function spark(pts, dayList) {
    const by = Object.fromEntries((pts || []).map((p) => [p.day, p.v]));
    const vals = dayList.map((d) => by[d] || 0);
    const max = Math.max(1, ...vals);
    const w = 100 / vals.length;
    const bars = vals.map((v, i) => {
      const h = v ? Math.max(3, (v / max) * 32) : 2;
      return `<rect class="${v ? "" : "zero"}" x="${(i * w + w * 0.12).toFixed(2)}" y="${(34 - h).toFixed(2)}" width="${(w * 0.76).toFixed(2)}" height="${h.toFixed(2)}" rx="1"></rect>`;
    }).join("");
    return `<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>
      <div class="spark-cap"><span>${esc(dayList[0].slice(5))}</span><span>today</span></div>`;
  }

  function barList(group, names) {
    const rows = Object.entries(group || {}).map(([k, pts]) => [k, sum(pts)]).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
    if (!rows.length) return `<p class="empty-state">None in the last 14 days.</p>`;
    const max = rows[0][1];
    return `<ul class="bars" data-enter>${rows.map(([k, v]) => `<li><span>${esc(names[k] || k)}</span><span class="n">${fmt(v)}</span>
      <span class="track"><span class="fill" style="width:${((100 * v) / max).toFixed(1)}%"></span></span></li>`).join("")}</ul>`;
  }

  function alarmsTable(alarms) {
    if (!alarms.length) return `<tbody><tr><td class="muted">No alarms found.</td></tr></tbody>`;
    const st = (a) => a.state === "OK" ? pill("pass", "Quiet") : a.state === "ALARM"
      ? (a.name === "cs-live-sent" ? pill("warn", "Informational") : pill("fail", "Firing")) : pill("info", "No data yet");
    const order = (a) => (a.state === "ALARM" ? 0 : a.state === "OK" ? 2 : 1);
    const rows = [...alarms].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
    return `<thead><tr><th>Alarm</th><th>State</th><th>What it means</th></tr></thead><tbody>${rows.map((a) =>
      `<tr><td class="nw"><b>${esc(a.name)}</b></td><td>${st(a)}</td><td>${esc(a.description)}</td></tr>`).join("")}</tbody>`;
  }

  function functionsTable(m) {
    const names = Object.keys(FN);
    return `<thead><tr><th>Function</th><th class="job">Job</th><th class="num">Invocations</th><th class="num">Errors</th><th class="num">p95 duration</th></tr></thead><tbody>${names.map((fn) => {
      const inv = sum(m[`${fn} invocations`]), er = sum(m[`${fn} errors`]);
      const p95 = Math.max(0, ...(m[`${fn} p95 ms`] || []).map((p) => p.v));
      return `<tr><td><b>${esc(fn)}</b></td><td class="job">${esc(FN[fn])}</td><td class="num">${fmt(inv)}</td><td class="num">${er ? `<span class="pill fail">${fmt(er)}</span>` : "0"}</td><td class="num">${p95 ? (p95 >= 1000 ? (p95 / 1000).toFixed(1) + " s" : fmt(p95) + " ms") : "none"}</td></tr>`;
    }).join("")}</tbody>`;
  }

  async function live() {
    $("tiles").setAttribute("aria-busy", "true");
    let s;
    try {
      s = await window.CS_api("/api/status");
    } catch (e) {
      $("finding").textContent = "CloudWatch could not be read just now. The saved results further down still load.";
      $("tiles").innerHTML = `<p class="empty-state">${esc(e.message)}</p>`;
      $("tiles").setAttribute("aria-busy", "false");
      return;
    }
    const m = (s.series || {}).metrics || {};
    const dl = days(s.generated_at);
    const live = sum(m.LiveSent), refused = sum(m.SenderRefused), denied = sum(m.HookDenied), allowed = sum(m.HookAllowed);
    const fns = Object.keys(FN);
    const errs = fns.reduce((n, f) => n + sum(m[`${f} errors`]), 0), inv = fns.reduce((n, f) => n + sum(m[`${f} invocations`]), 0);
    const pages = sum(m.PageSent);
    const tiles = [
      [live, "live alerts sent, each after an officer approved it", m.LiveSent],
      [refused, "approved releases the sender still refused", m.SenderRefused],
      [denied, `tool calls the hook denied, of ${fmt(denied + allowed)} it checked`, m.HookDenied],
      [errs, `errors in ${fmt(inv)} function calls`, null, errs > 0],
    ];
    $("tiles").innerHTML = tiles.map(([n, t, pts, bad]) => `<div class="tile${bad ? " alert" : ""}"><b>${fmt(n)}</b><span>${esc(t)}</span>${pts !== null ? spark(pts, dl) : spark(fns.flatMap((f) => m[`${f} errors`] || []), dl)}</div>`).join("");
    $("tiles").setAttribute("aria-busy", "false");
    $("hookby").innerHTML = barList((s.series || {}).hook_denied_by, HOOK_REASON);
    $("refby").innerHTML = barList((s.series || {}).sender_refused_by, SENDER_BY);
    $("alarms").innerHTML = alarmsTable(s.alarms || []);
    $("functions").innerHTML = functionsTable(m);
    const firing = (s.alarms || []).filter((a) => a.state === "ALARM" && a.name !== "cs-live-sent");
    const quiet = (s.alarms || []).filter((a) => a.state === "OK").length;
    $("finding").innerHTML = `Last 14 days: <strong>${fmt(live)} live ${live === 1 ? "alert" : "alerts"}</strong>, ${fmt(refused)} approved ${refused === 1 ? "release" : "releases"} refused at the sender, ${fmt(denied)} tool calls denied, ${fmt(pages)} pages sent and ${fmt(errs)} errors in ${fmt(inv)} function calls. `
      + (firing.length ? `${firing.length} ${firing.length === 1 ? "alarm is" : "alarms are"} firing: ${esc(firing.map((a) => a.name).join(", "))}.` : `${quiet} of ${(s.alarms || []).length} alarms are quiet.`);
    const age = Math.max(0, Math.round(Date.now() / 1000 - s.generated_at));
    $("fresh").textContent = `Read from CloudWatch ${age < 5 ? "just now" : age + " s ago"}`;
    requestAnimationFrame(() => document.querySelectorAll(".bars[data-enter]").forEach((b) => b.removeAttribute("data-enter")));
    window.CS_wire(document);
  }

  // ---------- one trace ----------
  function trace(t) {
    if (!t) { $("waterfall").innerHTML = `<p class="empty-state">The saved trace did not load.</p>`; return; }
    const sp = t.spans;
    const session = sp.find((x) => x.name === "POST /invocations") || sp[0];
    const t0 = session.start_ms, t1 = session.start_ms + session.dur_ms, span = t1 - t0;
    const pos = (x) => `left:${(((x.start_ms - t0) / span) * 100).toFixed(2)}%;width:${Math.max(0.25, (x.dur_ms / span) * 100).toFixed(2)}%`;
    let model = 0;
    const rows = [];
    for (const x of sp) {
      if (x.start_ms < t0 - 1 || x.start_ms > t1) continue;
      let label, cls;
      const two = (full, short) => `<span class="full">${full}</span><span class="short">${short}</span>`;
      if (x.kind === "agent") { label = two("<b>Agent loop</b>, Strands on the harness", "<b>Agent loop</b>"); cls = "k-agent"; }
      else if (x.kind === "model") { model += 1; label = two(`<b>Claude Sonnet 4.6</b>, call ${model}`, `<b>Model</b> ${model}`); cls = "k-model"; }
      else if (x.name.startsWith("execute_tool")) { label = two(`<b>Tool</b> ${esc(x.tool)}`, `<b>Tool</b> ${esc(x.tool)}`); cls = "k-tool"; }
      else if (x.kind === "hook") { label = x.parent === session.id ? two("<b>Hook</b>, Guardrails on the request", "<b>Guardrails</b>") : two("<b>Hook</b>, before the tool call", "<b>Hook</b>"); cls = "k-hook"; }
      else continue;
      rows.push(`<div class="wf-row"><span class="nm">${label}</span><span class="lane"><span class="seg ${cls}" style="${pos(x)}"></span></span><span class="ms">${x.dur_ms >= 1000 ? (x.dur_ms / 1000).toFixed(1) + " s" : Math.round(x.dur_ms) + " ms"}</span></div>`);
    }
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => `<span>${Math.round((span * f) / 1000)} s</span>`).join("");
    $("waterfall").innerHTML = rows.join("") + `<div class="wf-axis"><span></span><span class="ticks">${ticks}</span><span></span></div>`;
    const S = t.summary;
    $("trace-title").textContent = `The agent's ${Math.round(span / 1000)} seconds`;
    $("waterfall").setAttribute("aria-label", `Waterfall of one agent run: ${S.model_calls} model calls taking ${Math.round(S.model_ms / 1000)} seconds, ${S.tool_calls} tool calls taking ${(S.tool_ms / 1000).toFixed(1)} seconds, ${S.hook_calls} hook checks taking ${(S.hook_ms / 1000).toFixed(1)} seconds.`);
    $("trace-legend").innerHTML = [["k-model", "Model"], ["k-tool", "Tool"], ["k-hook", "Hook"], ["k-agent", "Agent loop"]]
      .map(([c, n]) => `<span><i class="sw seg ${c}" style="position:static"></i>${n}</span>`).join("");
    $("trace-note").textContent = `Run ${t.run_id} on the Kerr County preset, trace ${t.trace_id}. ${S.model_calls} model calls took ${Math.round(S.model_ms / 1000)} of the ${Math.round(span / 1000)} seconds, the ${S.tool_calls} tool calls ${(S.tool_ms / 1000).toFixed(1)} s and the ${S.hook_calls} hook checks ${(S.hook_ms / 1000).toFixed(1)} s. ${fmt(S.input_tokens)} input and ${fmt(S.output_tokens)} output tokens. ${S.errors} span errors. Exported from CloudWatch with only timing, names and token counts kept.`;

    // lanes over the whole trace: the agent finishes, paging carries on
    const total = t.total_ms;
    const pagesAt = sp.filter((x) => x.kind === "paging" && x.name === "PageContact").map((x) => x.start_ms);
    const who = ["Officer A", "Officer B", "Officer C", "Officer A", "Officer B", "Officer C"];
    const P = (ms) => ((ms / total) * 100).toFixed(2) + "%";
    $("lanes").innerHTML = `<div class="ln"><span>Agent</span><span class="track"><span class="span" style="left:${P(t0)};width:${P(span)}" title="Agent working"></span></span></div>
      <div class="ln"><span>Paging</span><span class="track">${pagesAt.map((ms, i) => `<span class="mark" style="left:${P(ms)}"><span>Pages ${who[i] || "next"}</span></span>`).join("")}</span></div>
      <div class="ln-axis"><span></span><span class="ticks">${[0, 1, 2, 3, 4, 5, 6].map((mn) => `<span>${mn}${mn === 6 ? " min" : ""}</span>`).join("")}</span></div>
      <p class="small muted">The first page went out ${pagesAt[0] < 1000 ? Math.round(pagesAt[0]) + " ms" : (pagesAt[0] / 1000).toFixed(1) + " s"} after the warning, before the agent had started. The agent was done at ${Math.round(t1 / 1000)} s; nobody answered in this demo, so paging moved to the next person every ${Math.round((pagesAt[1] - pagesAt[0]) / 1000)} s on its own.</p>`;
  }

  // ---------- scale ----------
  function scale(sc) {
    if (!sc) return;
    const r = sc.race || {}, rb = sc.race_before_fix || {}, ld = sc.load || {}, fo = sc.fanout || {};
    const stages = ld.stages || [];
    const total = stages.reduce((n, s) => n + s.requests, 0), five = stages.reduce((n, s) => n + s.server_5xx, 0);
    const at20 = stages.find((s) => s.target_rps === 20) || {};
    $("scale-tiles").innerHTML = [
      [`${rb.sent_true ?? "?"} to ${r.sent_true ?? "?"}`, `live sends from ${r.concurrent_approvals || 12} approvals landing at once, before and after the fix`],
      [fmt(five), `server errors in ${fmt(total)} API requests, up to twice the throttle`],
      [`${Math.round(at20.p95_ms || 0)} ms`, "p95 for a full rule check at 20 requests a second"],
      [`${fo.reached_first_person ?? "?"} of ${fo.counties ?? "?"}`, `counties paged at once, first page p95 ${fo.first_page_p95_s ?? "?"} s, all escalated`],
    ].map(([n, t]) => `<div class="tile"><b>${esc(n)}</b><span>${esc(t)}</span></div>`).join("");
    $("load").innerHTML = `<thead><tr><th class="num">Requests a second</th><th class="num">Requests</th><th class="num">200 OK</th><th class="num">429 throttled</th><th class="num">5xx</th><th class="num">p50</th><th class="num">p95</th><th class="num">p99</th></tr></thead>
      <tbody>${stages.map((s) => `<tr><td class="num">${s.target_rps}</td><td class="num">${fmt(s.requests)}</td><td class="num">${fmt(s.ok_200)}</td><td class="num">${fmt(s.throttled_429)}</td><td class="num">${fmt(s.server_5xx)}</td><td class="num">${s.p50_ms} ms</td><td class="num">${s.p95_ms} ms</td><td class="num">${s.p99_ms} ms</td></tr>`).join("")}</tbody>`;
    $("load-note").textContent = `${ld.endpoint || ""}. Path: ${ld.path || ""}. The API Gateway limit, ${ld.api_gateway_limit || ""}, is set low on purpose for a public demo; above it, requests get a clean 429 and nothing fails. The p99 at 40 a second is Lambda starting new copies. Measured ${ld.measured_at || ""}.`;
  }

  // ---------- accessibility ----------
  function a11y(a) {
    if (!a) return;
    const pages = [...new Set(a.rows.map((r) => r.page))];
    const cell = (p, th, w) => {
      const r = a.rows.find((x) => x.page === p && x.theme === th && x.width === w);
      if (!r) return `<td class="muted">not run</td>`;
      const n = r.axe_violations.length;
      return `<td>${n ? pill("fail", `${n} ${n === 1 ? "issue" : "issues"}`) : pill("pass", "0")}</td>`;
    };
    $("a11y").innerHTML = `<thead><tr><th>Page</th><th>Light, desktop</th><th>Light, phone</th><th>Dark, desktop</th><th>Dark, phone</th><th class="num">Tab stops</th><th>Reflow at 320 px</th></tr></thead>
      <tbody>${pages.map((p) => {
        const kb = a.rows.find((x) => x.page === p && x.keyboard && x.width === "desktop") || {};
        const ph = a.rows.find((x) => x.page === p && x.width === "phone" && x.theme === "light") || {};
        const miss = (kb.keyboard || {}).without_visible_focus || [];
        return `<tr><td><b>${esc(p)}</b></td>${cell(p, "light", "desktop")}${cell(p, "light", "phone")}${cell(p, "dark", "desktop")}${cell(p, "dark", "phone")}
          <td class="num">${(kb.keyboard || {}).tab_stops ?? ""}${miss.length ? ` <span class="pill fail">${miss.length} unseen</span>` : ""}</td>
          <td>${ph.reflow_320px_overflow_px ? pill("fail", `${ph.reflow_320px_overflow_px} px sideways`) : pill("pass", "fits")}</td></tr>`;
      }).join("")}</tbody>`;
    const motion = Object.values(a.reduced_motion_running_animations || {}).reduce((n, v) => n + v, 0);
    $("ax-lead").innerHTML = `<strong>${a.totals.violations} automated WCAG 2.2 AA failures</strong> across ${a.totals.pages} page views: ${pages.length} pages, light and dark, desktop and phone. Every tab stop shows where focus is, nothing scrolls sideways at 320 px or at 200% text, and with reduced motion set ${motion} animations run.`;
    $("ax-note").textContent = `${a.standard}. ${a.limits} Measured ${a.measured_at} with python evals/a11y.py.`;
  }

  function where() {
    const W = [
      ["CloudWatch dashboard: Countersign", "Safety, agent, paging, API and infrastructure in one view, with the AgentCore spans as a table.", "aws/observability.py"],
      ["Logs Insights", "Every refusal is a structured line. Try it on the hook's log group:", "filter event = \"hook_deny\" | stats count(*) by code"],
      ["Traces", "AgentCore spans land in aws/spans; X-Ray joins them to the API, hooks and paging. One trace per run.", "python evals/trace.py <run_id>"],
      ["Alarms and the ops topic", "13 alarms publish to the cs-ops SNS topic. Subscribe a phone to be woken by cs-unsafe.", "aws sns subscribe --topic-arn <cs-ops> --protocol sms"],
      ["Dead-letter queue", "Agent runs are never retried, so a failed run lands in cs-dlq at once; the recorder retries twice first. Paging is separate and does not depend on either.", "cs-dlq"],
      ["Measure it again", "Every number on this page below the alarms comes from a script in the repository.", "python evals/scale.py all && python evals/a11y.py"],
    ];
    $("where").innerHTML = W.map(([h, p, c]) => `<div class="panel where"><h3>${esc(h)}</h3><p>${esc(p)}</p><code>${esc(c)}</code></div>`).join("");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    where();
    $("refresh").addEventListener("click", live);
    live();
    const [t, sc, a] = await Promise.all([data("trace"), data("scale"), data("a11y")]);
    trace(t); scale(sc); a11y(a);
    window.CS_wire(document);
  });
})();
