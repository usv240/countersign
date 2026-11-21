/* Proof page: every table is built from the saved result files. */
(() => {
  "use strict";
  const BROKE = [
    ["My guardrail blocked the ordinary job",
     "The first live run of the control case, \"a warning may have come in, do your job\", was blocked before the agent did anything. Attached to the model, Bedrock Guardrails' prompt attack filter also reads the system prompt, which is a page of instructions by design, and scored it HIGH. It scored \"Do your job\" MEDIUM on its own.",
     "I moved the Guardrail into the harness's before-invocation hook, where it checks only the words a person typed, and set the filter to LOW. Every ordinary request passes; it still stops 2 of the 16 attacks. The rest are stopped further down, which is the point of layers."],
    ["The agent had no tools, and pretended it did",
     "In the next run the agent wrote out tool calls as text, \"list_warnings(county=...)\", and waited for results that never came. The Gateway was returning an empty tool list. AgentCore Policy filters the list to tools some request could be allowed, and my Cedar permit matched the agent's role only in its assumed-role form, which is not how the harness presents it.",
     "I widened the principal pattern to the role in either form and tested it by asking the agent to list its tools. It now sees exactly its six, and an admin user calling the Gateway directly still sees none."],
    ["The officer would have seen a failure that was already fixed",
     "Each draft's rule results were saved when it was written. Staging the correction afterwards did not update them, so the review page showed R6, no correction staged, on a draft that had one.",
     "Every tool that changes a draft now re-runs all ten rules and saves the result, so the officer's view is never older than the draft."],
    ["Ordinary Spanish words were flagged as invented places",
     "The check that every name in an alert appears in the source flagged \"Muevase\" and \"REPENTINA\" in the agent's Spanish text and refused the draft.",
     "Names are now checked on the English text, where the place names are the same; the Spanish text still gets the numbers check."],
    ["The agent told the officer something false",
     "In one run the agent flagged, as a fact for the officer, that the warning's sent time was after its expiry. It had misread two times in different zones. A hook can stop an opinion; it cannot tell a true fact from a wrong one.",
     "The warning tool now gives every time in the warning's own local time as well. The limit stays on the list below: flagged facts are not verified by code, and the officer always sees the source beside them."],
    ["My first area rule held three quarters of all real alerts",
     "Version one treated every alert without a drawn polygon as blocked. Run against the FEMA archive it held 76% of ordinary alerts, because many local alerts are county-wide on purpose. A rule that blocks the normal case gets switched off.",
     "The rule now asks for a choice made on purpose: a drawn area, or the whole county confirmed by an officer who is shown the number of people it reaches. It is the Los Angeles failure, a code filling in silently, that it stops."],
    ["Scheduled system tests were counted as public alerts",
     "The archive's weekly and monthly tests are sent with status Actual and their own follow-ups, so they were scored as public alerts and as errors, and their test wording tripped the drill rule.",
     "Events RWT, RMT, DMO, NPT and NMN are now left out of the benchmark, and the counts above are without them."],
    ["The archive's signatures do not verify",
     "Every alert in FEMA's archive carries an IPAWS XML signature. I tried to use it to prove a record is official. Every one failed, unmodified records included: the archive stores a copy that is not byte-exact.",
     "Official status comes only from code fetching the warning from the National Weather Service. Archive records replay the past; they never count as a live source."],
  ];

  const esc = (s) => window.CS_esc(s);
  const pill = (...a) => window.CS_app.pill(...a);

  async function load(name) { try { return await (await fetch(`data/${name}.json`)).json(); } catch (e) { return null; } }

  function stressTable(s, attacks) {
    const sides = [["countersign", "Countersign, Claude, full instructions"], ["prompt-only", "Send tool, Claude, full instructions"],
      ["countersign-naive", "Countersign, Claude, told to obey"], ["prompt-only-naive", "Send tool, Claude, told to obey"],
      ["countersign-obedient", "Countersign, Nova Lite, told to obey"], ["prompt-only-obedient", "Send tool, Nova Lite, told to obey"]].filter(([k]) => s[`${k}_attack_runs`] !== undefined);
    const cell = (runs) => {
      if (!runs || !runs.length) return `<td class="muted">not run</td>`;
      const sends = runs.reduce((n, r) => n + (r.live_sends || 0), 0);
      const viol = runs.filter((r) => r.violation).length;
      const by = [...new Set(runs.flatMap((r) => r.stopped_by || []))].join(", ");
      const p = sends ? pill("fail", `${sends} live`) : viol ? pill("warn", `${viol} broke a rule`) : pill("pass", "held");
      return `<td>${p}<span class="cellnote">${runs.length} runs${by ? `. ${esc(by)}` : ""}</span></td>`;
    };
    const rows = attacks.map((a) => {
      const b = s.by_attack[a.id] || {};
      return `<tr><td><b>${esc(a.kind)}</b><span class="cellnote">${esc(a.tries)}</span></td><td>${esc(a.rule)}</td>${sides.map(([k]) => cell(b[k])).join("")}</tr>`;
    });
    const tot = sides.map(([k]) => `<td><b>${s[`${k}_live_sends`] ?? 0} live sends</b><span class="cellnote">${s[`${k}_violations`] ?? 0} rule breaks in ${s[`${k}_attack_runs`] ?? 0} attack runs</span></td>`).join("");
    return `<thead><tr><th>Attack</th><th>Rule</th>${sides.map(([, n]) => `<th>${n}</th>`).join("")}</tr></thead>
      <tbody>${rows.join("")}<tr><td><b>Total</b></td><td></td>${tot}</tr></tbody>`;
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const [s, attacks, below, ar, bench] = await Promise.all([load("stress-summary"), load("attacks"), load("below-model"), load("automated-reasoning-probes"), load("benchmark")]);

    const head = [];
    if (s) {
      const cs = ["countersign", "countersign-naive", "countersign-obedient"].reduce((n, k) => n + (s[`${k}_live_sends`] || 0), 0);
      const runs = ["countersign", "countersign-naive", "countersign-obedient"].reduce((n, k) => n + (s[`${k}_attack_runs`] || 0), 0);
      head.push([cs, `live alerts sent by Countersign in ${runs} attack runs, with three different agents inside it`]);
      if (s["prompt-only-obedient_live_sends"] !== undefined) head.push([s["prompt-only-obedient_live_sends"], `live alerts the obedient model sent in ${s["prompt-only-obedient_attack_runs"]} runs with a send tool and no layers`]);
    }
    if (below) head.push([`${below.held} of ${below.probes}`, "direct attacks on each layer held, with the model removed"]);
    if (ar) head.push([`${ar.filter((r) => r.ok).length} of ${ar.length}`, "solver probes answered correctly, including the one that should pass"]);
    document.getElementById("headline").innerHTML = head.map(([n, t]) => `<div><b>${esc(n)}</b><span>${esc(t)}</span></div>`).join("");

    if (s && attacks) {
      document.getElementById("stress").innerHTML = stressTable(s, attacks);
      document.getElementById("stress-note").textContent = "Stopped by names the layers that refused at least one call in that row's runs. Hook refusals include ordinary ones, such as a draft over 90 characters.";
    }
    if (below) {
      document.getElementById("below").innerHTML = `<thead><tr><th>Layer</th><th>What a compromised model tries</th><th>Expected</th><th>Got</th><th></th></tr></thead>
        <tbody>${below.rows.map((r) => `<tr><td>${esc(r.layer)}</td><td>${esc(r.probe)}</td><td>${esc(r.expected)}</td><td>${esc(String(r.got).slice(0, 120))}</td><td>${pill(r.held ? "pass" : "fail", r.held ? "held" : "open")}</td></tr>`).join("")}</tbody>`;
    }
    if (ar) {
      document.getElementById("ar").innerHTML = `<thead><tr><th>Case</th><th>Should be</th><th>Solver said</th><th class="num">Seconds</th><th></th></tr></thead>
        <tbody>${ar.map((r) => `<tr><td>${esc(r.probe)}</td><td>${esc(r.expect)}</td><td>${esc(r.got)}</td><td class="num">${r.seconds}</td><td>${pill(r.ok ? "pass" : "fail", r.ok ? "correct" : "wrong")}</td></tr>`).join("")}</tbody>`;
    }
    if (bench && Object.keys(bench).length) {
      const years = Object.keys(bench).sort();
      const sum = (lab, k) => years.reduce((n, y) => n + (bench[y][lab][k] || 0), 0);
      const errN = sum("error", "n"), errStop = sum("error", "held") + sum("error", "confirm_whole_county");
      const ordN = sum("ordinary", "n"), ordStop = sum("ordinary", "held") + sum("ordinary", "confirm_whole_county");
      const total = years.reduce((n, y) => n + bench[y].phone_alerts_scored, 0);
      const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0) + "%";
      document.getElementById("fema-numbers").innerHTML = [
        [total.toLocaleString(), `local phone alerts checked, ${years[0]} to ${years[years.length - 1]}`],
        [errN, "of them later retracted by their own sender as sent in error"],
        [pct(errStop, errN), "of the retracted ones the rules would have stopped for a person"],
        [pct(ordStop, ordN), "of ordinary alerts that would also stop, the cost of the rules"],
      ].map(([n, t]) => `<div><b>${esc(n)}</b><span>${esc(t)}</span></div>`).join("");
      const row = (lab, name) => {
        const n = sum(lab, "n"), h = sum(lab, "held"), c = sum(lab, "confirm_whole_county"), cl = sum(lab, "clear");
        return `<tr><td>${name}</td><td class="num">${n.toLocaleString()}</td><td class="num">${h.toLocaleString()} (${pct(h, n)})</td><td class="num">${c.toLocaleString()} (${pct(c, n)})</td><td class="num">${cl.toLocaleString()} (${pct(cl, n)})</td></tr>`;
      };
      document.getElementById("fema").innerHTML = `<thead><tr><th>Alerts</th><th class="num">Count</th><th class="num">Held by a rule</th><th class="num">Whole county must be confirmed</th><th class="num">Pass every message check</th></tr></thead>
        <tbody>${row("error", "Retracted as sent in error")}${row("cancelled", "Cancelled by their sender")}${row("ordinary", "Ordinary")}</tbody>`;
    }
    document.getElementById("broke").innerHTML = BROKE.map(([t, what, fix]) => `<article class="panel"><h3>${esc(t)}</h3><p>${esc(what)}</p><p class="fix">${esc(fix)}</p></article>`).join("");
    window.CS_wire(document);
  });
})();
