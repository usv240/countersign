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

  // ---------- chaos ----------
  const CHAOS = {
    sender_down: ["The sender fails", "FIS: every invocation of cs-sender errors", (e) => {
      const d = e.during || {}, a = e.after_recovery || {};
      return [`Officer approves: ${d.live_messages ?? "?"} live alerts. The officer is told: "${d.told_officer || ""}"`,
        `After recovery: the retry ${a.first_retry_sent ? "sends" : "does not send"}, a second tap ${a.second_retry_sent ? "also sends" : "is refused"}; ${a.live_messages ?? "?"} live alert in all.`];
    }],
    pager_down: ["Both paging functions fail", "FIS: every invocation of cs-pager and cs-escalated errors", (e) => [
      `The workflow pages the state desk itself, through SNS with no Lambda in the path, after ${e.last_resort_page_s ?? "?"} s.`,
      `Then it ends ${String(e.execution_status || "").toLowerCase()} on purpose, so the paging alarm fires.`]],
    hook_down: ["The tool-call check fails", "FIS: every invocation of cs-hook errors", (e) => [
      `The agent drafts ${e.drafts ?? "?"} alerts and publishes ${e.test_messages ?? "?"} tests: the hooks fail closed.`,
      `Paging does not depend on the agent: first page after ${e.first_page_s ?? "?"} s.`]],
    hook_slow: ["The tool-call check stalls", "FIS: 15 seconds added to cs-hook; its limit is 10", (e) => [
      `A slow check is a failed check: ${e.drafts ?? "?"} drafts, ${e.test_messages ?? "?"} tests.`,
      `First page after ${e.first_page_s ?? "?"} s.`]],
    solver_unreachable: ["The solver cannot be asked", "IAM deny on the sender's role for Automated Reasoning", (e) => {
      const d = e.during || {}, a = e.after_recovery || {};
      return [`An approved alert that passes every rule is refused (solver: ${d.solver || "?"}); ${d.live_messages ?? "?"} live alerts.`,
        `Deny removed: it ${a.sent ? "sends" : "does not send"} (solver: ${a.solver || "?"}).`];
    }],
    guardrail_unreachable: ["The input guardrail cannot be reached", "IAM deny on the hook's role for Bedrock Guardrails", (e) => [
      `The request is refused, not waved through: ${e.drafts ?? "?"} drafts, ${e.live_messages ?? "?"} live.`,
      `First page after ${e.first_page_s ?? "?"} s.`]],
    ledger_unwritable: ["The sender cannot record a send", "IAM deny on the sender's role for writes to the ledger", (e) => {
      const d = e.during || {}, a = e.after_recovery || {};
      return [`It cannot claim the warning, so it does not publish: ${d.live_messages ?? "?"} live alerts.`,
        a.later_manual_retry ? `Deny removed: still refused for over ${Math.floor((a.seconds_after_fault_removed || 0) / 60)} minutes while IAM caught up; a retry ${Math.round(a.later_manual_retry.seconds_after_fault_removed / 60)} minutes later sent exactly once.` : a.sent ? "Deny removed: it sends once." : "Deny removed: it did not send."];
    }],
  };

  function chaos(c) {
    if (!c || !c.experiments) { $("ch-lead").textContent = "Not run yet."; return; }
    const rows = Object.keys(CHAOS).filter((k) => c.experiments[k]);
    const held = rows.filter((k) => c.experiments[k].held).length;
    $("ch-lead").innerHTML = `<strong>${held} of ${rows.length} faults held both promises</strong>: no live alert without an officer, and the warning never left unanswered. Each row is a real fault on the deployed stack.`;
    $("chaos").innerHTML = `<thead><tr><th>Fault</th><th>What happened</th><th>Alarm</th><th></th></tr></thead><tbody>${rows.map((k) => {
      const e = c.experiments[k], [title, how, obs] = CHAOS[k];
      const [a, b] = e.error ? [e.error, ""] : obs(e);
      const sig = e.signal ? `${e.signal.count} on ${e.signal.metric}. ` : "";
      const al = sig + Object.entries(e.alarm_seconds || {}).map(([n, t]) => (t == null ? `${n}: did not fire` : typeof t === "string" ? `${n} ${t} from the test before` : `${n} fired in ${t} s`)).join("; ") + (e.cs_unsafe_in_alarm ? ". cs-unsafe firing." : "");
      return `<tr><td><b>${esc(title)}</b><span class="cellnote">${esc(how)}</span></td><td class="obs">${esc(a)}<span>${esc(b)}</span></td><td class="obs">${esc(al || "none expected")}</td><td>${pill(e.held ? "pass" : "fail", e.held ? "held" : "did not hold")}</td></tr>`;
    }).join("")}</tbody>`;
    $("ch-note").textContent = "Alarm times are seconds from the start of the fault and include CloudWatch's metric delay. cs-unsafe is the composite alarm to wake someone for. Re-run with python evals/chaos.py setup, then run, then clean.";
  }

  // ---------- permissions ----------
  function perm(p) {
    if (!p) return;
    const roles = p.roles;
    $("pm-lead").innerHTML = `<strong>${p.checks - p.wrong} of ${p.checks}</strong> role and permission pairs are exactly as designed. Only the sender can publish live; only the approval service can invoke the sender, write an approval or end the paging; no role can silence an alarm or change its own rules. ${p.public.filter((x) => x.result === "PASS").length} of ${p.public.length} policies grant nothing public.`;
    $("perm").classList.add("perm");
    $("perm").innerHTML = `<thead><tr><th>Can it...</th>${roles.map((r) => `<th>${esc(r)}</th>`).join("")}</tr></thead><tbody>${p.matrix.map((m) =>
      `<tr><td>${esc(m.what)}</td>${roles.map((r) => { const x = m.roles[r]; return `<td class="${x.granted ? "can" : "no"}">${x.granted ? "yes" : "no"}${x.ok ? "" : " (wrong)"}</td>`; }).join("")}</tr>`).join("")}</tbody>`;
    const lint = p.lint || {};
    $("pm-note").textContent = `${p.method}. Policy lint: ${lint.ERROR || 0} errors, ${lint.SECURITY_WARNING || 0} security warnings. Measured ${p.measured_at} with python evals/permissions.py.`;
  }

  // ---------- cost ----------
  function cost(c) {
    if (!c) return;
    const m = c.model;
    const usd = (n) => "$" + Number(n).toFixed(2);
    $("cost-tiles").innerHTML = [
      [usd(c.per_warning_usd), "to draft one warning: model, checks, functions, workflow and storage"],
      [usd(m.usd_without_cache), `the same run without prompt caching; ${Math.round((100 * m.cache_read_tokens) / m.input_tokens_total)}% of input tokens were cache reads`],
      [usd(c.kerr_night.usd), `for all ${c.kerr_night.warnings} Kerr County warnings and updates on the night of July 4, 2025`],
      ["$0", `in compute between storms: everything runs on demand. The 14 alarms add ${usd(c.idle_month_usd.alarms)} a month`],
    ].map(([n, t]) => `<div class="tile"><b>${esc(n)}</b><span>${esc(t)}</span></div>`).join("");
    const pm = m.prices_per_million;
    $("co-note").textContent = `Run ${c.run_id}: ${fmt(m.input_tokens_total)} input tokens (${fmt(m.cache_read_tokens)} from cache) and ${fmt(m.output_tokens)} output on Claude Sonnet 4.6, at ${pm.input.usd}, ${pm.cache_read.usd} and ${pm.output.usd} US dollars per million input, cached and output tokens. The dashboard adds 3 dollars a month only beyond CloudWatch's three free dashboards. Measured ${c.measured_at} with python evals/cost.py.`;
  }

  // ---------- screen reader ----------
  function sr(r) {
    if (!r) return;
    const k = r.transcript_kept || {};
    $("sr-lead").innerHTML = `A real recorded run (${esc(r.run_id)}, ${r.events_replayed} events) replayed into the desk as it arrived. The transcript is ${k.same_node ? "appended to, never rebuilt" : "rebuilt on every poll"}, so an open panel ${k.details_open ? "stays open" : "closes"} and nothing is read twice. A polite live region says one sentence per change, refusals first. ${esc(r.limits)}`;
    $("sr-list").innerHTML = r.announcements.map((t) => `<li>${esc(t)}</li>`).join("");
  }

  function where() {
    const W = [
      ["CloudWatch dashboard: Countersign", "Safety, agent, paging, API and infrastructure in one view, with the AgentCore spans as a table.", "aws/observability.py"],
      ["Logs Insights", "Every refusal is a structured line. Try it on the hook's log group:", "filter event = \"hook_deny\" | stats count(*) by code"],
      ["Traces", "AgentCore spans land in aws/spans; X-Ray joins them to the API, hooks and paging. One trace per run.", "python evals/trace.py <run_id>"],
      ["Alarms and the ops topic", "14 alarms publish to the cs-ops SNS topic. Subscribe a phone to be woken by cs-unsafe.", "aws sns subscribe --topic-arn <cs-ops> --protocol sms"],
      ["Dead-letter queue", "Agent runs are never retried, so a failed run lands in cs-dlq at once; the recorder retries twice first. Paging is separate and does not depend on either.", "cs-dlq"],
      ["Measure it again", "Every number on this page below the alarms comes from a script in the repository.", "python evals/scale.py all && python evals/chaos.py run && python evals/permissions.py"],
    ];
    $("where").innerHTML = W.map(([h, p, c]) => `<div class="panel where"><h3>${esc(h)}</h3><p>${esc(p)}</p><code>${esc(c)}</code></div>`).join("");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    where();
    $("refresh").addEventListener("click", live);
    live();
    const [t, sc, a, ch, pm, co, srr] = await Promise.all([data("trace"), data("scale"), data("a11y"), data("chaos"),
      data("permissions"), data("cost"), data("screenreader")]);
    trace(t); scale(sc); a11y(a); chaos(ch); perm(pm); cost(co); sr(srr);
    window.CS_wire(document);
  });
})();
