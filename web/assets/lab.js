/* Test your data: presets, your own data, live warnings, the rule check and the debate. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  let A, esc;
  let presets = [], lexicon = {}, src = "preset", presetId = "kerr-2025-0114", liveId = null, area = "warning", eventCode = "";

  function setSeg(group, attr, value) {
    group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
  }

  function renderPresets() {
    $("#src-preset").innerHTML = presets.map((p) => `<div class="preset" aria-current="${p.id === presetId}">
        <b>${esc(p.title)}${p.modified ? '<span class="tag mod">modified copy</span>' : ""}</b>
        <div class="row">
          <a class="btn small secondary" href="presets/${esc(p.id)}.json" target="_blank" rel="noopener">View</a>
          <a class="btn small secondary" href="presets/${esc(p.id)}.json" download="countersign-${esc(p.id)}.json" aria-label="Download ${esc(p.title)}">${window.CS_ICONS.download}</a>
          <button class="btn small" type="button" data-use="${esc(p.id)}">Use</button></div>
        <p>${esc(p.summary)} ${esc(p.what_to_expect || "")}${p.modification ? ` <b>Changed:</b> ${esc(p.modification)}` : ""}</p></div>`).join("");
    $("#src-preset").querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => usePreset(b.dataset.use)));
  }

  function counts() {
    const a = $("#t360").value.length, b = $("#t90").value.length;
    $("#c360").textContent = `${a} of 360`; $("#c360").classList.toggle("over", a > 360);
    $("#c90").textContent = `${b} of 90`; $("#c90").classList.toggle("over", b > 90);
  }

  function renderActions(selected = []) {
    const list = Object.entries(lexicon).filter(([, v]) => !eventCode || v.events.includes(eventCode) || v.events.includes("*"));
    $("#actions").innerHTML = list.map(([id, v]) => `<label class="check" style="min-width:260px"><input type="checkbox" value="${esc(id)}"${selected.includes(id) ? " checked" : ""}> <span>${esc(v.en)}</span></label>`).join("")
      || `<span class="muted small">Choose data first.</span>`;
  }

  function fill(prop) {
    $("#t360").value = prop.text_360 || ""; $("#t90").value = prop.text_90 || ""; $("#t360es").value = prop.text_360_es || "";
    $("#klass").value = prop.alert_class === "Public Safety" ? "Public Safety" : "Imminent Threat";
    $("#who").value = prop.requested_by === "agent" ? "agent" : "officer";
    $("#corr").checked = !!prop.correction_staged;
    area = prop.polygon ? "warning" : ((prop.geocodes || []).length ? "code" : "none");
    setSeg($("#area-seg"), "area", area);
    renderActions(prop.action_ids || []);
    counts();
  }

  function caseBody() {
    const n = $("#nappr").value;
    const approvals = n === "1" ? [{ officer: "Officer A" }] : n === "2" ? [{ officer: "Officer A" }, { officer: "Officer B" }]
      : n === "same" ? [{ officer: "Officer A" }, { officer: "Officer A" }] : [];
    const proposal = {
      channel: "live", requested_by: $("#who").value, alert_class: $("#klass").value,
      text_360: $("#t360").value, text_90: $("#t90").value, text_360_es: $("#t360es").value,
      action_ids: Array.from(document.querySelectorAll("#actions input:checked")).map((i) => i.value),
      correction_staged: $("#corr").checked, approvals,
      whole_area_confirmed_by: $("#whole").checked ? "Officer A" : undefined,
    };
    const body = { proposal, area_mode: area, include_geometry: true };
    if (src === "preset") body.preset_id = presetId;
    else if (src === "live") { body.live_warning_id = liveId; body.county_geoid = $("#live-county").value.trim(); }
    else { body.warning = $("#own-text").value; body.county_geoid = $("#own-county").value.trim(); }
    return body;
  }

  async function check(silent) {
    const status = $("#run-status");
    status.textContent = "Checking on AWS...";
    const t0 = performance.now();
    try {
      const body = caseBody();
      if (src === "preset" || src === "live") {
        // warning_id must come from the server's own record, never from the page
        body.proposal.warning_id = window.__csWarningId || null;
      }
      const r = await window.CS_api("/api/evaluate", { body });
      eventCode = r.decision.facts.event_code || eventCode;
      $("#result").innerHTML = `<p class="small muted">${esc(r.source)}. ${esc(r.jurisdiction.name)}, population ${r.jurisdiction.population.toLocaleString()}.</p>
        ${A.outcome(r.decision)}<div id="lab-map"></div>${A.facts(r.decision)}${A.checks(r.decision)}`;
      A.map($("#lab-map"), { county: r.geometry && r.geometry.jurisdiction, warnings: (r.geometry && r.geometry.warning) || [], area: r.proposal.polygon });
      window.CS_wire($("#result"));
      status.textContent = `Answered by AWS Lambda in ${Math.round(performance.now() - t0)} ms.`;
      return r;
    } catch (e) {
      status.textContent = e.message;
      if (!silent) $("#result").innerHTML = `<div class="outcome blocked">${esc(e.message)}</div>`;
      return null;
    }
  }

  async function usePreset(id) {
    presetId = id; src = "preset"; renderPresets();
    try {
      const r = await window.CS_api("/api/evaluate", { body: { preset_id: id, include_geometry: true } });
      window.__csWarningId = r.proposal.warning_id || null;
      eventCode = r.decision.facts.event_code || "";
      fill(r.proposal);
      $("#nappr").value = "0"; $("#whole").checked = false;
      await check();
    } catch (e) { $("#run-status").textContent = e.message; }
  }

  async function fetchLive() {
    const c = $("#live-county").value.trim();
    $("#live-list").textContent = "Asking the National Weather Service...";
    try {
      const r = await window.CS_api(`/api/live?county=${encodeURIComponent(c)}`);
      if (!r.warnings.length) { $("#live-list").innerHTML = `No active warnings for ${esc(r.county)} right now. Try another county, or a preset.`; return; }
      $("#live-list").innerHTML = r.warnings.map((w) => `<div class="preset"><b>${esc(w.event)}</b><div class="row"><button class="btn small" data-live="${esc(w.id)}" type="button">Use</button></div><p>${esc(w.headline || "")}</p></div>`).join("");
      $("#live-list").querySelectorAll("[data-live]").forEach((b) => b.addEventListener("click", async () => {
        liveId = b.dataset.live; window.__csWarningId = liveId; src = "live";
        const res = await window.CS_api("/api/evaluate", { body: { live_warning_id: liveId, county_geoid: c, include_geometry: true } }).catch((e) => ({ error: e.message }));
        if (res.error) { $("#run-status").textContent = res.error; return; }
        eventCode = res.decision.facts.event_code || ""; fill(res.proposal); check();
      }));
    } catch (e) { $("#live-list").textContent = e.message; }
  }

  async function debate() {
    const btn = $("#debate-btn");
    btn.disabled = true;
    $("#debate").innerHTML = `<div class="thinking"><span class="spinner"></span>Claude Haiku 4.5 and Amazon Nova Pro are arguing. Claude Sonnet 4.6 will conclude.</div>`;
    try {
      const body = caseBody();
      if (src !== "own") body.proposal.warning_id = window.__csWarningId || null;
      const r = await window.CS_api("/api/debate", { body });
      const d = r.debate;
      const rulesSay = ["send_ok"].includes(r.rules.outcome) ? "SEND" : "HOLD";
      $("#debate").innerHTML = `<div class="debate">${d.turns.map((t) => `<div class="turn ${t.side}"><b>${t.side === "send" ? "Argues send" : "Argues hold"}: ${esc(t.model.split(".").slice(-1)[0].replace(/-v\d.*$/, ""))}</b>${esc(t.text)}</div>`).join("")}</div>
        <div class="versus" style="margin-top:12px">
          <div class="quote-box"><b>The moderator concludes</b><span style="font-size:20px;font-weight:600">${esc(d.conclusion.verdict)}</span><br>${esc(d.conclusion.reason)}</div>
          <div class="quote-box"><b>The rules decide</b><span style="font-size:20px;font-weight:600">${rulesSay}</span><br>${esc(r.rules.summary)}</div>
        </div>
        <p class="small muted" style="margin-top:10px">${d.conclusion.verdict === rulesSay ? "This time they agree. Run other cases: the rules give the same answer every time; the debate does not." : "They disagree. The rules hold the line; a debate's verdict can change from one run to the next."}</p>`;
    } catch (e) { $("#debate").innerHTML = `<div class="outcome blocked">${esc(e.message)}</div>`; }
    finally { btn.disabled = false; }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    A = window.CS_app; esc = window.CS_esc;
    try { presets = await (await fetch("data/presets.json")).json(); } catch (e) { presets = []; }
    try { lexicon = await (await fetch("data/lexicon.json")).json(); } catch (e) { lexicon = {}; }
    renderPresets(); renderActions();
    document.querySelectorAll("[data-src]").forEach((b) => b.addEventListener("click", () => {
      src = b.dataset.src;
      setSeg(b.parentElement, "src", src);
      ["preset", "own", "live"].forEach((k) => ($(`#src-${k}`).hidden = k !== src));
    }));
    $("#area-seg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { area = b.dataset.area; setSeg($("#area-seg"), "area", area); }));
    ["#t360", "#t90"].forEach((s) => $(s).addEventListener("input", counts));
    $("#run").addEventListener("click", () => check());
    $("#debate-btn").addEventListener("click", debate);
    $("#live-fetch").addEventListener("click", fetchLive);
    $("#dl-case").addEventListener("click", () => A.download("countersign-case.json", caseBody()));
    $("#own-file").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) $("#own-text").value = await f.text(); });
    $("#own-sample").addEventListener("click", async () => {
      const p = await (await fetch("presets/kerr-2025-0114.json")).json();
      $("#own-text").value = p.record.originalMessage;
      $("#own-county").value = "48265";
      A.toast("Loaded the real 1:14 AM Kerr County warning as plain CAP XML.");
    });
    usePreset(presetId);
  });
})();
