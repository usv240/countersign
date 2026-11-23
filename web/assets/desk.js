/* The alert desk: start a run, watch the agent, get paged, decide. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  let A, esc, I;
  let presets = [], attacks = [], selected = "kerr-2025-0114", listPrompt = "";
  let runId = null, token = null, pollTimer = null, geoCache = {}, lastDraftKey = "", seen = 0, lastPage = "";
  const toolNames = {};

  /* Screen readers hear one short sentence per change, not the whole transcript again. */
  function announce(text) {
    const el = document.getElementById("sr-announce");
    if (!el || !text) return;
    el.textContent = "";
    setTimeout(() => { el.textContent = text; }, 60);
  }
  const headOf = (html) => { const d = document.createElement("div"); d.innerHTML = html; const b = d.querySelector("b"); return b ? b.textContent.trim() : ""; };
  const OFFICERS = [["Officer A", "Duty officer"], ["Officer B", "Deputy coordinator"], ["Officer C", "Emergency management coordinator"]];

  function tokens() { try { return JSON.parse(localStorage.getItem("cs-officer") || "{}"); } catch (e) { return {}; } }
  function saveToken(id, t) { try { const all = tokens(); all[id] = t; localStorage.setItem("cs-officer", JSON.stringify(all)); } catch (e) { /* ignore */ } }

  function renderScenarios() {
    const items = presets.map((p) => `<button class="scen" type="button" data-id="${esc(p.id)}" aria-pressed="${p.id === selected}">
        <b>${esc(p.title)}${p.modified ? '<span class="tag mod">modified copy</span>' : ""}${p.kind === "attack" ? '<span class="tag">attack</span>' : ""}</b>
        <span>${esc(p.summary)}</span></button>`);
    items.push(`<button class="scen" type="button" data-id="live" aria-pressed="${selected === "live"}"><b>Live weather, any county<span class="tag">live</span></b><span>Today's real National Weather Service warnings. Often there are none, which is a valid result.</span></button>`);
    $("#scenarios").innerHTML = items.join("");
    $("#scenarios").querySelectorAll(".scen").forEach((b) => b.addEventListener("click", () => {
      selected = b.dataset.id; renderScenarios();
      $("#live-county").hidden = selected !== "live";
    }));
  }

  async function checkLive() {
    const c = $("#county").value.trim();
    $("#live-result").textContent = "Asking the National Weather Service...";
    try {
      const r = await window.CS_api(`/api/live?county=${encodeURIComponent(c)}`);
      $("#live-result").textContent = r.warnings.length
        ? `${r.county}: ${r.warnings.length} active: ${r.warnings.map((w) => w.event).join(", ")}.`
        : `${r.county}: no active warnings right now. The agent should find nothing and say so.`;
    } catch (e) { $("#live-result").textContent = e.message; }
  }

  async function start() {
    const btn = $("#start");
    btn.disabled = true;
    $("#start-status").textContent = "Starting the agent on AWS...";
    try {
      const message = $("#msg").value.trim();
      const body = { message, message_source: message && message === listPrompt.trim() ? "list" : "typed" };
      if (selected === "live") body.county_geoid = $("#county").value.trim(); else body.preset_id = selected;
      const r = await window.CS_api("/api/runs", { body });
      runId = r.run_id; token = r.officer_token; saveToken(runId, token);
      history.replaceState(null, "", `?run=${runId}`);
      seen = 0; lastDraftKey = ""; lastPage = "";
      $("#transcript").innerHTML = `<div class="thinking"><span class="spinner"></span>The agent is starting. Sessions take a few seconds to open.</div>`;
      $("#start-status").textContent = `Run ${runId} started. You are its officer in this browser.`;
      pocket();
      poll();
    } catch (e) {
      $("#start-status").textContent = e.message;
    } finally { btn.disabled = false; }
  }

  const plain = (t) => String(t || "").replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu, "")
    .replace(/\*\*(.+?)\*\*/g, "$1").replace(/^#+\s*/gm, "").replace(/^-{3,}$/gm, "").trim();

  function stepHtml(e) {
    const b = e.body || {};
    if (e.kind === "user") return `<div class="step user"><span class="ic">${I.page}</span><div><b>Message to the agent</b><div class="what">${esc(b.text)}</div></div></div>`;
    if (e.kind === "agent_text") return `<div class="step agent"><span class="ic">${I.dot}</span><div><b>The agent says</b><div class="what">${esc(plain(b.text))}</div></div></div>`;
    if (e.kind === "tool_call") {
      const name = String(b.tool || "").split("___").pop();
      if (b.id) toolNames[b.id] = name;
      const input = typeof b.input === "object" ? b.input : {};
      const brief = name === "draft_alert" ? (input.text_360 || "") : name === "submit_for_review" ? (input.concerns || []).join(" ") : "";
      return `<div class="step"><span class="ic">${I.arrow}</span><div><b>Calls ${esc(name)}</b>${brief ? `<div class="what">${esc(brief)}</div>` : ""}
        <details><summary>Arguments</summary><pre>${esc(JSON.stringify(b.input, null, 1))}</pre></details></div></div>`;
    }
    if (e.kind === "tool_result") {
      let r = b.result; let txt = typeof r === "string" ? r : JSON.stringify(r);
      const denied = /AuthorizeAction|policy enforcement|"error"/.test(txt);
      const tn = toolNames[b.id];
      let head = denied ? `A layer refused ${tn || "the call"}` : (tn ? `${tn} answered` : "Tool result");
      if (/AuthorizeAction|policy enforcement/.test(txt)) head = "Refused by the Cedar policy";
      let short = "";
      try { const o = typeof r === "string" ? JSON.parse(r) : r; short = o.error || o.summary || o.note || ""; } catch (x) { short = ""; }
      return `<div class="step${denied ? " deny" : ""}"><span class="ic">${denied ? I.x : I.check}</span><div><b>${head}</b>${short ? `<div class="what">${esc(short)}</div>` : ""}
        <details><summary>Full result</summary><pre>${esc(typeof r === "string" ? r : JSON.stringify(r, null, 1))}</pre></details></div></div>`;
    }
    if (e.kind === "hook") {
      const deny = b.decision === "deny";
      if (!deny) return "";
      return `<div class="step deny"><span class="ic">${I.x}</span><div><b>Refused by the ${b.type === "before_invocation" ? "Guardrails hook" : "tool-call hook"}</b><div class="what">${esc(b.reason || "")}</div></div></div>`;
    }
    if (e.kind === "error") return `<div class="step deny"><span class="ic">${I.warn}</span><div><b>Error</b><div class="what">${esc(JSON.stringify(b))}</div></div></div>`;
    if (e.kind === "send_attempt") {
      const ok = b.sent;
      return `<div class="step${ok ? "" : " deny"}"><span class="ic">${ok ? I.bell : I.x}</span><div><b>${ok ? "Sent live by the sender, after the officer signed" : "The sender refused to send"}</b>
        <div class="what">Code: ${esc(b.decision && b.decision.outcome)}. Solver: ${esc(b.automated_reasoning && b.automated_reasoning.status)}.</div></div></div>`;
    }
    return "";
  }

  /* The judge's own phone joins the run. The page it opens reads the same public run record as this desk. */
  function pocket() {
    const el = $("#pocket");
    if (!el || !runId) return;
    const url = new URL(`phone.html?run=${runId}`, location.href).href;
    let svg = "";
    try {
      const qr = window.qrcode(0, "M"); qr.addData(url); qr.make();
      svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true, alt: "QR code that opens this run's phone page" });
    } catch (e) { svg = ""; }
    el.innerHTML = `<div class="pocket">${svg ? `<div class="qr">${svg}</div>` : ""}
      <div><p class="small">Scan with your phone, then tap <b>Put this phone in the county</b>. It stays silent through everything the agent does, and rings when you sign here.</p>
      <p class="small"><a href="${url}" target="_blank" rel="noopener">Or open it in another window</a></p></div></div>`;
  }

  function renderPaging(run) {
    const pg = run.paging || {};
    const cfg = run.config || {};
    const decided = pg.done;
    const current = pg.contact && pg.contact.name;
    const timeout = cfg.page_timeout || 120;
    let left = "";
    if (pg.at && !decided) {
      const secs = Math.max(0, Math.round(pg.at + timeout - Date.now() / 1000));
      left = `${secs}s`;
    }
    const rows = OFFICERS.map(([n, role]) => {
      const now = current === n && !decided;
      return `<li class="${now ? "now" : ""}"><span>${esc(n)}, ${esc(role)}</span><span>${now ? `Paged now${left ? `, next in ${left}` : ""}` : ""}</span></li>`;
    });
    if (current === "State warning point") rows.push(`<li class="now"><span>State warning point, 24-hour desk</span><span>Escalated</span></li>`);
    if ((run.channel || []).some((c) => c.body && c.body.last_resort)) rows.push(`<li class="now"><span>State warning point, paged directly by the workflow</span><span>Paging functions failed</span></li>`);
    if (decided) rows.unshift(`<li class="done"><span>Paging ended: ${esc(pg.decided || "a decision was made")}</span><span></span></li>`);
    if (!pg.at && !decided) rows.unshift(`<li><span>Paging starts with Officer A</span><span>Round 1</span></li>`);
    else if (pg.round) rows.unshift(`<li><span>Round ${pg.round}</span><span>${timeout}s per person</span></li>`);
    $("#paging").innerHTML = rows.join("");
  }

  async function geometry(run, draft) {
    const key = `${run.config.preset_id || run.config.county_geoid}|${draft.draft_id}`;
    if (geoCache[key]) return geoCache[key];
    try {
      const body = run.config.preset_id ? { preset_id: run.config.preset_id, proposal: draft.proposal, include_geometry: true }
        : { county_geoid: run.config.county_geoid, proposal: draft.proposal, include_geometry: true };
      const r = await window.CS_api("/api/evaluate", { body });
      geoCache[key] = r.geometry;
    } catch (e) { geoCache[key] = null; }
    return geoCache[key];
  }

  async function renderReview(run) {
    const drafts = (run.drafts || []).slice().sort((a, b) => (a.submitted || a.created || 0) - (b.submitted || b.created || 0));
    const d = drafts.filter((x) => ["in_review", "sent", "rejected", "corrected"].includes(x.status)).pop() || drafts.pop();
    if (!d) return;
    const key = `${d.draft_id}|${d.status}|${(d.approvals || []).length}|${JSON.stringify(d.decision && d.decision.outcome)}`;
    if (key === lastDraftKey) return;
    lastDraftKey = key;
    const p = d.proposal, dec = d.decision;
    const mine = !!token;
    const whole = !p.polygon && (p.geocodes || []).length;
    const appr = (d.approvals || []).filter((a) => a.kind !== "correction");
    const geo = await geometry(run, d);
    const body = $("#review-body");
    body.innerHTML = `
      <p class="sub">Draft ${esc(d.draft_id)}, status: <b>${esc(d.status.replace("_", " "))}</b></p>
      <div class="compare2">
        <div class="quote-box"><b>Alert, 360 characters</b>${esc(p.text_360)}<div class="count">${(p.text_360 || "").length} of 360</div></div>
        <div class="quote-box"><b>Older phones, 90 characters</b>${esc(p.text_90)}<div class="count">${(p.text_90 || "").length} of 90</div>
          ${p.text_360_es ? `<b style="margin-top:8px">En espanol</b>${esc(p.text_360_es)}` : ""}</div>
      </div>
      <div id="review-map" style="margin-top:12px"></div>
      ${A.facts(dec)}
      ${A.outcome(dec)}
      ${A.checks(dec)}
      ${(d.concerns || []).length ? `<div class="quote-box" style="margin-top:12px"><b>Facts the agent flagged for you</b>${d.concerns.map(esc).join("<br>")}</div>` : ""}
      ${d.correction ? `<div class="quote-box" style="margin-top:12px"><b>Staged correction<button class="i" data-term="countersign"></button></b>${esc(d.correction.text_360)}</div>` : ""}
      <div style="margin-top:14px">${mine ? decideForm(d, whole, appr) : `<p class="muted small">Only the browser that started this run can act as its officer. Start your own run to decide.</p>`}</div>`;
    A.map(body.querySelector("#review-map"), { county: geo && geo.jurisdiction, warnings: (geo && geo.warning) || [], area: p.polygon });
    window.CS_wire(body);
    body.querySelectorAll("[data-decide]").forEach((b) => b.addEventListener("click", () => decide(d, b.dataset.decide, b.dataset.kind || "alert")));
  }

  function decideForm(d, whole, appr) {
    if (d.status === "sent") {
      return `<div class="outcome ok">Sent live. Signed by ${esc(appr.filter((a) => a.decision === "approve").map((a) => a.officer).join(" and "))}.</div>
        <div class="field"><label for="off2">If it was wrong: send the staged correction</label><select id="officer">${OFFICERS.map(([n]) => `<option>${n}</option>`).join("")}</select></div>
        <button class="btn danger" data-decide="approve" data-kind="correction" type="button">Send the correction</button>`;
    }
    if (d.status === "corrected") return `<div class="outcome ok">The correction went out to the same area.</div>`;
    return `<div class="field"><label for="officer">Sign as<button class="i" data-tip-title="Different people" data-tip="An unsourced alert needs two different officers. Signing twice as the same officer counts once."></button></label>
        <select id="officer">${OFFICERS.map(([n, r]) => `<option value="${n}">${n}, ${r}</option>`).join("")}</select></div>
      ${whole ? `<label class="check"><input type="checkbox" id="whole"> <span>I confirm this goes to the whole county, about ${((d.decision.facts || {}).people_if_code_used || 0).toLocaleString()} people.</span></label>` : ""}
      <div class="row" style="margin-top:12px"><button class="btn" data-decide="approve" type="button">Approve and send</button><button class="btn secondary" data-decide="reject" type="button">Reject</button></div>
      ${appr.length ? `<p class="small muted" style="margin-top:8px">Signed so far: ${esc(appr.map((a) => `${a.officer} (${a.decision})`).join(", "))}</p>` : ""}`;
  }

  async function decide(d, decision, kind) {
    const officer = ($("#officer") || {}).value || "Officer A";
    const body = { draft_id: d.draft_id, officer, decision, kind, officer_token: token, confirm_whole_county: !!($("#whole") && $("#whole").checked) };
    try {
      const r = await window.CS_api(`/api/runs/${runId}/decide`, { body });
      if (r.send) A.toast(r.send.sent ? "Sent to the live channel." : `Not sent: ${r.send.decision.summary}`);
      else A.toast(`${officer} ${decision}d it.`);
      lastDraftKey = "";
      // Keep polling: the live channel row lands a moment after the sender answers.
      poll();
      if (window.CS_tally) setTimeout(window.CS_tally, 25000);
    } catch (e) { A.toast(e.message); }
  }

  function renderPhones(run) {
    const ch = run.channel || [];
    const time = () => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    document.querySelectorAll("[data-clock]").forEach((c) => (c.textContent = time()));
    for (const which of ["test", "live"]) {
      const msgs = ch.filter((c) => c.channel === which);
      const el = $(`#phone-${which}`);
      el.innerHTML = `<div class="clock">${time()}</div>` + (msgs.length
        ? msgs.slice(-3).map((m) => `<div class="alert${which === "test" ? " test" : ""}"><b>${which === "test" ? "Test channel" : (m.body.kind === "correction" ? "Emergency Alert: correction" : "Emergency Alert")}</b>${esc(m.body.text)}</div>`).join("")
        : `<div class="empty">Nothing yet</div>`);
    }
  }

  async function poll(once) {
    clearTimeout(pollTimer);
    if (!runId) return;
    try {
      const run = await window.CS_api(`/api/runs/${runId}`);
      $("#run-id").textContent = `Run ${runId}`;
      const html = run.events.map(stepHtml).filter(Boolean);
      const status = run.config.agent_status;
      const tr = $("#transcript");
      // Append only the new steps, so open details stay open and assistive technology is not re-read the list.
      if (tr.dataset.run !== runId || html.length < seen) { tr.innerHTML = ""; tr.dataset.run = runId; seen = 0; }
      if (html.length > seen) {
        const think = tr.querySelector(".thinking");
        const fresh = html.slice(seen).join("");
        if (think) think.insertAdjacentHTML("beforebegin", fresh); else tr.insertAdjacentHTML("beforeend", fresh);
        // One sentence per poll: a refusal first, then the newest tool call, then anything else.
        const heads = html.slice(seen).map(headOf).filter(Boolean);
        const pickLast = (re) => heads.filter((h) => re.test(h)).pop();
        const key = pickLast(/^(Refused|A layer refused|The sender refused|Error)/) || pickLast(/^(Calls|Sent live)/) || heads[heads.length - 1];
        if (key) announce(heads.length > 1 ? `${key}. ${heads.length} new steps.` : `${key}.`);
        seen = html.length;
      }
      const working = status === "running" || status === "queued";
      const think = tr.querySelector(".thinking");
      if (working && !think) tr.insertAdjacentHTML("beforeend", `<div class="thinking"><span class="spinner"></span>The agent is working...</div>`);
      if (!working && think) { think.remove(); if (status === "done") announce("The agent has finished."); if (status === "error") announce("The agent stopped with an error."); }
      const pg = run.paging || {};
      const pageKey = pg.done ? `done:${pg.decided || ""}` : (pg.contact && pg.contact.name) || "";
      if (pageKey && pageKey !== lastPage) {
        if (lastPage || !pg.done) announce(pg.done ? `Paging ended: ${pg.decided || "a decision was made"}.` : `Paging ${pg.contact.name}, round ${pg.round || 1}.`);
        lastPage = pageKey;
      }
      renderPaging(run); renderPhones(run); await renderReview(run);
      const done = (status === "done" || status === "error") && run.paging && run.paging.done;
      if (!once) pollTimer = setTimeout(poll, done ? 8000 : 2000);
    } catch (e) {
      if (!once) pollTimer = setTimeout(poll, 5000);
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    A = window.CS_app; esc = window.CS_esc; I = window.CS_ICONS;
    try { presets = await (await fetch("data/presets.json")).json(); } catch (e) { presets = []; }
    try { attacks = await (await fetch("data/attacks.json")).json(); } catch (e) { attacks = []; }
    renderScenarios();
    $("#attacks").innerHTML += attacks.filter((a) => a.id !== "control").map((a) => `<option value="${esc(a.id)}">${esc(a.kind)}: ${esc(a.tries)}</option>`).join("");
    $("#attacks").addEventListener("change", (e) => {
      const a = attacks.find((x) => x.id === e.target.value);
      if (!a) return;
      $("#msg").value = a.prompt; listPrompt = a.prompt;
      if (a.preset && presets.some((p) => p.id === a.preset)) { selected = a.preset; renderScenarios(); }
    });
    $("#start").addEventListener("click", start);
    $("#check-live").addEventListener("click", checkLive);
    const q = new URLSearchParams(location.search);
    if (q.get("preset") && presets.some((p) => p.id === q.get("preset"))) { selected = q.get("preset"); renderScenarios(); }
    if (q.get("run")) { runId = q.get("run"); token = tokens()[runId] || null; pocket(); poll(); }
  });
})();
