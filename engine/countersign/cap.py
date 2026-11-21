"""Read warnings into one shape, whatever they arrive as.

Three inputs are supported:
  * CAP 1.2 XML (what IPAWS and the FEMA archive store)
  * a National Weather Service API feature (GeoJSON, from api.weather.gov)
  * an OpenFEMA archive record (JSON that wraps the CAP XML)

Everything text-like is kept as data. Nothing in a warning is ever executed.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
N = {"c": CAP_NS}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class Area:
    desc: str = ""
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)  # (lon, lat)
    same: list[str] = field(default_factory=list)
    ugc: list[str] = field(default_factory=list)


@dataclass
class Info:
    language: str = "en-US"
    event: str = ""
    event_codes: dict[str, str] = field(default_factory=dict)
    urgency: str = ""
    severity: str = ""
    certainty: str = ""
    response_type: str = ""
    headline: str = ""
    description: str = ""
    instruction: str = ""
    sender_name: str = ""
    effective: str = ""
    onset: str = ""
    expires: str = ""
    parameters: dict[str, list[str]] = field(default_factory=dict)
    areas: list[Area] = field(default_factory=list)


@dataclass
class Warning:
    identifier: str
    sender: str
    sent: str
    status: str
    msg_type: str
    scope: str = "Public"
    source_kind: str = "unknown"      # nws-api | ipaws-archive | cap-xml | user
    official: bool = False             # True only when code fetched it from an official source
    cog_id: int | None = None
    references: list[str] = field(default_factory=list)
    infos: list[Info] = field(default_factory=list)

    @property
    def en(self) -> Info:
        for i in self.infos:
            if i.language.lower().startswith("en"):
                return i
        return self.infos[0] if self.infos else Info()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _t(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path, N)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_polygon(text: str) -> list[tuple[float, float]]:
    """CAP polygons are 'lat,lon lat,lon ...'. Returns (lon, lat) pairs."""
    pts: list[tuple[float, float]] = []
    for pair in text.split():
        try:
            lat, lon = pair.split(",")[:2]
            pts.append((float(lon), float(lat)))
        except ValueError:
            continue
    return pts


def from_cap_xml(xml: str, *, source_kind: str = "cap-xml", official: bool = False) -> Warning:
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    if root.tag != f"{{{CAP_NS}}}alert":
        found = root.find(".//c:alert", N)
        if found is None:
            raise ValueError("Not a CAP 1.2 alert")
        root = found
    w = Warning(
        identifier=_t(root, "c:identifier"),
        sender=EMAIL.sub("[redacted sender]", _t(root, "c:sender")),
        sent=_t(root, "c:sent"),
        status=_t(root, "c:status"),
        msg_type=_t(root, "c:msgType"),
        scope=_t(root, "c:scope") or "Public",
        source_kind=source_kind,
        official=official,
        references=_t(root, "c:references").split(),
    )
    for info in root.findall("c:info", N):
        i = Info(
            language=_t(info, "c:language") or "en-US",
            event=_t(info, "c:event"),
            urgency=_t(info, "c:urgency"),
            severity=_t(info, "c:severity"),
            certainty=_t(info, "c:certainty"),
            response_type=_t(info, "c:responseType"),
            headline=_t(info, "c:headline"),
            description=_t(info, "c:description"),
            instruction=_t(info, "c:instruction"),
            sender_name=_t(info, "c:senderName"),
            effective=_t(info, "c:effective"),
            onset=_t(info, "c:onset"),
            expires=_t(info, "c:expires"),
        )
        for ec in info.findall("c:eventCode", N):
            i.event_codes[_t(ec, "c:valueName")] = _t(ec, "c:value")
        for p in info.findall("c:parameter", N):
            i.parameters.setdefault(_t(p, "c:valueName"), []).append(_t(p, "c:value"))
        for a in info.findall("c:area", N):
            area = Area(desc=_t(a, "c:areaDesc"))
            for poly in a.findall("c:polygon", N):
                pts = parse_polygon(poly.text or "")
                if len(pts) >= 4:
                    area.polygons.append(pts)
            for g in a.findall("c:geocode", N):
                name, val = _t(g, "c:valueName"), _t(g, "c:value")
                if name == "SAME":
                    area.same.append(val)
                elif name == "UGC":
                    area.ugc.append(val)
            i.areas.append(area)
        w.infos.append(i)
    return w


def from_openfema(rec: dict[str, Any]) -> Warning:
    """An OpenFEMA archive record. The archive copy is public but not byte-exact,
    so its XML signature does not verify; it is pinned by its archive id instead."""
    w = from_cap_xml(rec["originalMessage"], source_kind="ipaws-archive",
                     official=str(rec.get("cogId")) == "200032")
    w.cog_id = rec.get("cogId")
    w.sent = rec.get("sent") or w.sent
    return w


def from_nws_feature(feat: dict[str, Any], *, official: bool = True) -> Warning:
    """A feature from https://api.weather.gov/alerts (GeoJSON)."""
    p = feat.get("properties", {})
    geom = feat.get("geometry") or {}
    area = Area(desc=p.get("areaDesc", ""),
                same=list((p.get("geocode") or {}).get("SAME", [])),
                ugc=list((p.get("geocode") or {}).get("UGC", [])))
    if geom.get("type") == "Polygon":
        area.polygons.append([tuple(c) for c in geom["coordinates"][0]])
    elif geom.get("type") == "MultiPolygon":
        for poly in geom["coordinates"]:
            area.polygons.append([tuple(c) for c in poly[0]])
    params = {k: (v if isinstance(v, list) else [v]) for k, v in (p.get("parameters") or {}).items()}
    info = Info(
        language="en-US", event=p.get("event", ""),
        event_codes={"SAME": ((p.get("eventCode") or {}).get("SAME") or [""])[0],
                     "NationalWeatherService": ((p.get("eventCode") or {}).get("NationalWeatherService") or [""])[0]},
        urgency=p.get("urgency", ""), severity=p.get("severity", ""), certainty=p.get("certainty", ""),
        response_type=p.get("response", ""), headline=p.get("headline") or "",
        description=p.get("description") or "", instruction=p.get("instruction") or "",
        sender_name=p.get("senderName", ""), effective=p.get("effective") or "",
        onset=p.get("onset") or "", expires=p.get("expires") or "", parameters=params, areas=[area],
    )
    return Warning(
        identifier=p.get("id") or feat.get("id", ""), sender=p.get("sender", ""),
        sent=p.get("sent", ""), status=p.get("status", ""), msg_type=p.get("messageType", ""),
        scope=p.get("scope", "Public"), source_kind="nws-api", official=official,
        references=[r.get("identifier", "") for r in (p.get("references") or [])], infos=[info],
    )


def load_any(payload: str | dict[str, Any]) -> Warning:
    """Best effort for user-supplied data. User data is never marked official."""
    if isinstance(payload, str):
        s = payload.strip()
        if s.startswith("<"):
            return from_cap_xml(s, source_kind="user", official=False)
        payload = json.loads(s)
    if "originalMessage" in payload:
        w = from_openfema(payload)
    elif payload.get("type") == "Feature" or "properties" in payload:
        w = from_nws_feature(payload, official=False)
    elif payload.get("type") == "FeatureCollection" and payload.get("features"):
        w = from_nws_feature(payload["features"][0], official=False)
    else:
        raise ValueError("Unrecognised warning format. Send CAP XML, an NWS GeoJSON feature, or an OpenFEMA record.")
    w.source_kind, w.official = "user", False
    return w
