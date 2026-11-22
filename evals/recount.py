"""Recount every number on the Proof page from the saved results. No AWS account needed.

    python evals/recount.py
"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
B = Path(__file__).resolve().parent / "benchmark"


def main():
    s = json.loads((R / "stress-summary.json").read_text())
    print("Stress test (live sends / rule breaks / attack runs):")
    for side in ("countersign", "prompt-only", "prompt-only-naive", "countersign-naive"):
        if f"{side}_attack_runs" in s:
            print(f"  {side:20s} {s[side + '_live_sends']:3d} / {s[side + '_violations']:3d} / {s[side + '_attack_runs']}")
    below = json.loads((R / "below-model.json").read_text())
    print(f"Model removed: {below['held']} of {below['probes']} probes held")
    ar = json.loads((R / "automated-reasoning-probes.json").read_text())
    print(f"Solver probes: {sum(r['ok'] for r in ar)} of {len(ar)} correct")
    tot = {}
    for f in sorted(B.glob("summary-*.json")):
        d = json.loads(f.read_text())
        for lab in ("error", "cancelled", "ordinary"):
            t = tot.setdefault(lab, {"n": 0, "held": 0, "confirm_whole_county": 0, "clear": 0})
            for k in t:
                t[k] += d[lab][k]
    for lab, t in tot.items():
        stop = t["held"] + t["confirm_whole_county"]
        print(f"FEMA archive {lab:9s}: {t['n']:5d} alerts, {stop} stopped for a person ({round(100 * stop / max(1, t['n']))}%), {t['clear']} clear")
    sc = R / "scale.json"
    if sc.exists():
        d = json.loads(sc.read_text())
        r, rb, fo = d.get("race", {}), d.get("race_before_fix", {}), d.get("fanout", {})
        print(f"Race, {r.get('concurrent_approvals')} approvals at once: {rb.get('sent_true')} live sends before the fix, "
              f"{r.get('sent_true')} after")
        for st in d.get("load", {}).get("stages", []):
            print(f"Load {st['target_rps']:3d}/s: {st['requests']} requests, {st['ok_200']} ok, {st['throttled_429']} throttled, "
                  f"{st['server_5xx']} 5xx, p95 {st['p95_ms']} ms")
        print(f"Fan-out: {fo.get('reached_first_person')} of {fo.get('counties')} counties paged, first page p95 "
              f"{fo.get('first_page_p95_s')} s, {fo.get('escalated_to_state_after_no_answer')} escalated")
    ax = R / "a11y.json"
    if ax.exists():
        d = json.loads(ax.read_text())
        print(f"Accessibility: {d['totals']['violations']} axe WCAG 2.2 AA violations in {d['totals']['pages']} page views")
    p = R / "screenreader.json"
    if p.exists():
        d = json.loads(p.read_text())
        k = d["transcript_kept"]
        print(f"Screen reader replay: {d['announcement_count']} announcements for {d['events_replayed']} events, "
              f"transcript {'appended, not rebuilt' if k['same_node'] else 'REBUILT'}, open panel "
              f"{'kept' if k['details_open'] else 'LOST'}")
    p = R / "permissions.json"
    if p.exists():
        d = json.loads(p.read_text())
        print(f"Access Analyzer: {d['checks'] - d['wrong']} of {d['checks']} role and permission pairs as designed; "
              f"{sum(x['result'] == 'PASS' for x in d['public'])} of {len(d['public'])} policies not public; "
              f"lint errors {d['lint'].get('ERROR', 0)}")
    p = R / "chaos.json"
    if p.exists():
        d = json.loads(p.read_text())
        for k, e in d["experiments"].items():
            print(f"Chaos {k:22s} {'held' if e.get('held') else 'DID NOT HOLD'}  alarms {e.get('alarm_seconds')}")
    p = R / "cost.json"
    if p.exists():
        d = json.loads(p.read_text())
        print(f"Cost: ${d['per_warning_usd']} per warning (${d['model']['usd_without_cache']} model cost without caching), "
              f"${d['kerr_night']['usd']} for the {d['kerr_night']['warnings']} Kerr County warnings")


if __name__ == "__main__":
    main()
