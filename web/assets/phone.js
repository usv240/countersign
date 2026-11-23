/* A phone in the county. It reads the same public run record the desk reads, and rings only for the live channel. */
(() => {
  "use strict";
  const $ = (s) => document.getElementById(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const API = window.CS_API_BASE || "";
  const runId = (new URLSearchParams(location.search).get("run") || "").match(/^run_[a-f0-9]{16}$/) ? new URLSearchParams(location.search).get("run") : "";
  let joined = false, seenLive = new Set(), audio = null, wake = null, timer = null, started = Date.now(), lastQuiet = "";

  async function getRun() {
    const r = await fetch(`${API}/api/runs/${runId}`, { headers: { "content-type": "application/json" } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  function tick() {
    const d = new Date();
    $("clock").textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, "");
    $("date").textContent = d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  }

  /* The federal attention signal is two tones together, 853 and 960 Hz. Short and quiet here: it is a demo. */
  function ring() {
    try {
      if (audio) {
        const t = audio.currentTime, g = audio.createGain();
        g.gain.setValueAtTime(0.0001, t);
        g.connect(audio.destination);
        for (const f of [853, 960]) {
          const o = audio.createOscillator(); o.frequency.value = f; o.connect(g); o.start(t); o.stop(t + 2.6);
        }
        for (let i = 0; i < 3; i++) {
          g.gain.setTargetAtTime(0.12, t + i * 0.9, 0.01);
          g.gain.setTargetAtTime(0.0001, t + i * 0.9 + 0.6, 0.02);
        }
      }
    } catch (e) { /* no sound is fine */ }
    try { if (navigator.vibrate) navigator.vibrate([600, 250, 600, 250, 600]); } catch (e) { /* not supported */ }
  }

  function showAlert(m) {
    const b = m.body || {};
    const corr = b.kind === "correction";
    $("wea-h").textContent = corr ? "Emergency Alert: correction" : "Emergency Alert";
    $("wea-text").textContent = b.text || "";
    $("wea-text").hidden = false;
    $("wea-es").textContent = b.text_es || "";
    $("wea-es").hidden = true;
    $("wea-lang").hidden = !b.text_es;
    $("wea-lang").textContent = "En espanol";
    const who = (b.officers || []).join(" and ");
    $("wea-meta").textContent = `${b.county || ""}${who ? `. Signed at the desk by ${who}.` : ""}`;
    $("wea").hidden = false;
    document.body.classList.add("ringing");
    $("wea-ok").focus();
    ring();
  }

  function count(run) {
    const ev = run.events || [];
    const calls = ev.filter((e) => e.kind === "tool_call").length;
    const refused = ev.filter((e) => (e.kind === "hook" && e.body && e.body.decision === "deny")
      || (e.kind === "tool_result" && /AuthorizeAction|policy enforcement|"error"/.test(JSON.stringify(e.body && e.body.result)))).length;
    const typed = ev.find((e) => e.kind === "user");
    const attack = typed && !/A warning may have arrived for .* Do your job\.$/.test(typed.body.text || "");
    const tests = (run.channel || []).filter((c) => c.channel === "test").length;
    return { calls, refused, attack, tests };
  }

  function render(run) {
    const cfg = run.config || {};
    $("where").textContent = cfg.title || cfg.county_name || "";
    const c = count(run);
    const pg = run.paging || {};
    const live = (run.channel || []).filter((x) => x.channel === "live");
    const status = cfg.agent_status;
    const items = [];
    items.push(`<li><b>${c.calls}</b> tool calls by the agent so far${c.refused ? `, <b>${c.refused}</b> refused by a layer` : ""}.</li>`);
    if (c.attack) items.push(`<li>Someone typed a message to the agent. It reached the agent, not this phone.</li>`);
    if (c.tests) items.push(`<li>A test copy went to the test channel. No phone receives it.</li>`);
    if (pg.done) items.push(`<li>Paging ended: ${esc(pg.decided || "an officer decided")}.</li>`);
    else if (pg.contact && pg.contact.name) items.push(`<li>${esc(pg.contact.name)} is being paged, round ${esc(pg.round || 1)}.</li>`);
    $("watch").innerHTML = items.join("");
    const fresh = live.filter((m) => !seenLive.has(m.sk));
    let quiet = "Waiting for the agent to start.";
    if (status === "running") quiet = "The agent is drafting. This phone stays silent.";
    else if (status === "done") quiet = "The agent has finished. Only the officer's signature can make this phone ring.";
    else if (status === "error") quiet = "The agent stopped. The paging carries on without it.";
    if (live.length) quiet = `Rang ${live.length} time${live.length > 1 ? "s" : ""}, after an officer signed.`;
    $("quiet").querySelector("b").textContent = live.length ? "Alert received" : "Silent";
    $("quiet").classList.toggle("rang", live.length > 0);
    if (quiet !== lastQuiet) { $("quiet-line").textContent = quiet; lastQuiet = quiet; }
    $("alerts").innerHTML = live.slice().reverse().map((m) => `<div class="alert"><b>${m.body && m.body.kind === "correction" ? "Emergency Alert: correction" : "Emergency Alert"}</b>${esc(m.body && m.body.text)}</div>`).join("");
    fresh.forEach((m) => seenLive.add(m.sk));
    if (joined && fresh.length) showAlert(fresh[fresh.length - 1]);
  }

  async function poll() {
    clearTimeout(timer);
    try { render(await getRun()); } catch (e) { $("quiet-line").textContent = "Cannot reach the desk right now. Trying again."; }
    // Two hours is longer than any demo night; after that the page stops asking.
    if (Date.now() - started < 2 * 3600 * 1000) timer = setTimeout(poll, document.hidden ? 6000 : 2000);
  }

  async function join() {
    joined = true;
    try { audio = new (window.AudioContext || window.webkitAudioContext)(); await audio.resume(); } catch (e) { audio = null; }
    try { if ("wakeLock" in navigator) wake = await navigator.wakeLock.request("screen"); } catch (e) { wake = null; }
    $("join").hidden = true; $("lock").hidden = false;
    tick(); setInterval(tick, 15000);
    poll();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (!runId) { $("join-btn").hidden = true; $("join-missing").hidden = false; return; }
    try {
      const run = await getRun();
      if (run.config && run.config.county_name) $("join-county").textContent = run.config.county_name;
      // Alerts that already arrived are shown, not rung again.
      (run.channel || []).filter((c) => c.channel === "live").forEach((m) => seenLive.add(m.sk));
    } catch (e) { /* the lock screen will say so */ }
    $("join-btn").addEventListener("click", join);
    $("wea-ok").addEventListener("click", () => { $("wea").hidden = true; document.body.classList.remove("ringing"); });
    $("wea-lang").addEventListener("click", () => {
      const es = $("wea-es").hidden;
      $("wea-es").hidden = !es; $("wea-text").hidden = es;
      $("wea-lang").textContent = es ? "In English" : "En espanol";
    });
    document.addEventListener("visibilitychange", async () => {
      if (!document.hidden && joined && "wakeLock" in navigator && (!wake || wake.released)) {
        try { wake = await navigator.wakeLock.request("screen"); } catch (e) { /* fine */ }
      }
    });
  });
})();
