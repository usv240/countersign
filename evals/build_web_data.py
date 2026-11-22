"""Write the website's offline data from the engine: the three replayed nights and the presets.

    python evals/build_web_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from countersign.cap import from_openfema  # noqa: E402
from countersign.gate import evaluate, proposal_from_cap  # noqa: E402
from countersign.geo import load_jurisdiction  # noqa: E402

OUT = ROOT / "web" / "data"
PRESETS = ROOT / "aws" / "lambdas" / "presets"


def simplify(geom, tol=0.004):
    g = geom.simplify(tol, preserve_topology=True)
    return json.loads(json.dumps(g.__geo_interface__))


def record_view(rec: dict) -> dict:
    w = from_openfema(rec)
    i = w.en
    return {"identifier": w.identifier, "sent": w.sent, "msg_type": w.msg_type, "official": w.official,
            "sender_name": i.sender_name, "event": i.event, "headline": i.headline,
            "urgency": i.urgency, "severity": i.severity, "certainty": i.certainty,
            "area_desc": "; ".join(a.desc for a in i.areas), "same": [s for a in i.areas for s in a.same],
            "has_polygon": any(a.polygons for a in i.areas),
            "text_90": (i.parameters.get("CMAMtext") or [""])[0],
            "text_360": (i.parameters.get("CMAMlongtext") or [""])[0],
            "description": i.description[:700], "polygons": [p for a in i.areas for p in a.polygons]}


def night(pid: str, extra: dict) -> dict:
    p = json.loads((PRESETS / f"{pid}.json").read_text(encoding="utf-8"))
    w = from_openfema(p["record"])
    jur = load_jurisdiction(p["county_geoid"])
    as_sent = evaluate(proposal_from_cap(w), w, jur).to_dict()
    out = {"id": pid, "title": p["title"], "summary": p["summary"], "county": p["county_name"],
           "geoid": jur.geoid, "population": jur.population, "record": record_view(p["record"]),
           "as_sent": as_sent, "jurisdiction": simplify(jur.geom), **extra}
    if p.get("proposal"):
        out["countersign_draft"] = {"proposal": p["proposal"],
                                    "decision": evaluate(p["proposal"], w, jur).to_dict()}
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nights = [
        night("hawaii-2018", {"short": "Hawaii, 2018", "what_happened":
              "A drill script that said both \"exercise\" and \"this is not a drill\" was read as real. One officer "
              "sent the missile alert alone. The correction took 38 minutes because no template existed.",
              "reached": "Everyone in Hawaii, about 1,455,271 residents plus visitors", "minutes_to_fix": 38}),
        night("los-angeles-2025", {"short": "Los Angeles, 2025", "what_happened":
              "An evacuation warning for the Kenneth Fire area was sent with the county code but no map area on the "
              "phone channel, so it reached the whole county.",
              "reached": "About 10,014,009 people in Los Angeles County", "minutes_to_fix": 22}),
        night("kerr-2025-0114", {"short": "Kerr County, 2025", "what_happened":
              "The National Weather Service issued flash flood warnings for Kerr County through the night of July 4, "
              "2025, starting at 1:14 AM. The county sent no phone alert of its own that day.",
              "reached": "No county alert. Kerr County has 52,598 residents", "minutes_to_fix": None}),
    ]
    (OUT / "replays.json").write_text(json.dumps({"nights": nights}, indent=1), encoding="utf-8")
    import yaml
    rules = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    (OUT / "rules.json").write_text(json.dumps(rules, indent=1), encoding="utf-8")
    presets = []
    for f in sorted(PRESETS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        presets.append({k: d.get(k) for k in ("id", "title", "kind", "summary", "county_geoid", "county_name",
                                               "failure", "what_to_expect", "modified", "modification")})
    (OUT / "presets.json").write_text(json.dumps(presets, indent=1), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "evals"))
    from attacks import ATTACKS
    (OUT / "attacks.json").write_text(json.dumps(ATTACKS, indent=1), encoding="utf-8")
    from countersign.lexicon import ACTIONS
    (OUT / "lexicon.json").write_text(json.dumps(ACTIONS, indent=1), encoding="utf-8")
    import shutil
    for f in ("below-model.json", "automated-reasoning-probes.json", "stress-summary.json", "scale.json", "a11y.json"):
        src = ROOT / "evals" / "results" / f
        if src.exists():
            shutil.copy(src, OUT / f)
    bench = {}
    for y in (2023, 2024, 2025):
        f = ROOT / "evals" / "benchmark" / f"summary-{y}.json"
        if f.exists():
            bench[str(y)] = json.loads(f.read_text(encoding="utf-8"))
    (OUT / "benchmark.json").write_text(json.dumps(bench, indent=1), encoding="utf-8")
    print("wrote replays.json", [n["id"] for n in nights])


if __name__ == "__main__":
    main()
