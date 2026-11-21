/* Developers: create a key, send a real request, examples in three languages. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const base = () => (window.CS_API_BASE || location.origin);
  const SAMPLE = {
    preset_id: "kerr-2025-0114",
    proposal: {
      requested_by: "officer", alert_class: "Imminent Threat",
      text_360: "Kerr County Emergency Management: NWS Flash Flood Warning for Kerr County until 4:15 AM CDT. Move to higher ground now.",
      text_90: "NWS: Flash Flood Warning in Kerr County til 4:15 AM CDT. Move to higher ground now.",
      action_ids: ["flood-higher-ground"], correction_staged: true, approvals: [],
    },
    area_mode: "warning",
  };
  const EXAMPLES = {
    hawaii: { label: "The Hawaii 2018 record, as sent", body: { preset_id: "hawaii-2018" } },
    la: { label: "The Los Angeles 2025 record, as sent", body: { preset_id: "los-angeles-2025" } },
    kerr: { label: "A good Kerr County draft", body: SAMPLE },
    invented: { label: "A draft with an invented time", body: { ...SAMPLE, proposal: { ...SAMPLE.proposal, text_360: SAMPLE.proposal.text_360.replace("4:15 AM", "9:00 AM") } } },
    own: { label: "Your own CAP XML, for county 48265", body: { county_geoid: "48265", warning: "<alert xmlns=\"urn:oasis:names:tc:emergency:cap:1.2\">...</alert>" } },
  };

  function examples(key) {
    const k = key || "cs_live_YOUR_KEY";
    const body = JSON.stringify(SAMPLE, null, 2);
    return {
      curl: `curl -s ${base()}/v1/evaluate \\\n  -H "x-api-key: ${k}" \\\n  -H "content-type: application/json" \\\n  -d '${JSON.stringify(SAMPLE)}'`,
      py: `import requests\n\nr = requests.post(\n    "${base()}/v1/evaluate",\n    headers={"x-api-key": "${k}"},\n    json=${body.replace(/true/g, "True").replace(/false/g, "False").replace(/null/g, "None")},\n    timeout=30,\n)\nd = r.json()["decision"]\nprint(d["outcome"])\nfor c in d["checks"]:\n    print(c["rule"], c["status"], c["message"])`,
      js: `const r = await fetch("${base()}/v1/evaluate", {\n  method: "POST",\n  headers: { "x-api-key": "${k}", "content-type": "application/json" },\n  body: JSON.stringify(${body}),\n});\nconst { decision } = await r.json();\nconsole.log(decision.outcome, decision.checks.filter((c) => c.status === "fail"));`,
    };
  }
  function renderExamples(key) {
    const ex = examples(key);
    document.querySelectorAll("[data-ex]").forEach((pre) => { pre.querySelector("code").textContent = ex[pre.dataset.ex]; });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const A = window.CS_app, esc = window.CS_esc;
    $("#base-url").textContent = base();
    $("#try-body").value = JSON.stringify(SAMPLE, null, 1);
    $("#try-preset").innerHTML += Object.entries(EXAMPLES).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");
    $("#try-preset").addEventListener("change", (e) => { const v = EXAMPLES[e.target.value]; if (v) $("#try-body").value = JSON.stringify(v.body, null, 1); });
    renderExamples();
    document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll("[data-lang]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      document.querySelectorAll("[data-ex]").forEach((pre) => (pre.hidden = pre.dataset.ex !== b.dataset.lang));
    }));
    $("#get-key").addEventListener("click", async () => {
      const btn = $("#get-key"); btn.disabled = true;
      try {
        const r = await window.CS_api("/api/keys", { body: { label: $("#label").value } });
        $("#key-out").innerHTML = `<div class="key-box"><span id="the-key">${esc(r.api_key)}</span></div>
          <p class="small muted" style="margin-top:8px">${esc(r.note)} Daily limit: ${r.daily_limit.toLocaleString()} calls. It is already filled in below and in the examples.</p>
          <button class="btn small secondary" id="copy-key" type="button" style="margin-top:8px">${window.CS_ICONS.copy} Copy key</button>`;
        $("#try-key").value = r.api_key;
        renderExamples(r.api_key);
        A.copyButtons();
        $("#copy-key").addEventListener("click", async () => { try { await navigator.clipboard.writeText(r.api_key); A.toast("Key copied."); } catch (e) { A.toast("Select the key and copy it."); } });
      } catch (e) { $("#key-out").innerHTML = `<div class="outcome blocked">${esc(e.message)}</div>`; }
      finally { btn.disabled = false; }
    });
    $("#try-send").addEventListener("click", async () => {
      const out = $("#try-out");
      let body;
      try { body = JSON.parse($("#try-body").value); } catch (e) { out.innerHTML = `<div class="outcome blocked">The body is not valid JSON: ${esc(e.message)}</div>`; return; }
      out.innerHTML = `<div class="thinking"><span class="spinner"></span>Calling POST /v1/evaluate...</div>`;
      const t0 = performance.now();
      try {
        const r = await window.CS_api("/v1/evaluate", { body, headers: { "x-api-key": $("#try-key").value.trim() } });
        out.innerHTML = `<p class="small muted">200 OK in ${Math.round(performance.now() - t0)} ms. Call ${r.usage.calls} of ${r.usage.daily_limit.toLocaleString()} today.</p>
          ${A.outcome(r.decision)}${A.checks(r.decision)}
          <details style="margin-top:10px"><summary>Raw JSON</summary><pre class="code"><code>${esc(JSON.stringify(r, null, 1))}</code></pre></details>`;
        window.CS_wire(out); A.copyButtons(out);
      } catch (e) {
        out.innerHTML = `<div class="outcome blocked">${e.status || ""} ${esc(e.message)}</div>`;
      }
    });
  });
})();
