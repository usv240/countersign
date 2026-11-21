"""The gate: every proposed alert is checked against all ten rules here.

The same function runs in the agent's tool backend, in the pre-tool-call hook,
in the approval service before a live send, in the public API, and offline in
the evaluation. One implementation, so the layers cannot disagree about a rule.
A second, independent check of the approval decision runs in Amazon Bedrock
Automated Reasoning checks (see aws/automated_reasoning.py).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import text as T
from .cap import Warning
from .geo import Jurisdiction, check_polygon
from .lexicon import ACTIONS

IMMINENT_URGENCY = {"Immediate", "Expected"}
IMMINENT_SEVERITY = {"Extreme", "Severe"}
IMMINENT_CERTAINTY = {"Observed", "Likely"}
CLASSES = {"Imminent Threat", "Public Safety"}
LONG, SHORT = 360, 90

RULE_NAMES = {
    "R1": "Official source only", "R2": "The agent never sends live", "R3": "A real map area, inside the county",
    "R4": "Say only what the source says", "R5": "The right alert class", "R6": "A correction is always ready",
    "R7": "Never go quiet", "R8": "Text is data, not orders", "R9": "No opinion on the approval screen",
    "R10": "One alert per warning",
}


@dataclass
class Check:
    rule: str
    status: str          # pass | fail | warn | info
    message: str
    hard: bool = False   # a hard failure blocks a live send whatever the approvals
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return RULE_NAMES.get(self.rule, self.rule)


@dataclass
class Decision:
    outcome: str                 # test_ok | test_blocked | needs_1_approval | needs_2_approvals | blocked | send_ok
    channel: str
    approvals_required: int
    approvals_present: int
    checks: list[Check]
    facts: dict[str, Any]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for c, cd in zip(self.checks, d["checks"]):
            cd["name"] = c.name
        return d


def qualifies_imminent(w: Warning) -> bool:
    i = w.en
    return i.urgency in IMMINENT_URGENCY and i.severity in IMMINENT_SEVERITY and i.certainty in IMMINENT_CERTAINTY


def event_code(w: Warning | None) -> str:
    if not w:
        return ""
    ec = w.en.event_codes
    return ec.get("SAME") or ec.get("NationalWeatherService") or ""


def evaluate(proposal: dict[str, Any], warning: Warning | None, jur: Jurisdiction, *,
             sent_registry: set[str] | None = None, local_places: list[str] | None = None) -> Decision:
    """Check one proposed alert. `warning` must be what code fetched for proposal['warning_id'],
    never what the caller or the model says the warning contains."""
    p = proposal
    channel = p.get("channel", "test")
    checks: list[Check] = []
    facts: dict[str, Any] = {"jurisdiction": jur.name, "jurisdiction_geoid": jur.geoid,
                             "population": jur.population}
    text360 = (p.get("text_360") or "").strip()
    text90 = (p.get("text_90") or "").strip()
    es360 = (p.get("text_360_es") or "").strip()
    es90 = (p.get("text_90_es") or "").strip()
    action_ids = [a for a in p.get("action_ids") or [] if isinstance(a, str)]
    approvals = p.get("approvals") or []
    officers = {a.get("officer") for a in approvals if a.get("officer") and a.get("decision", "approve") == "approve"}
    places = local_places or []
    ec = event_code(warning)
    facts["event_code"] = ec

    # R1: official source
    sourced = bool(warning and warning.official and p.get("warning_id") and warning.identifier == p.get("warning_id"))
    if sourced and warning.status != "Actual":
        sourced = False
    if sourced and not any(jur.codes & set(a.same) or jur.same[:3] + "000" in a.same for i in warning.infos for a in i.areas) \
            and not any(a.polygons for i in warning.infos for a in i.areas):
        checks.append(Check("R1", "fail", "The official warning does not cover this county.", hard=True))
        sourced = False
    elif sourced:
        checks.append(Check("R1", "pass", f"Matches official warning {warning.identifier[-24:]} fetched by code.",
                            evidence={"warning_id": warning.identifier, "event": warning.en.event,
                                      "sender": warning.en.sender_name, "source_kind": warning.source_kind}))
    else:
        why = "No official warning id." if not p.get("warning_id") else \
              "The warning was supplied by a person or a message, not fetched from an official source."
        checks.append(Check("R1", "warn", f"Unsourced. {why} Two officers must approve.",
                            evidence={"source_kind": warning.source_kind if warning else "none"}))
    facts["sourced"] = sourced

    # R2: who may send
    requested_by = p.get("requested_by", "agent")
    if channel == "live" and requested_by == "agent":
        checks.append(Check("R2", "fail", "The agent asked to send live. Only the approval service can.", hard=True))
    elif channel == "live":
        need = 1 if sourced else 2
        if len(officers) >= need:
            checks.append(Check("R2", "pass", f"{len(officers)} of {need} required approvals from distinct officers.",
                                evidence={"officers": sorted(officers)}))
        else:
            checks.append(Check("R2", "warn", f"Needs {need} approval{'s' if need > 1 else ''} from distinct officers; "
                                              f"has {len(officers)}.", evidence={"officers": sorted(officers)}))
    else:
        checks.append(Check("R2", "pass", "Test channel. No phone receives a test."))

    # R3: area
    src_polys = [pt for i in warning.infos for a in i.areas for pt in a.polygons] if warning else []
    poly = p.get("polygon")
    if poly and isinstance(poly[0], (list, tuple)):
        poly = [(float(x), float(y)) for x, y in poly]
    area = check_polygon(poly, jur, src_polys or None)
    facts.update({k: area[k] for k in ("area_km2", "share_of_jurisdiction", "people_reached_est", "outside_km2")})
    geocodes = [g for g in (p.get("geocodes") or []) if g]
    whole_ok = (not area["has_polygon"] and geocodes and set(geocodes) <= jur.codes
                and p.get("whole_area_confirmed_by"))
    if whole_ok:
        facts.update({"people_reached_est": jur.population, "share_of_jurisdiction": 1.0})
        checks.append(Check("R3", "warn", f"Whole of {jur.name} by county code, confirmed by officer "
                                          f"{p['whole_area_confirmed_by']}. Reaches about {jur.population:,} people.",
                            evidence={"geocodes": geocodes, "confirmed_by": p["whole_area_confirmed_by"]}))
    elif not area["has_polygon"] and geocodes:
        facts["people_if_code_used"] = jur.population if set(geocodes) <= jur.codes else None
        wider = not set(geocodes) <= jur.codes
        checks.append(Check("R3", "fail", ("The code covers more than this office's area. " if wider else "")
                            + f"No drawn area. A code alone sends to all of it, about {jur.population:,} people in "
                              f"{jur.name}. An officer must draw the area or confirm the whole county on purpose.",
                            hard=True, evidence={"geocodes": geocodes, "needs": "whole_area_confirmed_by"}))
    elif not area["has_polygon"] or not area["valid"]:
        checks.append(Check("R3", "fail", area["problem"], hard=True, evidence=area))
    elif not area["inside_jurisdiction"]:
        checks.append(Check("R3", "fail", f"{area['outside_km2']} km2 of the area is outside {jur.name}.",
                            hard=True, evidence=area))
    elif area["inside_source"] is False:
        checks.append(Check("R3", "fail", "The area goes beyond the warning's own polygon.", hard=True, evidence=area))
    else:
        note = "inside the warning polygon" if area["inside_source"] else "warning has no polygon; county boundary used"
        checks.append(Check("R3", "pass", f"{area['area_km2']} km2, {round(area['share_of_jurisdiction'] * 100, 1)}% "
                                          f"of {jur.name}, {note}. About {area['people_reached_est']:,} people.",
                            evidence=area))

    # R4: content
    problems: list[str] = []
    hard4 = False
    if not text360:
        problems.append("No alert text.")
        hard4 = True
    if len(text360) > LONG:
        problems.append(f"Long text is {len(text360)} characters; the limit is {LONG}.")
        hard4 = True
    if channel == "live" and not text90:
        problems.append("No 90-character version for older phones.")
        hard4 = True
    if len(text90) > SHORT:
        problems.append(f"Short text is {len(text90)} characters; the limit is {SHORT}.")
        hard4 = True
    mk = T.markers(text360 + " " + text90, channel, warning)
    if mk:
        problems.append(mk)
        hard4 = True
    if channel == "live" and es360 and T.markers(es360, channel, warning):
        problems.append("Spanish text contains drill or test wording.")
        hard4 = True
    bad_actions = [a for a in action_ids if a not in ACTIONS or (ec and ec not in ACTIONS[a]["events"]
                                                                 and "*" not in ACTIONS[a]["events"])]
    if bad_actions:
        problems.append(f"Protective action not in the approved library for this hazard: {', '.join(bad_actions)}.")
        hard4 = True
    if warning:
        nowhere = T.numbers_from_nowhere(text360 + " " + text90 + " " + es360 + " " + es90, warning, jur.name)
        if nowhere:
            problems.append(f"Numbers or times not in the source: {', '.join(sorted(set(nowhere)))}.")
            hard4 = True
        # Place and name check on the English text. The Spanish text gets the numbers check above; ordinary
        # Spanish words are not names, and flagging them was a false alarm found in the first live run.
        names = T.unknown_names(text360, warning, jur.name, places)
        if names:
            problems.append(f"Names not in the source or the county's place list: {', '.join(names)}.")
            hard4 = True
    parts = T.five_parts(text360, ec, warning, jur.name, action_ids) if (warning and text360) else {}
    missing = [k for k, v in parts.items() if not v]
    facts["five_parts"] = parts
    if problems:
        checks.append(Check("R4", "fail", " ".join(problems), hard=hard4, evidence={"five_parts": parts}))
    elif missing:
        checks.append(Check("R4", "warn", f"Missing part{'s' if len(missing) > 1 else ''}: {', '.join(missing)}. "
                                          "Complete warnings name source, hazard, location, time and action.",
                            evidence={"five_parts": parts}))
    else:
        checks.append(Check("R4", "pass", "Every fact is in the source; all five parts present; within length.",
                            evidence={"five_parts": parts, "length": len(text360)}))

    # R5: class
    klass = p.get("alert_class", "Public Safety")
    facts["alert_class"] = klass
    if klass not in CLASSES:
        checks.append(Check("R5", "fail", f"'{klass}' is not a class this office may send.", hard=True))
    elif klass == "Imminent Threat":
        if warning and qualifies_imminent(warning):
            i = warning.en
            checks.append(Check("R5", "pass", f"Qualifies: urgency {i.urgency}, severity {i.severity}, "
                                              f"certainty {i.certainty}.",
                                evidence={"urgency": i.urgency, "severity": i.severity, "certainty": i.certainty}))
        elif warning:
            i = warning.en
            checks.append(Check("R5", "fail", f"Does not qualify as Imminent Threat (urgency {i.urgency or 'none'}, "
                                              f"severity {i.severity or 'none'}, certainty {i.certainty or 'none'}). "
                                              "Send as Public Safety.", hard=True,
                                evidence={"urgency": i.urgency, "severity": i.severity, "certainty": i.certainty}))
        else:
            checks.append(Check("R5", "warn", "No source to classify against. Two officers must confirm the class."))
    else:
        checks.append(Check("R5", "pass", "Public Safety message."))

    # R6: correction staged
    if channel == "live" and not p.get("correction_staged"):
        checks.append(Check("R6", "fail", "No correction staged. Stage it before sending.", hard=True))
    elif channel == "live":
        checks.append(Check("R6", "pass", "Correction staged and ready: one approval sends it."))
    else:
        checks.append(Check("R6", "info", "Not needed for a test."))

    # R7: paging is outside this function; report the state it was given
    paging = p.get("paging_state")
    if paging:
        checks.append(Check("R7", "info", f"Paging: {paging}."))
    else:
        checks.append(Check("R7", "info", "Paging is run by the escalation workflow, not by this check."))

    # R8: instruction-like text in what was read
    hits = T.injection_hits((T.source_text(warning) if warning else "") + "\n" + (p.get("context_text") or ""))
    if hits:
        checks.append(Check("R8", "warn", "Instruction-like text found in the material read. It was treated as data "
                                          "and changed nothing: " + "; ".join(f'"{h}"' for h in hits[:4]),
                            evidence={"hits": hits}))
    else:
        checks.append(Check("R8", "pass", "No instructions found in the source material."))

    # R9: no opinion in what the officer sees
    if p.get("recommendation"):
        checks.append(Check("R9", "fail", "A recommendation was attached. It is removed before the officer sees "
                                          "the draft.", hard=False))
    else:
        checks.append(Check("R9", "pass", "No recommendation shown to the officer."))

    # R10: one live alert per warning
    key = f"{p.get('warning_id') or 'unsourced'}|{jur.geoid}"
    if channel == "live" and sent_registry is not None and key in sent_registry and p.get("warning_id"):
        checks.append(Check("R10", "fail", "This warning was already sent live for this county.", hard=True))
    else:
        checks.append(Check("R10", "pass" if channel == "live" else "info",
                            "Not sent before." if channel == "live" else "Not needed for a test."))

    hard_fail = any(c.status == "fail" and c.hard for c in checks)
    need = 0 if channel == "test" else (1 if sourced else 2)
    have = len(officers)
    if channel == "test":
        outcome = "test_blocked" if any(c.status == "fail" and c.hard and c.rule in ("R4",) for c in checks) else "test_ok"
        summary = "Test message can go to the test channel." if outcome == "test_ok" else \
                  "Test message blocked: fix the wording first."
    elif hard_fail:
        outcome = "blocked"
        first = next(c for c in checks if c.status == "fail" and c.hard)
        summary = f"Blocked by {first.rule} ({first.name}). No number of approvals can send it."
    elif have >= need:
        outcome = "send_ok"
        summary = f"All rules pass and {have} of {need} approvals are in. The approval service may send it."
    else:
        outcome = "needs_1_approval" if need == 1 else "needs_2_approvals"
        summary = f"Ready for review. Needs {need} approval{'s' if need > 1 else ''} from distinct officers."
    return Decision(outcome=outcome, channel=channel, approvals_required=need, approvals_present=have,
                    checks=checks, facts=facts, summary=summary)


def proposal_from_cap(w: Warning) -> dict[str, Any]:
    """Treat a historical alert as if someone proposed to send it live. Used by the replay."""
    i = w.en
    long_text = (i.parameters.get("CMAMlongtext") or [""])[0] or (i.parameters.get("CMAMtext") or [""])[0] \
        or i.description[:360]
    short = (i.parameters.get("CMAMtext") or [""])[0]
    polys = [pt for a in i.areas for pt in a.polygons]
    handling = (i.parameters.get("WEAHandling") or [""])[0]
    return {
        "warning_id": w.identifier if w.official else None, "channel": "live", "requested_by": "officer",
        "alert_class": "Imminent Threat" if handling == "Imminent Threat" or not handling else "Public Safety",
        "polygon": polys[0] if polys else None, "geocodes": [s for a in i.areas for s in a.same],
        "text_360": long_text, "text_90": short, "action_ids": [], "correction_staged": False,
    }
