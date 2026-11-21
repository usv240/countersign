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


if __name__ == "__main__":
    main()
