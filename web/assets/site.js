/* Countersign shared script: theme, info buttons, glossary, site bar, stack strip, footer, API helper. */
(() => {
  "use strict";

  // ---------- theme: light by default, remembered per visitor ----------
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("cs-theme"); } catch (e) { /* storage blocked */ }
  const asked = new URLSearchParams(location.search).get("theme");
  root.dataset.theme = (asked || saved) === "dark" ? "dark" : "light";

  const ICONS = {
    sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/></svg>',
    menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/></svg>',
    dot: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4" fill="currentColor"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    page: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>',
    bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
  };
  window.CS_ICONS = ICONS;

  // ---------- glossary: every term a newcomer meets ----------
  const TERMS = {
    wea: ["Wireless Emergency Alert", "The loud alert that appears on every phone in an area. It is sent by authorised agencies through FEMA's IPAWS system. Long alerts can be 360 characters; older phones get a 90-character version."],
    ipaws: ["IPAWS", "FEMA's Integrated Public Alert and Warning System. The gateway that carries alerts from about 1,800 authorised agencies to phones, radio and TV. Using it needs an agreement with FEMA, so Countersign uses a stand-in channel."],
    cap: ["CAP", "Common Alerting Protocol. The XML format every US public alert is written in: who sent it, the hazard, urgency, severity, certainty, the text and the map area."],
    nws: ["National Weather Service", "The US agency that issues official weather warnings, such as flash flood and tornado warnings. Countersign fetches them from its public API, api.weather.gov."],
    polygon: ["Polygon", "The shape on a map that decides which phones get an alert. A county code alone covers the whole county, which is how 10 million people in Los Angeles got an alert meant for one area."],
    imminent: ["Imminent Threat class", "A federal alert class (47 CFR 10.400) allowed only when urgency is Immediate or Expected, severity is Extreme or Severe, and certainty is Observed or Likely."],
    harness: ["AgentCore managed harness", "Amazon Bedrock AgentCore's managed agent loop. You declare the model, tools, hooks and limits as configuration; AWS runs the reasoning and tool calls in an isolated session."],
    gateway: ["AgentCore Gateway", "Turns Countersign's Lambda function into six tools the agent can call, over the MCP protocol. There is no live-send tool on it."],
    cedar: ["AgentCore Policy (Cedar)", "A policy engine attached to the Gateway. It denies every tool call unless a Cedar rule permits it, and checks the arguments, for example that the alert area is one of two allowed values."],
    hook: ["Tool-call hook", "A Lambda function the harness runs before every tool call. It can deny the call. It fails closed: if it errors or times out, the call is denied."],
    guardrail: ["Bedrock Guardrails", "Filters on the model's input. Countersign uses the prompt attack filter and a denied topic for credentials."],
    reasoning: ["Automated Reasoning checks", "A Bedrock Guardrails feature that checks a statement against rules written as formal logic, using a solver rather than another model. Countersign uses it as the second, independent check before a live send."],
    stepfn: ["Step Functions paging", "A workflow that pages the duty officer, waits, then pages the next person, round after round. Only an officer's decision ends it. The agent has no permission to touch it."],
    test: ["Test channel", "A stand-in for the phone network that no phone receives. The agent may publish here so a person can see exactly what would go out."],
    live: ["Live channel", "In this demo, an Amazon SNS topic standing in for IPAWS. Only the sender function, after officer approval and two independent checks, can publish to it."],
    countersign: ["Countersign", "A second signature that makes a document valid. Here: nothing reaches the public until a person signs what the agent drafted."],
    rule: ["Rule", "One of the ten plain-language rules the agent works under. Each is enforced outside the model, in at least one place the model cannot argue with."],
    openfema: ["OpenFEMA archive", "FEMA's public archive of every alert sent through IPAWS since 2012, about 4.9 million records. Countersign replays real records from it."],
    unsourced: ["Unsourced alert", "An alert with no official National Weather Service warning behind it, like Hawaii's missile alert. Countersign requires two different officers to approve one."],
    injection: ["Prompt injection", "Instructions hidden inside material an AI reads, such as a warning's text, aimed at the AI. Countersign treats all such text as data."],
  };
  window.CS_TERMS = TERMS;

  // ---------- tooltip engine for .i buttons and abbr[data-term] ----------
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.setAttribute("role", "tooltip");
  tip.id = "cs-tip";
  let current = null, hideTimer = null, lastClose = 0;

  function contentFor(el) {
    const term = el.dataset.term && TERMS[el.dataset.term];
    if (term) return `<strong>${term[0]}</strong>${term[1]}`;
    const t = el.dataset.tipTitle ? `<strong>${el.dataset.tipTitle}</strong>` : "";
    return t + (el.dataset.tip || "");
  }
  function place(el) {
    const r = el.getBoundingClientRect();
    tip.style.left = "0px"; tip.style.top = "0px";
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = r.left, y = r.bottom + 8, ox = "left", oy = "top";
    if (x + w > window.innerWidth - 12) { x = Math.max(12, r.right - w); ox = "right"; }
    if (y + h > window.innerHeight - 12) { y = r.top - h - 8; oy = "bottom"; }
    tip.style.left = `${Math.round(x)}px`; tip.style.top = `${Math.round(y)}px`;
    tip.style.setProperty("--origin", `${oy} ${ox}`);
  }
  function open(el) {
    clearTimeout(hideTimer);
    if (!tip.isConnected) document.body.appendChild(tip);
    if (current && current !== el) current.setAttribute("aria-expanded", "false");
    const instant = Date.now() - lastClose < 400 || tip.hasAttribute("data-open");
    tip.toggleAttribute("data-instant", instant);
    tip.innerHTML = contentFor(el);
    current = el;
    el.setAttribute("aria-expanded", "true");
    el.setAttribute("aria-describedby", "cs-tip");
    place(el);
    requestAnimationFrame(() => tip.setAttribute("data-open", ""));
  }
  function close() {
    if (!current) return;
    current.setAttribute("aria-expanded", "false");
    current.removeAttribute("aria-describedby");
    current = null;
    tip.removeAttribute("data-open");
    lastClose = Date.now();
  }
  const isTrigger = (t) => t && t.closest && t.closest(".i, abbr[data-term]");
  document.addEventListener("pointerover", (e) => {
    const el = isTrigger(e.target);
    if (el && e.pointerType === "mouse") open(el);
  });
  document.addEventListener("pointerout", (e) => {
    const el = isTrigger(e.target);
    if (el && e.pointerType === "mouse" && !el.contains(e.relatedTarget) && !tip.contains(e.relatedTarget)) {
      hideTimer = setTimeout(close, 120);
    }
  });
  tip.addEventListener("pointerleave", () => { hideTimer = setTimeout(close, 120); });
  tip.addEventListener("pointerenter", () => clearTimeout(hideTimer));
  document.addEventListener("focusin", (e) => { const el = isTrigger(e.target); if (el) open(el); });
  document.addEventListener("focusout", (e) => { if (isTrigger(e.target)) hideTimer = setTimeout(close, 80); });
  document.addEventListener("click", (e) => {
    const el = isTrigger(e.target);
    if (el) { e.preventDefault(); if (current === el && tip.hasAttribute("data-open")) close(); else open(el); return; }
    if (!tip.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  window.addEventListener("scroll", () => { if (current) place(current); }, { passive: true });

  // Turn every abbr[data-term] into a focusable trigger, and make every .i a real button.
  function wire(scope = document) {
    scope.querySelectorAll("abbr[data-term]").forEach((a) => { a.tabIndex = 0; a.setAttribute("aria-expanded", "false"); });
    scope.querySelectorAll(".i").forEach((b) => {
      if (b.tagName !== "BUTTON") return;
      b.type = "button";
      if (!b.textContent.trim()) b.textContent = "i";
      if (!b.getAttribute("aria-label")) b.setAttribute("aria-label", "Info: " + (b.dataset.tipTitle || "what this means"));
      b.setAttribute("aria-expanded", "false");
    });
  }
  window.CS_wire = wire;

  // ---------- site bar ----------
  const PAGES = [
    ["index.html", "Home"], ["rules.html", "How it works"], ["desk.html", "Alert desk"],
    ["lab.html", "Test your data"], ["proof.html", "Proof"], ["developers.html", "Developers"],
  ];
  function themeButton() {
    const b = document.createElement("button");
    b.className = "theme";
    const set = () => {
      const dark = root.dataset.theme === "dark";
      b.innerHTML = dark ? ICONS.sun : ICONS.moon;
      b.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    };
    set();
    b.addEventListener("click", () => {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem("cs-theme", root.dataset.theme); } catch (e) { /* ignore */ }
      set();
    });
    return b;
  }
  window.CS_themeButton = themeButton;

  function renderBar() {
    const bar = document.querySelector("header.bar");
    if (!bar) return;
    const here = location.pathname.split("/").pop() || "index.html";
    bar.innerHTML = `<a class="mark" href="index.html">Countersign</a>
      <nav aria-label="Main">${PAGES.map(([h, l]) => `<a href="${h}"${h === here ? ' aria-current="page"' : ""}>${l}</a>`).join("")}</nav>
      <button class="theme menu-btn" aria-label="Open menu" aria-expanded="false">${ICONS.menu}</button>`;
    bar.appendChild(themeButton());
    const mb = bar.querySelector(".menu-btn");
    mb.addEventListener("click", () => {
      const openNow = !bar.hasAttribute("data-open");
      bar.toggleAttribute("data-open", openNow);
      mb.setAttribute("aria-expanded", String(openNow));
    });
  }

  // ---------- built with: every AWS service and data source, on every page ----------
  const STACK = [
    ["core", "Amazon Bedrock AgentCore managed harness", "The agent itself. Claude Sonnet 4.6 runs in AWS's managed loop with a tool allowlist, a before-tool-call hook and hard limits on steps, tokens and time."],
    ["core", "AgentCore Gateway", "Exposes the six Countersign tools over MCP. A live-send tool does not exist on it."],
    ["core", "AgentCore Policy (Cedar)", "Default-deny policy engine on the Gateway: only the agent's role, only the six tools, only allowed argument values."],
    ["core", "Bedrock Guardrails", "Prompt attack filter and a denied topic on everything a person types to the agent."],
    ["core", "Automated Reasoning checks", "A solver holding the live-release rules as formal logic. It must agree with the code before anything is sent."],
    ["", "Claude Sonnet 4.6 on Amazon Bedrock", "The agent's model, called through a US cross-region inference profile."],
    ["", "Claude Haiku 4.5 and Amazon Nova Pro", "The two debaters in the send-versus-hold debate, chosen from different model families on purpose."],
    ["", "AWS Lambda", "Eight functions: tools, hook, sender, API, agent runner, pager, escalation, channel recorder. Each has its own role."],
    ["", "AWS Step Functions", "The paging workflow. It pages the call list until an officer decides. The agent cannot stop it."],
    ["", "Amazon DynamoDB", "Four tables. The agent's tool role cannot write the approvals table at all."],
    ["", "Amazon SNS", "The test, live and pager channels. Only the sender function may publish to live."],
    ["", "AWS IAM", "A separate least-privilege role per function, with explicit denies on the live channel and the approvals table."],
    ["", "Amazon API Gateway", "The public API, with throttling and API keys for developers."],
    ["", "Amazon CloudFront and S3", "Hosts this site, with security headers."],
    ["", "Amazon CloudWatch and AWS X-Ray", "Logs and traces for every function and every agent step."],
    ["", "FEMA OpenFEMA IPAWS archive", "The real alert records replayed here, including Hawaii 2018 and Los Angeles 2025."],
    ["", "National Weather Service API", "Live official warnings for any US county."],
    ["", "US Census Bureau TIGERweb", "County boundaries and 2020 population, used to check every alert area and count who it reaches."],
  ];
  window.CS_STACK = STACK;
  function renderStack() {
    document.querySelectorAll("[data-stack]").forEach((el) => {
      el.classList.add("stack");
      el.setAttribute("aria-label", "Built with");
      el.innerHTML = `<h2>Built with<button class="i" data-tip-title="What runs behind this page" data-tip="Every service and data source Countersign uses. The filled ones are the controls that keep the agent inside its rules."></button></h2>
        <ul>${STACK.map(([role, name, why]) => `<li data-role="${role}">${name}<button class="i" data-tip-title="${name}" data-tip="${why}"></button></li>`).join("")}</ul>`;
    });
  }

  function renderFoot() {
    const f = document.querySelector("footer.foot");
    if (!f) return;
    f.innerHTML = `
      <div><h3>Countersign</h3><ul><li>An AI alert desk that drafts and never sends alone.</li><li>Entry for Agents, your rules, AWS Builder Center, 2026.</li><li><a href="https://github.com/usv240/countersign">Code on GitHub, Apache-2.0</a></li></ul></div>
      <div><h3>Pages</h3><ul>${PAGES.map(([h, l]) => `<li><a href="${h}">${l}</a></li>`).join("")}</ul></div>
      <div><h3>Sources</h3><ul>
        <li><a href="https://docs.fcc.gov/public/attachments/DOC-350119A1.pdf">FCC report on the Hawaii alert</a></li>
        <li><a href="https://www.fema.gov/openfema-data-page/ipaws-archived-alerts-v1">OpenFEMA IPAWS archive</a></li>
        <li><a href="https://www.weather.gov/documentation/services-web-api">National Weather Service API</a></li>
        <li><a href="https://www.law.cornell.edu/cfr/text/47/10.400">47 CFR 10.400, alert classes</a></li></ul></div>
      <div><h3>Honest limits</h3><ul><li>No real phone receives these alerts. The live channel stands in for IPAWS, which needs a FEMA agreement.</li><li>Modified records are labelled as modified.</li></ul></div>
      <p class="legal">Not affiliated with or endorsed by FEMA, the National Weather Service or AWS. Public records are used as published; personal sender addresses are removed.</p>`;
  }

  // ---------- API helper ----------
  const API = (window.CS_API_BASE || "");
  async function api(path, opts = {}) {
    const res = await fetch(API + path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers: { "content-type": "application/json", ...(opts.headers || {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { data = { error: "The server sent something that is not JSON." }; }
    if (!res.ok) { const err = new Error(data.error || `HTTP ${res.status}`); err.status = res.status; err.data = data; throw err; }
    return data;
  }
  window.CS_api = api;
  window.CS_esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- section entrances: visible by default, lifted only when script runs ----------
  function entrances() {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches || !("IntersectionObserver" in window)) return;
    const items = document.querySelectorAll("[data-rise]");
    items.forEach((el) => el.classList.add("rise-ready"));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        const kids = en.target.querySelectorAll(":scope > *");
        kids.forEach((k, idx) => { k.style.transitionDelay = `${Math.min(idx, 6) * 70}ms`; });
        en.target.classList.add("risen");
        io.unobserve(en.target);
      });
    }, { rootMargin: "0px 0px -10% 0px" });
    items.forEach((el) => io.observe(el));
    // Never leave content hidden: anything not revealed within 2.5 s is shown anyway.
    setTimeout(() => items.forEach((el) => el.classList.add("risen")), 2500);
  }

  document.addEventListener("DOMContentLoaded", () => {
    renderBar(); renderStack(); renderFoot(); wire(); entrances();
    document.querySelectorAll("[data-theme-slot]").forEach((s) => s.appendChild(themeButton()));
  });
})();
