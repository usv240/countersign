/* Landing page: the masked headline, the replay of three real nights, the rules line-up and the lit names. */
(() => {
  "use strict";
  const html = document.documentElement;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduce) html.classList.add("js");
  const ready = () => requestAnimationFrame(() => html.classList.add("ready"));
  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(ready);
  setTimeout(ready, 1200);

  const esc = (s) => window.CS_esc ? window.CS_esc(s) : String(s ?? "");
  const I = () => window.CS_ICONS || {};
  const pill = (status) => {
    const map = { pass: ["pass", "check", "Passes"], fail: ["fail", "x", "Blocks"], warn: ["warn", "warn", "Needs a person"], info: ["info", "dot", "Note"] };
    const [cls, icon, label] = map[status] || map.info;
    return `<span class="pill ${cls}">${I()[icon] || ""}${label}</span>`;
  };
  const fmtTime = (iso, tz) => {
    try {
      return new Date(iso).toLocaleString("en-US", { timeZone: tz, month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
    } catch (e) { return iso; }
  };
  const TZ = { "hawaii-2018": "Pacific/Honolulu", "los-angeles-2025": "America/Los_Angeles", "kerr-2025-0114": "America/Chicago" };

  let nights = [], current = 0;

  function renderNight(n, decisionOverride) {
    const r = n.record;
    const kerr = n.id.startsWith("kerr");
    document.getElementById("call-record").innerHTML = `
      <dl>
        <dt>Sent</dt><dd>${esc(fmtTime(r.sent, TZ[n.id]))}</dd>
        <dt>From</dt><dd>${esc(r.sender_name)}</dd>
        <dt>Event</dt><dd>${esc(r.event)}</dd>
        <dt>Urgency</dt><dd>${esc(r.urgency)}, ${esc(r.severity)}, ${esc(r.certainty)}<button class="i" data-term="imminent"></button></dd>
        <dt>Area</dt><dd>${esc(r.area_desc)} (code ${esc(r.same.join(", "))})</dd>
        <dt>Map area</dt><dd>${r.has_polygon ? `A drawn <abbr data-term="polygon">polygon</abbr>` : `None. Only the county code.<button class="i" data-term="polygon"></button>`}</dd>
      </dl>
      <p class="quote">${esc(r.text_90 || r.headline)}</p>`;
    document.getElementById("resp-source").innerHTML = r.official
      ? `<p class="verdict">${pill("pass")}<span>An official <abbr data-term="nws">National Weather Service</abbr> warning, fetched by code. One officer may approve an alert based on it.</span></p>`
      : `<p class="verdict">${pill("warn")}<span>Not an official weather warning, so it is <abbr data-term="unsourced">unsourced</abbr>. Two different officers would have to sign before it could go out.</span></p>`;

    const draft = kerr && n.countersign_draft;
    const d = decisionOverride || (draft ? draft.decision : n.as_sent);
    document.getElementById("call-proposal").innerHTML = draft
      ? `<p>The county sent nothing. This is the alert Countersign's agent drafts from the warning, clipped to the county:</p><p class="quote">${esc(draft.proposal.text_360)}</p>`
      : `<p>Proposal: send this record live again, exactly as it went out that night.</p><p class="quote">${esc(r.text_90 || r.headline)}</p>`;
    const checks = d.checks.filter((c) => !(c.rule === "R7" && c.status === "info"));
    document.getElementById("resp-checks").innerHTML = `<ol class="checks">${checks.map((c) => `
      <li><span class="rid">${esc(c.rule)}</span><span>${esc(c.name)}</span>${pill(c.status)}<span class="msg">${esc(c.message)}</span></li>`).join("")}</ol>
      <p class="summary-line">${esc(d.summary)}</p>`;
    const lis = document.querySelectorAll("#resp-checks .checks li");
    lis.forEach((li, idx) => setTimeout(() => li.classList.add("on"), reduce ? 0 : 70 * idx));

    const people = d.facts.people_reached_est || 0;
    const that = kerr
      ? `<div class="phone"><div class="screen"><div class="clock">1:14</div><div class="date">Friday, July 4</div><div class="empty">No alert from Kerr County</div></div></div>
         <p>${esc(n.what_happened)}</p>`
      : `<div class="phone"><div class="screen"><div class="clock">${n.id.startsWith("hawaii") ? "8:07" : "3:56"}</div><div class="date">${n.id.startsWith("hawaii") ? "Saturday, January 13" : "Thursday, January 9"}</div>
           <div class="alert"><b>Emergency Alert</b>${esc(r.text_90 || r.headline)}</div></div></div>
         <p>${esc(n.what_happened)} Reached: ${esc(n.reached)}.</p>`;
    let ours;
    if (n.id.startsWith("hawaii")) {
      ours = `<div class="phone"><div class="screen"><div class="clock">8:07</div><div class="date">Saturday, January 13</div><div class="empty">Nothing sent. Held for two officers, a map area and a staged correction.</div></div></div>
        <p>Countersign would hold it before any phone. If an alert ever did go out by mistake, its correction is already drafted and needs one approval, not 38 minutes.</p>`;
    } else if (n.id.startsWith("los")) {
      ours = `<div class="phone"><div class="screen"><div class="clock">3:56</div><div class="date">Thursday, January 9</div><div class="empty">Nothing sent. An officer must draw the fire area, or confirm all ${(d.facts.people_if_code_used || n.population).toLocaleString()} people on purpose.</div></div></div>
        <p>The county code alone is never treated as an area. The officer sees the number of people it would reach before choosing.</p>`;
    } else {
      ours = `<div class="phone"><div class="screen"><div class="clock">1:15</div><div class="date">Friday, July 4</div>
          <div class="alert test"><b>Test channel</b>TEST. ${esc(draft.proposal.text_90)}</div><div class="empty">Officer A paged at 1:15. No answer in 2 minutes pages Officer B, then C, then the state desk.</div></div></div>
        <p>Drafted from the 1:14 AM warning, about ${people.toLocaleString()} people inside the warning area. One approval sends it live. Nobody can turn the paging off except by deciding.</p>`;
    }
    document.getElementById("resp-result").innerHTML = `<div class="side"><h3>That night</h3>${that}</div><div class="side"><h3>With Countersign</h3>${ours}</div>`;
    if (window.CS_wire) window.CS_wire(document.getElementById("replay"));
  }

  function select(idx) {
    current = idx;
    document.querySelectorAll(".tab").forEach((t, i) => { t.setAttribute("aria-selected", String(i === idx)); t.tabIndex = i === idx ? 0 : -1; });
    document.getElementById("run-status").textContent = "";
    renderNight(nights[idx]);
  }

  async function runLive() {
    const n = nights[current];
    const status = document.getElementById("run-status");
    const btn = document.getElementById("run-live");
    btn.disabled = true;
    status.textContent = "Sending the record to the API on AWS...";
    const t0 = performance.now();
    try {
      const body = n.countersign_draft ? { preset_id: n.id, proposal: n.countersign_draft.proposal } : { preset_id: n.id };
      const out = await window.CS_api("/api/evaluate", { body });
      renderNight(n, out.decision);
      status.textContent = `Answered by AWS Lambda in ${Math.round(performance.now() - t0)} ms. Same result as the page.`;
    } catch (e) {
      status.textContent = `The API did not answer (${e.message}). The result shown is the engine's own, computed in advance.`;
    } finally { btn.disabled = false; }
  }

  async function initReplay() {
    try {
      const res = await fetch("data/replays.json");
      nights = (await res.json()).nights;
    } catch (e) { return; }
    const tabs = document.querySelector(".tabs");
    tabs.innerHTML = nights.map((n, i) => `<button class="tab" role="tab" id="tab-${n.id}" aria-selected="${i === 0}" type="button">${esc(n.short)}</button>`).join("");
    tabs.querySelectorAll(".tab").forEach((t, i) => t.addEventListener("click", () => select(i)));
    tabs.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = (current + (e.key === "ArrowRight" ? 1 : nights.length - 1)) % nights.length;
      select(next); tabs.querySelectorAll(".tab")[next].focus();
    });
    document.getElementById("run-live").addEventListener("click", runLive);
    select(0);
  }

  async function initRules() {
    let data;
    try { data = await (await fetch("data/rules.json")).json(); } catch (e) { return; }
    const where = {
      tool_backend: "tool code", hook: "tool-call hook", approval_service: "sender checks", harness_tools: "tool allowlist",
      gateway_cedar: "Cedar policy", iam: "IAM", automated_reasoning: "solver", step_functions: "paging workflow", guardrail: "Guardrail",
    };
    const row = (r) => `<li><span>${esc(r.name)}</span><span>${esc(r.must_refuse)}</span><span>${esc(r.enforced_by.map((x) => where[x] || x).join(", "))}</span><span>${esc((data.failures[r.failure] || {}).title ? data.failures[r.failure].title.split(",")[0] : "")}</span></li>`;
    const always = new Set(["R6", "R7", "R1"]);
    document.getElementById("rule-rows-never").innerHTML = data.rules.filter((r) => !always.has(r.id)).map(row).join("");
    document.getElementById("rule-rows-always").innerHTML = data.rules.filter((r) => always.has(r.id)).map((r) => row({ ...r, must_refuse: r.says.split(".")[0] + "." })).join("");
  }

  function initNames() {
    const names = Array.from(document.querySelectorAll("#names-run .n"));
    if (!names.length) return;
    let k = 0;
    names[0].classList.add("on");
    if (reduce) return;
    setInterval(() => { names[k].classList.remove("on"); k = (k + 1) % names.length; names[k].classList.add("on"); }, 2000);
  }

  async function initCompare() {
    try {
      const r = await (await fetch("data/stress-summary.json")).json();
      const a = r["prompt-only-obedient_live_sends"], b = r["countersign-obedient_live_sends"];
      if (a === undefined || b === undefined) return;
      const runs = r["prompt-only-obedient_attack_runs"];
      document.getElementById("spec-a").textContent = a;
      document.getElementById("spec-b").textContent = b;
      document.getElementById("spec-a-cap").textContent = `live alerts sent in ${runs} attack runs, with a send tool and nothing behind it`;
      document.getElementById("spec-b-cap").textContent = `live alerts sent by the same model in the same ${runs} runs, inside Countersign`;
    } catch (e) { /* keep the measured figures written into the page */ }
  }

  document.addEventListener("DOMContentLoaded", () => { initReplay(); initRules(); initNames(); initCompare(); });
})();
