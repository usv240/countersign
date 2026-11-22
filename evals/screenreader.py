"""What a screen reader hears at the alert desk while the agent works.

The desk changes every two seconds while a run is live. This replays a real recorded agent run into the deployed
desk page, a few events per poll as it arrived, and records:
  * every sentence the page's polite live region announces, in order,
  * whether the transcript is appended to or rebuilt (a rebuilt list is re-read, and loses an open details panel),
  * the page's accessibility tree (Playwright ARIA snapshot) of the transcript, paging and decision areas.

    python evals/screenreader.py [run_id]       # writes evals/results/screenreader.json

This is an automated check of what is exposed to assistive technology. It is not a test by a screen-reader user.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "results" / "screenreader.json"
SITE = json.loads((ROOT / "aws" / "state.json").read_text())["site_url"]


def main(run_id: str) -> dict:
    full = requests.get(f"{SITE}/api/runs/{run_id}", timeout=30).json()
    events = full["events"]
    paging_steps = [{"contact": {"name": "Officer A"}, "round": 1, "at": time.time()},
                    {"contact": {"name": "Officer B"}, "round": 1, "at": time.time()},
                    {"contact": {"name": "Officer A"}, "round": 1, "at": time.time(), "done": True,
                     "decided": "Officer A rejected the alert"}]
    state = {"n": 0, "polls": 0}

    def snapshot():
        n = state["n"]
        cfg = dict(full["config"], agent_status="running" if n < len(events) else "done")
        pg = paging_steps[min(2, state["polls"] // 6)] if n >= len(events) else paging_steps[min(1, state["polls"] // 6)]
        return {**full, "config": cfg, "events": events[:n], "drafts": full["drafts"] if n >= len(events) else [],
                "channel": [], "paging": pg}

    def handle(route):
        state["polls"] += 1
        state["n"] = min(len(events), state["n"] + 4)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(snapshot()))

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 900})
        pg.route(f"**/api/runs/{run_id}", handle)
        pg.goto(f"{SITE}/desk.html?run={run_id}", wait_until="networkidle")
        assert pg.evaluate("!!document.getElementById('sr-announce')"), "deployed desk has no live region yet"
        pg.evaluate("""() => { window.__heard = []; const el = document.getElementById('sr-announce');
            new MutationObserver(() => { const t = el.textContent.trim(); if (t) window.__heard.push(t); })
              .observe(el, {childList: true, characterData: true, subtree: true}); }""")
        # open the first step's details, then keep polling: it must stay open and the first node must be the same one
        pg.wait_for_selector("#transcript .step details", timeout=20000)
        pg.evaluate("""() => { const d = document.querySelector('#transcript .step details'); d.open = true;
            window.__first = document.querySelector('#transcript .step'); window.__details = d; }""")
        t0 = time.time()
        while time.time() - t0 < 90 and (state["n"] < len(events) or state["polls"] < 22):
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(4000)
        kept = pg.evaluate("""() => ({ same_node: document.querySelector('#transcript .step') === window.__first,
            details_open: !!(window.__details && window.__details.isConnected && window.__details.open),
            steps: document.querySelectorAll('#transcript .step').length })""")
        heard = pg.evaluate("window.__heard")
        aria = {}
        for sel, name in (("#transcript", "transcript"), ("#paging", "paging"), ("#review", "decision")):
            try:
                aria[name] = pg.locator(sel).aria_snapshot()[:3000]
            except Exception as e:
                aria[name] = f"unavailable: {type(e).__name__}"
        live_regions = pg.evaluate("""() => [...document.querySelectorAll('[aria-live],[role=status],[role=alert],[role=log]')]
            .map(e => ({id: e.id || null, role: e.getAttribute('role'), live: e.getAttribute('aria-live')}))""")
        br.close()
    res = {"measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "run_id": run_id,
           "events_replayed": len(events), "polls": state["polls"], "announcements": heard,
           "announcement_count": len(heard), "transcript_kept": kept, "live_regions": live_regions, "aria": aria,
           "held": kept["same_node"] and kept["details_open"] and len(heard) >= 3,
           "limits": "Automated: this checks what the page exposes to assistive technology. No screen-reader user "
                     "has tested it."}
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "aria"}, indent=1))
    return res


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run_74dd1b7866e549ee")
