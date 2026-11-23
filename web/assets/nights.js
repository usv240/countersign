/* Three nights, two clocks. Left: the public record (data/history.json). Right: Countersign's measured run (data/nights.json). */
(() => {
  "use strict";
  let esc, H, M, R = {}, current = 0;

  /* "8:05", "3:46 PM", "1:14 AM" to seconds after midnight. A bare time is morning. */
  function clockToSec(s) {
    const m = String(s).match(/(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?/i);
    if (!m) return null;
    let h = +m[1] % 12;
    if ((m[4] || "AM").toUpperCase() === "PM") h += 12;
    return h * 3600 + +m[2] * 60 + +(m[3] || 0);
  }
  function secToClock(t, withSec) {
    t = ((Math.round(t) % 86400) + 86400) % 86400;
    const h = Math.floor(t / 3600), mi = Math.floor((t % 3600) / 60), se = t % 60;
    const ap = h >= 12 ? "PM" : "AM", h12 = h % 12 || 12;
    return `${h12}:${String(mi).padStart(2, "0")}${withSec ? `:${String(se).padStart(2, "0")}` : ""} ${ap}`;
  }
  const dur = (s) => (s < 90 ? `${Math.round(s)} seconds` : s < 600 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : s < 5400 ? `${Math.round(s / 60)} minutes` : `${(s / 3600).toFixed(1).replace(/\.0$/, "")} hours`);
  const ruleName = (id) => { const r = (R.rules || []).find((x) => x.id === id); return r ? `${id}, ${r.name.toLowerCase()}` : id; };

  function srcLinks(ids, n) {
    return (ids || []).map((id) => `<a class="cite" href="#src-${n.id}-${id}" aria-label="Source: ${esc((H.sources[id] || {}).title || id)}">${(n._src.indexOf(id) + 1)}</a>`).join("");
  }

  /* Countersign's rows, built only from what the measured run recorded. */
  function csRows(n, m) {
    if (!m) return [];
    const rows = [];
    const add = (s, text, kind) => { if (s !== null && s !== undefined) rows.push({ s, text, kind, cs: true }); };
    add(m.first_page_s, "Officer A's pager goes off. The paging was started by the API before the agent ran, and the agent has no permission to stop it.");
    add(m.first_draft_s, "The agent drafts the alert from the official record: area by code from the warning, text from the approved action list, English and Spanish.");
    add(m.test_published_s, "A test copy goes to the test channel. No phone receives it.");
    add(m.correction_staged_s, "The correction is staged, for the same area, before anyone can approve anything.", "good");
    add(m.in_review_s, "The draft, the map, the rule results and the source are in front of the officer. No recommendation.");
    const d = m.decision;
    if (d) {
      if (d.sent) add(d.signed_after_s + d.sender_s, `Officer A signs. The sender re-checks all ten rules on its own stored copy, the solver returns ${esc(d.solver || "VALID")}, and the alert goes out once, ${d.sender_s} seconds after the signature.`, "good");
      else {
        const why = (d.failed_rules || []).map(ruleName).join("; ");
        const ppl = d.people_if_code_used ? Number(d.people_if_code_used).toLocaleString("en-US") : "";
        const extra = (ppl ? ` The officer's screen had said a ${n.id === "hawaii-2018" ? "statewide" : "countywide"} code reaches about ${ppl} people, and that only a deliberate confirmation can send to all of them.` : "")
          + (d.approvals_required > 1 ? ` It also needs ${d.approvals_required} different officers: the record did not come from an official feed.` : "");
        add(d.signed_after_s + d.sender_s, `Officer A approves it alone, as the real officer did. The sender refuses${why ? `: ${esc(why)}` : ""}. Nothing reaches a phone.${extra}`, "good");
      }
    }
    return rows;
  }

  function lanes(n) {
    const t0 = clockToSec(n.t0);
    const m = M[n.preset];
    const real = n.events.map((e) => ({ ...e, s: e.m === null || e.m === undefined ? null : e.m * 60 }));
    const cs = csRows(n, m);
    const timed = [...real.filter((e) => e.s !== null), ...cs].sort((a, b) => a.s - b.s || (a.cs ? -1 : 1));
    const untimed = real.filter((e) => e.s === null);
    const pre = untimed.filter((e) => /Jul 3|Jan 8/.test(e.t));
    const post = untimed.filter((e) => !pre.includes(e));
    const row = (e) => {
      const when = e.cs ? secToClock(t0 + e.s, true) : e.s !== null ? secToClock(t0 + e.s) + (/^Jan|^Jul/.test(e.t) ? "" : "") : esc(e.t);
      const since = e.s === null || e.s === undefined ? "" : e.s === 0 ? "the start" : `+${dur(e.s)}`;
      const body = e.cs ? `<p>${e.text}</p>` : `<p>${esc(e.text)}${srcLinks(e.src, n)}</p>`;
      return `<li class="${e.cs ? "cs" : "real"}${e.kind ? ` ${e.kind}` : ""}">
        <span class="when"><time>${when}</time>${since ? `<span class="since">${since}</span>` : ""}</span>
        <div class="lane"><span class="who">${e.cs ? "Countersign, measured" : "What happened"}</span>${body}</div></li>`;
    };
    return `<ol class="clocks" aria-label="${esc(n.title)}: both clocks in time order">${[...pre, ...timed, ...post].map(row).join("")}</ol>`;
  }

  function headline(n) {
    const m = M[n.preset] || {};
    const stats = [];
    if (n.id === "hawaii-2018") {
      stats.push(["38 minutes", "until the real correction reached phones"]);
      if (m.correction_staged_s !== undefined) stats.push([dur(m.correction_staged_s), "until Countersign's correction was staged, before any approval"]);
      if (m.decision) stats.push([m.decision.sent ? "sent" : "0 phones", "reached when one officer approved it alone"]);
    } else if (n.id === "los-angeles-2025") {
      stats.push(["about 10 million", "phones reached instead of one fire zone"]);
      if (m.correction_staged_s !== undefined) stats.push([dur(m.correction_staged_s), "until Countersign's correction was staged; the real one took about 20 minutes"]);
      if (m.decision) stats.push([m.decision.sent ? "sent" : "0 phones", "reached when the officer approved without confirming the whole county"]);
      stats.push(["1 per warning", "a second send of the same warning is refused (R10)"]);
    } else {
      stats.push(["3 h 47 min", "from the first warning to the county's first CodeRED, which reached only people signed up"]);
      if (m.first_page_s !== undefined) stats.push([dur(m.first_page_s), "until Countersign paged the duty officer"]);
      if (m.in_review_s !== undefined) stats.push([dur(m.in_review_s), "until a draft was in front of a person"]);
    }
    return `<div class="big-numbers night-stats">${stats.map(([v, l]) => `<div class="big"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("")}</div>`;
  }

  /* Kerr County with nobody answering: the measured escalation, then the same workflow at a real desk's timeout. */
  function escalation(n) {
    const m = M["kerr-no-answer"];
    if (n.id !== "kerr-2025-0114" || !m) return "";
    const pages = m.pages || [];
    const toState = pages.find((p) => p.to === "state");
    const officerPages = pages.filter((p) => /^Officer /.test(p.to)).length;
    const measured = toState ? `Measured on AWS with ${m.page_timeout_s}-second pages and nobody answering: ${officerPages} pages to Officers A, B and C, three rounds, then the state warning point at +${dur(toState.at_s)}.` : "";
    return `<section class="panel what-if" aria-labelledby="wi-h">
      <h3 id="wi-h">And if nobody at the county answers?<button class="i" data-tip-title="Measured, then projected" data-tip="The first sentence is a measured run (kerr-no-answer). The slider applies the same workflow, three officers and three rounds and then the state's 24-hour desk, to a timeout you choose. That part is arithmetic on the configuration, not a measurement."></button></h3>
      <p>${esc(measured)}</p>
      <div class="field"><label for="wi-min">Minutes before the page moves to the next person: <b id="wi-val">5</b></label>
        <input type="range" id="wi-min" min="1" max="15" step="1" value="5" aria-describedby="wi-out"></div>
      <p class="wi-out" id="wi-out" aria-live="polite"></p>
      <ol class="wi-pages" id="wi-pages" aria-label="Pages at this timeout"></ol>
    </section>`;
  }

  function wireEscalation(n) {
    const input = document.getElementById("wi-min");
    if (!input) return;
    const t0 = clockToSec(n.t0), m = M[n.preset] || {};
    const first = (m.first_page_s || 3);
    const emergency = clockToSec("4:03 AM"), codered = clockToSec("5:01 AM");
    const people = ["Officer A", "Officer B", "Officer C"];
    const draw = () => {
      const min = +input.value;
      document.getElementById("wi-val").textContent = min;
      const list = [];
      for (let r = 0; r < 3; r++) people.forEach((p, i) => list.push({ who: p, round: r + 1, at: t0 + first + (r * 3 + i) * min * 60 }));
      const state = t0 + first + 9 * min * 60;
      document.getElementById("wi-out").textContent =
        `At ${min} minute${min > 1 ? "s" : ""} a page, the state's 24-hour desk is paged at ${secToClock(state)}: ` +
        `${dur(emergency - state)} before the flash flood emergency and ${dur(codered - state)} before the county's first CodeRED. ` +
        `Every one of those pages carries the draft that was ready at ${secToClock(t0 + (m.in_review_s || 60))}.`;
      document.getElementById("wi-pages").innerHTML = list.map((x) => `<li><time>${secToClock(x.at)}</time> ${esc(x.who)}, round ${x.round}</li>`).join("")
        + `<li class="state"><time>${secToClock(state)}</time> State warning point, 24-hour desk</li>`;
    };
    input.addEventListener("input", draw);
    draw();
  }

  function sources(n) {
    return `<details class="sources"><summary>Sources for this night (${n._src.length})</summary><ol>${n._src.map((id) => {
      const s = H.sources[id] || {};
      return `<li id="src-${n.id}-${id}"><a href="${esc(s.url)}" rel="noopener" target="_blank">${esc(s.title)}</a>, ${esc(s.date)}</li>`;
    }).join("")}</ol></details>`;
  }

  function render(i) {
    current = i;
    const n = H.nights[i];
    n._src = [...new Set(n.events.flatMap((e) => e.src || []))];
    const m = M[n.preset];
    document.querySelectorAll("#night-tabs button").forEach((b, k) => b.setAttribute("aria-pressed", String(k === i)));
    document.getElementById("nights").innerHTML = `<article class="night" aria-labelledby="n-h">
      <header class="night-head"><h2 id="n-h">${esc(n.title)}</h2><p>${esc(n.failure)} ${esc(n.reach)}</p></header>
      ${headline(n)}
      <div class="clock-legend" aria-hidden="true"><span class="real">What happened, ${esc(n.tz)}</span><span class="cs">Countersign on AWS, same clock</span></div>
      ${lanes(n)}
      ${escalation(n)}
      ${n.contested ? `<p class="contested"><b>Where sources differ.</b> ${esc(n.contested)}</p>` : ""}
      ${m ? `<p class="small muted">Countersign's run: ${esc(m.run_id)}, ${m.tool_calls} tool calls, measured ${new Date(M.measured_at * 1000).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}. <a href="desk.html?preset=${esc(n.preset)}">Run this night yourself</a></p>` : ""}
      ${sources(n)}
    </article>`;
    window.CS_wire && window.CS_wire(document.getElementById("nights"));
    wireEscalation(n);
    try { history.replaceState(null, "", `#${n.id}`); } catch (e) { /* file: */ }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    esc = window.CS_esc;
    try {
      [H, M, R] = await Promise.all(["data/history.json", "data/nights.json", "data/rules.json"].map((u) => fetch(u).then((r) => r.json())));
    } catch (e) { document.getElementById("nights").innerHTML = '<p class="muted">The records did not load.</p>'; return; }
    const tabs = document.getElementById("night-tabs");
    tabs.innerHTML = H.nights.map((n, i) => `<button type="button" aria-pressed="${i === 0}">${esc(n.title.split(",")[0])}</button>`).join("");
    tabs.querySelectorAll("button").forEach((b, i) => b.addEventListener("click", () => render(i)));
    const want = H.nights.findIndex((n) => `#${n.id}` === location.hash);
    render(want >= 0 ? want : 0);
  });
})();
