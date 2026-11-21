/* Shared helpers for the working pages: rule checks, the map, toasts, copy buttons. */
(() => {
  "use strict";
  const esc = (s) => window.CS_esc(s);
  const I = () => window.CS_ICONS || {};

  const PILL = { pass: ["pass", "check", "Passes"], fail: ["fail", "x", "Blocks"], warn: ["warn", "warn", "Needs a person"], info: ["info", "dot", "Note"] };
  function pill(status, text) {
    const [cls, icon, label] = PILL[status] || PILL.info;
    return `<span class="pill ${cls}">${I()[icon] || ""}${esc(text || label)}</span>`;
  }

  const RULE_TIPS = {
    R1: "Only a warning code fetched from the National Weather Service counts as official. Anything typed, pasted or forwarded is unsourced and needs two officers.",
    R2: "The agent can never send live. A live alert needs one officer when it matches an official warning, two different officers when it does not.",
    R3: "Every live alert goes to an area someone chose: a drawn polygon inside the county, or the whole county confirmed on purpose with the number of people it reaches.",
    R4: "The alert may only state hazard, place and time found in the source. Every number must come from it. Actions come from the approved library. 360 characters, plus a 90-character version.",
    R5: "Imminent Threat only when urgency, severity and certainty meet the federal test in 47 CFR 10.400.",
    R6: "A correction is drafted before the alert goes out, so a mistake is fixed with one approval.",
    R7: "Paging runs outside the agent and only an officer's decision ends it.",
    R8: "Instructions found inside a warning, email or tool result are data. They change nothing.",
    R9: "The officer sees the source and the draft, never the agent's opinion of what to decide.",
    R10: "A warning that went out live cannot go out live again.",
  };
  window.CS_RULE_TIPS = RULE_TIPS;

  function checks(decision, opts = {}) {
    const list = (decision.checks || []).filter((c) => opts.all || !(c.status === "info" && ["R7", "R10", "R6"].includes(c.rule) && !c.message.startsWith("Paging:")));
    return `<ol class="checks2">${list.map((c) => `<li><span class="rid">${esc(c.rule)}</span><span>${esc(c.name)}<button class="i" data-tip-title="${esc(c.rule)}: ${esc(c.name)}" data-tip="${esc(RULE_TIPS[c.rule] || "")}"></button></span>${pill(c.status)}<span class="msg">${esc(c.message)}</span></li>`).join("")}</ol>`;
  }

  const OUTCOME = {
    blocked: ["blocked", "Held. No number of approvals can send this until the failing rule is fixed."],
    needs_1_approval: ["ready", "Ready for review. One officer approval can send it."],
    needs_2_approvals: ["ready", "Ready for review. Two different officers must approve."],
    send_ok: ["ok", "All checks pass and the approvals are in."],
    test_ok: ["ok", "Fine for the test channel."],
    test_blocked: ["blocked", "Not even fit for the test channel yet."],
  };
  function outcome(decision) {
    const [cls, text] = OUTCOME[decision.outcome] || ["ready", decision.summary];
    return `<div class="outcome ${cls}" role="status">${esc(text)}</div>`;
  }

  function facts(decision) {
    const f = decision.facts || {};
    const people = f.people_reached_est ? f.people_reached_est.toLocaleString() : (f.people_if_code_used ? f.people_if_code_used.toLocaleString() : "0");
    const share = f.share_of_jurisdiction ? Math.round(f.share_of_jurisdiction * 1000) / 10 + "%" : (f.people_if_code_used ? "100%" : "none");
    return `<div class="facts">
      <div><b>${people}</b><span>people it would reach<button class="i" data-tip-title="How this is counted" data-tip="The share of the county's land the area covers, times the county's 2020 Census population. An estimate: people are not spread evenly."></button></span></div>
      <div><b>${share}</b><span>of ${esc(f.jurisdiction || "the area")}</span></div>
      <div><b>${decision.approvals_present ?? 0} of ${decision.approvals_required ?? 0}</b><span>officer approvals</span></div>
    </div>`;
  }

  // ---------- map: county outline, warning polygon, alert area ----------
  function rings(geom) {
    if (!geom) return [];
    if (geom.type === "Polygon") return [geom.coordinates[0]];
    if (geom.type === "MultiPolygon") return geom.coordinates.map((p) => p[0]);
    return [];
  }
  function map(el, { county, warnings = [], area = null }) {
    const all = [...rings(county), ...warnings, ...(area ? [area] : [])].flat();
    if (!all.length) { el.innerHTML = `<div class="empty-state">No map to show.</div>`; return; }
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    all.forEach(([x, y]) => { minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y); });
    const k = Math.cos(((miny + maxy) / 2) * Math.PI / 180);
    const W = 400, H = 300, pad = 14;
    const sx = (W - 2 * pad) / Math.max((maxx - minx) * k, 1e-6), sy = (H - 2 * pad) / Math.max(maxy - miny, 1e-6);
    const s = Math.min(sx, sy);
    const ox = (W - (maxx - minx) * k * s) / 2, oy = (H - (maxy - miny) * s) / 2;
    const pt = ([x, y]) => `${(ox + (x - minx) * k * s).toFixed(1)},${(oy + (maxy - y) * s).toFixed(1)}`;
    const path = (ring) => "M" + ring.map(pt).join("L") + "Z";
    el.innerHTML = `<svg class="map" viewBox="0 0 ${W} ${H}" role="img" aria-label="Map: the county outline, the warning's polygon and the alert area">
      ${rings(county).map((r) => `<path class="county" d="${path(r)}"/>`).join("")}
      ${warnings.map((r) => `<path class="warning" d="${path(r)}"/>`).join("")}
      ${area ? `<path class="area" d="${path(area)}"/>` : ""}
    </svg>
    <div class="legend"><span><i style="background:var(--surface);outline:1.2px solid var(--soft)"></i>County</span>
      <span><i style="outline:1.5px dashed var(--fail)"></i>Warning's own polygon</span>
      <span><i style="background:color-mix(in srgb, var(--chart) 28%, transparent);outline:2px solid var(--chart)"></i>Alert area</span></div>`;
  }

  function toast(text) {
    let t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.textContent = text;
    t.setAttribute("data-open", "");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.removeAttribute("data-open"), 2600);
  }

  function copyButtons(scope = document) {
    scope.querySelectorAll("pre.code").forEach((pre) => {
      if (pre.querySelector(".copy")) return;
      const b = document.createElement("button");
      b.className = "copy"; b.type = "button"; b.textContent = "Copy";
      b.addEventListener("click", async () => {
        const text = pre.querySelector("code") ? pre.querySelector("code").textContent : pre.textContent.replace(/Copy$/, "");
        try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch (e) { b.textContent = "Select and copy"; }
        setTimeout(() => (b.textContent = "Copy"), 1600);
      });
      pre.appendChild(b);
    });
  }

  function download(name, obj) {
    const blob = new Blob([typeof obj === "string" ? obj : JSON.stringify(obj, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  window.CS_app = { pill, checks, outcome, facts, map, toast, copyButtons, download, esc };
  document.addEventListener("DOMContentLoaded", () => copyButtons());
})();
