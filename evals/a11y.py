"""Accessibility audit of the live site: axe-core (WCAG 2.2 A and AA) plus three checks axe cannot make.

    python evals/a11y.py [--axe path/to/axe.min.js] [--site https://...]

For every page, in light and dark, at desktop (1440 px) and phone (390 px) width:
  * axe-core with the wcag2a, wcag2aa, wcag21a, wcag21aa and wcag22aa rule tags,
  * keyboard: Tab through the page; every stop must show a visible focus indicator,
  * zoom: at 200% text size (WCAG 1.4.4) and at 320 px width (1.4.10 reflow), no sideways scrolling,
  * motion: with prefers-reduced-motion set, nothing keeps animating.
Writes evals/results/a11y.json. Without --axe, axe-core 4.10.2 is loaded from cdnjs.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "results" / "a11y.json"
PAGES = [p for p in ("index.html", "desk.html", "lab.html", "developers.html", "rules.html", "proof.html", "status.html", "404.html")
         if (ROOT / "web" / p).exists()]
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"

def run(site: str, axe_path: str | None) -> dict:
    axe_src = Path(axe_path).read_text(encoding="utf-8") if axe_path else None
    rows, totals = [], {"pages": 0, "violations": 0, "nodes": 0}
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for theme in ("light", "dark"):
            for vw, label in ((1440, "desktop"), (390, "phone")):
                ctx = br.new_context(viewport={"width": vw, "height": 900}, color_scheme=theme,
                                     reduced_motion="no-preference")
                pg = ctx.new_page()
                for name in PAGES:
                    url = f"{site}/{name}" + ("?theme=dark" if theme == "dark" else "")
                    pg.goto(url, wait_until="networkidle")
                    pg.wait_for_timeout(2800)  # scroll reveals finish or fall back
                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    pg.wait_for_timeout(600)
                    pg.evaluate("window.scrollTo(0, 0)")
                    if axe_src:
                        pg.add_script_tag(content=axe_src)
                    else:
                        pg.add_script_tag(url=CDN)
                    res = pg.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, "
                                      "resultTypes: ['violations']})", TAGS)
                    v = [{"id": x["id"], "impact": x["impact"], "help": x["help"], "nodes": len(x["nodes"]),
                          "targets": [n["target"] for n in x["nodes"][:4]]} for x in res["violations"]]
                    row = {"page": name, "theme": theme, "width": label, "axe_violations": v,
                           "axe_passes_checked": len(res.get("passes", [])) or None}
                    if theme == "light":
                        stops, missing = 0, []
                        pg.keyboard.press("Tab")
                        for _ in range(120):
                            info = pg.evaluate("""() => { const el = document.activeElement;
                                if (!el || el === document.body) return null;
                                const s = getComputedStyle(el);
                                const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0)
                                             || (s.boxShadow && s.boxShadow !== 'none');
                                return {key: el.tagName + '|' + (el.id || el.getAttribute('aria-label') ||
                                        (el.textContent || '').trim().slice(0, 30)), ring: ring}; }""")
                            if info is None:
                                break
                            stops += 1
                            if not info["ring"]:
                                missing.append(info["key"])
                            pg.keyboard.press("Tab")
                        row["keyboard"] = {"tab_stops": stops, "without_visible_focus": sorted(set(missing))}
                    if label == "phone":
                        pg.set_viewport_size({"width": 320, "height": 900})
                        pg.wait_for_timeout(300)
                        row["reflow_320px_overflow_px"] = pg.evaluate(
                            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                        pg.set_viewport_size({"width": 390, "height": 900})
                    if label == "desktop" and theme == "light":
                        pg.evaluate("document.documentElement.style.fontSize = '200%'")
                        pg.wait_for_timeout(300)
                        row["text_200pct_overflow_px"] = pg.evaluate(
                            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    rows.append(row)
                    totals["pages"] += 1
                    totals["violations"] += len(v)
                    totals["nodes"] += sum(x["nodes"] for x in v)
                    print(f"{name:16} {theme:5} {label:7} axe:{len(v)}", row.get("keyboard", ""),
                          row.get("reflow_320px_overflow_px", ""), [x["id"] for x in v])
                ctx.close()
        # reduced motion: nothing may still be running after the page settles
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        pg = ctx.new_page()
        motion = {}
        for name in PAGES:
            pg.goto(f"{site}/{name}", wait_until="networkidle")
            pg.wait_for_timeout(1500)
            motion[name] = pg.evaluate("document.getAnimations().filter(a => a.playState === 'running' && "
                                       "(a.effect && a.effect.getComputedTiming().duration > 0.01)).length")
        ctx.close()
        br.close()
    return {"site": site, "standard": "WCAG 2.2 level AA (axe-core 4.10.2 automated rules) plus keyboard, reflow, "
            "200% text and reduced-motion checks", "tags": TAGS, "totals": totals,
            "reduced_motion_running_animations": motion, "rows": rows,
            "measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "limits": "Automated checks find only part of accessibility problems. No screen-reader user or "
                      "disability organisation has tested this site yet."}


if __name__ == "__main__":
    a = sys.argv
    site = a[a.index("--site") + 1] if "--site" in a else json.loads((ROOT / "aws" / "state.json").read_text())["site_url"]
    axe = a[a.index("--axe") + 1] if "--axe" in a else None
    out = run(site, axe)
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["totals"]), "reduced motion:", out["reduced_motion_running_animations"])
