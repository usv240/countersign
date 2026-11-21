"""Places and polygons: county boundaries, area checks and reach estimates.

Boundaries and 2020 population come from the US Census Bureau's TIGERweb
service (public domain). They are cached on disk; any US county or state can be
fetched by its GEOID (2-digit state, or 5-digit state+county FIPS).
"""
from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity

TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer"
COUNTY_LAYER, STATE_LAYER = 59, 58  # Census 2020, about 1:1,400,000
CACHE = Path(os.environ.get("CS_GEO_CACHE", Path(__file__).resolve().parents[2] / "data" / "geo"))
BUNDLED = Path(__file__).resolve().parent / "geo"  # the counties the demo needs, shipped with the code

# The federal geotargeting rule allows 0.1 mile of overshoot (47 CFR 10.450).
TOLERANCE_MILES = 0.1
MILE_DEG_LAT = 1 / 69.0


@dataclass
class Jurisdiction:
    geoid: str
    name: str
    population: int
    geom: Polygon | MultiPolygon
    extra_codes: tuple[str, ...] = ()

    @property
    def codes(self) -> set[str]:
        return {self.same, *self.extra_codes}

    @property
    def same(self) -> str:
        """SAME code: 0 + state + county, or 0 + state + 000 for a state."""
        return f"0{self.geoid}" if len(self.geoid) == 5 else f"0{self.geoid}000"

    def to_geojson(self) -> dict:
        return {"type": "Feature", "properties": {"geoid": self.geoid, "name": self.name,
                "population": self.population}, "geometry": json.loads(json.dumps(self.geom.__geo_interface__))}


def _fetch(geoid: str) -> dict:
    layer = COUNTY_LAYER if len(geoid) == 5 else STATE_LAYER
    q = urllib.parse.urlencode({"where": f"GEOID='{geoid}'", "outFields": "GEOID,NAME,POP100",
                                "outSR": 4326, "f": "geojson"})
    req = urllib.request.Request(f"{TIGER}/{layer}/query?{q}", headers={"User-Agent": "countersign/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        fc = json.loads(r.read())
    if not fc.get("features"):
        raise ValueError(f"No US county or state with GEOID {geoid}")
    return fc["features"][0]


def load_jurisdiction(geoid: str) -> Jurisdiction:
    geoid = geoid.strip()
    if not geoid.isdigit() or len(geoid) not in (2, 5):
        raise ValueError("GEOID must be 2 digits (state) or 5 digits (county), for example 48265")
    path = CACHE / f"{geoid}.geojson"
    if not path.exists() and (BUNDLED / f"{geoid}.geojson").exists():
        path = BUNDLED / f"{geoid}.geojson"
    try:
        feat = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        feat = _fetch(geoid)
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(feat), encoding="utf-8")
        except OSError:
            pass
    props = feat["properties"]
    return Jurisdiction(geoid=geoid, name=props.get("NAME", geoid),
                        population=int(props.get("POP100") or 0), geom=shape(feat["geometry"]).buffer(0))


def union_jurisdiction(geoids: list[str]) -> Jurisdiction:
    """Several counties treated as one office's area, for agencies that cover more than one."""
    parts = [load_jurisdiction(g) for g in dict.fromkeys(geoids)]
    if len(parts) == 1:
        return parts[0]
    return Jurisdiction(geoid=parts[0].geoid, name=f"{parts[0].name} and {len(parts) - 1} more",
                        population=sum(p.population for p in parts),
                        geom=unary_union([p.geom for p in parts]).buffer(0),
                        extra_codes=tuple(p.same for p in parts[1:]))


def polygon_from_points(points: list[tuple[float, float]]) -> Polygon:
    return Polygon(points)


def area_km2(geom) -> float:
    """Equal-area approximation, good to about 1% at county scale."""
    if geom.is_empty:
        return 0.0
    lat = geom.centroid.y
    kx = 111.320 * math.cos(math.radians(lat))
    return geom.area * kx * 110.574


def check_polygon(poly_pts: list[tuple[float, float]] | None, jur: Jurisdiction,
                  source_polys: list[list[tuple[float, float]]] | None = None) -> dict:
    """Return the facts the area rule needs. No judgement here, only measurements."""
    out: dict = {"has_polygon": bool(poly_pts), "valid": False, "inside_jurisdiction": False,
                 "inside_source": None, "outside_km2": 0.0, "area_km2": 0.0,
                 "share_of_jurisdiction": 0.0, "people_reached_est": 0, "problem": ""}
    if not poly_pts:
        out["problem"] = "No polygon. A county or state code alone does not define an area."
        return out
    if len(poly_pts) < 4:
        out["problem"] = "A polygon needs at least 4 points, with the first repeated as the last."
        return out
    poly = Polygon(poly_pts)
    if not poly.is_valid:
        out["problem"] = f"Invalid polygon: {explain_validity(poly)}"
        return out
    out["valid"] = True
    tol = TOLERANCE_MILES * MILE_DEG_LAT
    jur_tol = jur.geom.buffer(tol)
    out["inside_jurisdiction"] = jur_tol.contains(poly)
    out["outside_km2"] = round(area_km2(poly.difference(jur_tol)), 2)
    if source_polys:
        src = unary_union([Polygon(p).buffer(0) for p in source_polys if len(p) >= 4])
        out["inside_source"] = src.buffer(tol).contains(poly)
    covered = poly.intersection(jur.geom)
    out["area_km2"] = round(area_km2(poly), 1)
    jur_area = area_km2(jur.geom) or 1.0
    out["share_of_jurisdiction"] = round(area_km2(covered) / jur_area, 4)
    # Uniform density inside the jurisdiction. Disclosed as an estimate.
    out["people_reached_est"] = int(round(jur.population * out["share_of_jurisdiction"]))
    return out


def clip_to(points: list[tuple[float, float]], jur: Jurisdiction) -> list[tuple[float, float]] | None:
    """Intersect a source polygon with the jurisdiction. Used by the drafting tool."""
    inter = Polygon(points).buffer(0).intersection(jur.geom)
    if inter.is_empty:
        return None
    if isinstance(inter, MultiPolygon):
        inter = max(inter.geoms, key=lambda g: g.area)
    if not isinstance(inter, Polygon):
        return None
    simp = inter.simplify(0.001, preserve_topology=True)
    return [(round(x, 5), round(y, 5)) for x, y in simp.exterior.coords]
