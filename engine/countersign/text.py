"""Content checks for an alert's wording. Code, not a model, decides these."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .cap import Warning
from .lexicon import ACTIONS, HAZARD_TERMS, HAZARD_TERMS_ES, SOURCE_TERMS

TEST_MARKERS = re.compile(r"\b(exercise|drill|this is a test|test message|testing)\b", re.I)
NOT_A_DRILL = re.compile(r"\bnot\s+a\s+drill\b", re.I)
NUMBER = re.compile(r"\d+(?::\d{2})?")
TIME = re.compile(r"\b(\d{1,2})(?::?(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", re.I)
CAPWORD = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
INJECTION = re.compile(
    r"(ignore (all |any |the )?(previous|prior|above) (instructions|rules)|disregard (your|the) (rules|instructions)"
    r"|you are now|system (note|override|prompt)|new instructions|approve (this|the) (alert|draft)"
    r"|send (it )?live|skip (the )?approval|do not (wake|page|notify)|don'?t (wake|page|notify)"
    r"|as an ai|jailbreak|developer mode)", re.I)

COMMON = set("""The This That These Those There Their They Your You And For From With Within Until Till Now
Today Tonight Tomorrow Morning Afternoon Evening Night Monday Tuesday Wednesday Thursday Friday Saturday
Sunday January February March April May June July August September October November December Move Go Get
Stay Leave Avoid Check Follow Pack Turn Do Not Seek If In On At Of To An Is Are Be Take Shelter Higher
Ground Warning Watch Advisory Alert Emergency Flash Flood Flooding Tornado Storm Fire Wind Hurricane
Tsunami Evacuation Evacuate Danger Threat Civil Public Safety Message National Weather Service NWS County
Office Management Sheriff Department Area Areas Official Updates Imminent Immediate Life Threatening Dangerous
Situation Water Roads Road River Rivers Banks Crossings Low Inside Basement Room Floor Building Vehicle
Mobile Home Windows Doors Close Route Routes Officials Medicine Documents Pets Ready Inland High CDT CST EDT
EST MDT MST PDT PST HST AKDT AKST Repeat False Alarm There Missile Ballistic Attack Inbound Immediately
Servicio Nacional Meteorologia SNM Aviso Vaya Evite Salga Siga Quedese Entre Cierre Preparese Lleve Esta
Este Hasta Ahora Condado Inundaciones Repentinas Areas Inundadas Lugar Alto Maneje Camine Agua Cruces Bajos
Orillas Rios Peligrosa Situacion Amenaza Vida Manejo Emergencias Oficina Departamento Bomberos Alguacil TEST Test Drill Exercise""".split())


def _local_times(iso: str) -> set[str]:
    """Format an ISO time the ways an alert might say it, in the source's own offset."""
    out: set[str] = set()
    if not iso:
        return out
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return out
    h12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    out |= {f"{h12}:{dt.minute:02d}{ampm}", f"{h12}{dt.minute:02d}{ampm}"}
    if dt.minute == 0:
        out.add(f"{h12}{ampm}")
    return out


def source_text(w: Warning) -> str:
    parts = []
    for i in w.infos:
        parts += [i.event, i.headline, i.description, i.instruction, i.sender_name]
        parts += [a.desc for a in i.areas]
        for k, vs in i.parameters.items():
            if k.startswith("CMAM") or k in ("NWSheadline", "VTEC"):
                parts += vs
    return "\n".join(p for p in parts if p)


def allowed_numbers(w: Warning, jur_name: str = "") -> set[str]:
    txt = source_text(w) + " " + jur_name
    nums = set(NUMBER.findall(txt))
    for m in TIME.finditer(txt):
        h, mm, ap = m.group(1), m.group(2) or "00", m.group(3).lower().replace(".", "")
        nums |= {f"{int(h)}:{mm}{ap}", f"{int(h)}{mm}{ap}"}
        if mm == "00":
            nums.add(f"{int(h)}{ap}")
    for i in w.infos:
        for t in (i.effective, i.onset, i.expires):
            nums |= _local_times(t)
            if t:
                nums |= set(NUMBER.findall(t[:10]))
    return nums


def numbers_from_nowhere(draft: str, w: Warning, jur_name: str = "") -> list[str]:
    ok = allowed_numbers(w, jur_name)
    bad: list[str] = []
    spans = []
    for m in TIME.finditer(draft):
        h, mm, ap = m.group(1), m.group(2) or "00", m.group(3).lower().replace(".", "")
        forms = {f"{int(h)}:{mm}{ap}", f"{int(h)}{mm}{ap}", f"{int(h)}{ap}" if mm == "00" else ""}
        if not (forms & ok):
            bad.append(m.group(0).strip())
        spans.append(m.span())
    for m in NUMBER.finditer(draft):
        if any(a <= m.start() < b for a, b in spans):
            continue
        if m.group(0) not in ok:
            bad.append(m.group(0))
    return bad


def unknown_names(draft: str, w: Warning, jur_name: str, local_places: list[str]) -> list[str]:
    vocab = source_text(w) + " " + jur_name + " " + " ".join(local_places)
    known = {t.lower() for t in re.findall(r"[A-Za-z]+", vocab)}
    known |= {c.lower() for c in COMMON}
    for a in ACTIONS.values():
        known |= {t.lower() for t in re.findall(r"[A-Za-z]+", a["en"] + " " + a["es"])}
    return sorted({t for t in CAPWORD.findall(draft) if t.lower() not in known})


def five_parts(draft: str, event_code: str, w: Warning, jur_name: str, action_ids: list[str],
               spanish: bool = False) -> dict[str, bool]:
    low = draft.lower()
    terms = (HAZARD_TERMS_ES if spanish else HAZARD_TERMS).get(event_code, []) or [w.en.event.lower()]
    places = [jur_name.lower().replace(" county", "")] + [a.desc.lower() for i in w.infos for a in i.areas]
    action_texts = [ACTIONS[a]["es" if spanish else "en"].lower().rstrip(".") for a in action_ids if a in ACTIONS]
    return {
        "source": any(s in low for s in SOURCE_TERMS),
        "hazard": any(t in low for t in terms if t),
        "location": "this area" in low or "esta area" in low or "su area" in low
                    or any(p and any(part.strip() and part.strip().split(",")[0] in low for part in p.split(";")) for p in places),
        "time": bool(TIME.search(draft)) or any(k in low for k in ("until", "hasta", "now", "ahora", "tonight")),
        "guidance": bool(action_texts) and any(a in low for a in action_texts),
    }


def markers(draft: str, channel: str, w: Warning | None) -> str:
    """Return a problem description, or '' when the drill and live wording is consistent."""
    has_test = bool(TEST_MARKERS.search(NOT_A_DRILL.sub("", draft)))
    has_live = bool(NOT_A_DRILL.search(draft))
    src = source_text(w) if w else ""
    src_test = bool(TEST_MARKERS.search(NOT_A_DRILL.sub("", src)))
    if channel == "live":
        if has_test:
            return "A live alert contains drill or test wording."
        if has_live and src_test:
            return "The source mixes exercise wording with 'not a drill'. This is how the Hawaii script read."
    else:
        if has_live:
            return "A test message says 'not a drill'."
        if not draft.strip().upper().startswith("TEST"):
            return "A test message must start with TEST."
    return ""


def injection_hits(text: str) -> list[str]:
    return sorted({m.group(0) for m in INJECTION.finditer(text or "")})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
