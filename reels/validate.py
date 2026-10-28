"""Checks a generated episode before it is rendered.

Catches what an LLM typically gets wrong: unknown country codes, labels that land off-screen
or under the title/captions, scenes that are too long or short, hides of unknown layers.

    python -m reels.validate episodes/x.json
"""
from __future__ import annotations

import copy
import json
import math
import sys
from functools import lru_cache
from pathlib import Path

from pyproj import Transformer

from . import H, W
from .timeline import Timeline

LAYER_TYPES = {"country", "region", "label", "marker", "measure", "arrow", "badge"}
WORDS_PER_SEC = 2.6
SAFE = dict(x0=60, x1=W - 60, y0=430, y1=1660, cap0=1250, cap1=1420)


@lru_cache(maxsize=4)
def known_isos(pov: str | None = "PAK") -> frozenset[str]:
    import geopandas as gpd
    from .data import countries_file
    return frozenset(gpd.read_file(countries_file(pov), ignore_geometry=True)["ADM0_A3"])


def _place_ok(x: float, y: float) -> str | None:
    if not (SAFE["x0"] < x < SAFE["x1"] and SAFE["y0"] < y < SAFE["y1"]):
        return f"lands off-screen or under the title (pixel {x:.0f},{y:.0f})"
    if SAFE["cap0"] < y < SAFE["cap1"]:
        return f"lands under the captions (pixel y={y:.0f}; keep y outside {SAFE['cap0']}-{SAFE['cap1']})"
    return None


def validate(ep: dict, drop_bad: bool = False) -> tuple[list[str], dict]:
    """Return (errors, cleaned episode). With drop_bad, offending labels/markers/arrows are
    removed instead of reported, so a nearly-good episode can still be used."""
    ep = copy.deepcopy(ep)
    errs: list[str] = []
    for k in ("slug", "title", "scenes"):
        if k not in ep:
            errs.append(f"missing top-level key '{k}'")
    if errs:
        return errs, ep
    pov = ep.get("map", {}).get("pov", "PAK")
    isos = known_isos(pov)
    scenes = ep["scenes"]
    if not 5 <= len(scenes) <= 10:
        errs.append(f"need 5-10 scenes, got {len(scenes)}")
    if scenes and scenes[0].get("mode") != "globe":
        errs.append("scene 1 must be mode 'globe'")

    words = 0
    seen: set[str] = set()
    for i, s in enumerate(scenes):
        sid = s.setdefault("id", f"s{i + 1}")
        tag = f"scene {sid}"
        mode = s.get("mode", "map")
        if i > 0 and mode != "map":
            errs.append(f"{tag}: only the first scene may be a globe")
        cam = s.get("camera", {})
        need = ("lon", "lat", "radius") if mode == "globe" else ("lon", "lat", "width_km")
        for k in need:
            if not isinstance(cam.get(k), (int, float)):
                errs.append(f"{tag}: camera.{k} must be a number")
        if mode == "map" and isinstance(cam.get("width_km"), (int, float)) and not 40 <= cam["width_km"] <= 9000:
            errs.append(f"{tag}: camera.width_km {cam['width_km']} outside 40-9000")
        if isinstance(cam.get("lat"), (int, float)) and abs(cam["lat"]) > 80:
            errs.append(f"{tag}: camera.lat must be within ±80")
        vo = s.get("vo", "").strip()
        if not vo:
            errs.append(f"{tag}: empty 'vo'")
        s.setdefault("caption", vo)
        n = len(vo.split())
        words += n
        s["duration"] = round(max(3.5, n / WORDS_PER_SEC + 0.6), 1)  # rough, the voice decides later
        if len(s.get("title") or "") > 48:
            errs.append(f"{tag}: title longer than 48 characters")
        for h in s.get("hide", []):
            hid = h if isinstance(h, str) else h.get("id")
            if hid not in seen:
                errs.append(f"{tag}: hides unknown layer '{hid}'")
        for l in s.get("show", []):
            lid = l.get("id")
            if not lid:
                errs.append(f"{tag}: a layer has no id")
                continue
            if lid in seen and len(l) <= 2:
                continue  # re-show of an existing layer
            typ = l.get("type")
            if typ not in LAYER_TYPES:
                errs.append(f"{tag}: layer {lid} has unknown type '{typ}'")
                continue
            seen.add(lid)
            if typ in ("country", "region", "measure"):
                codes = l.get("iso") or l.get("of") or l.get("between") or ([] if "points" in l else ["?"])
                codes = [codes] if isinstance(codes, str) else codes
                bad = [c for c in codes if c not in isos]
                if bad:
                    errs.append(f"{tag}: layer {lid} uses unknown country codes {bad}")
            if typ == "region":
                c = l.get("clip", [])
                if len(c) != 4 or c[0] >= c[2] or c[1] >= c[3]:
                    errs.append(f"{tag}: region {lid} needs clip [minLon,minLat,maxLon,maxLat]")
            if typ == "badge":
                if len(str(l.get("text", ""))) > 12:
                    errs.append(f"{tag}: badge {lid} text over 12 characters")
                if len(str(l.get("sub", ""))) > 28:
                    errs.append(f"{tag}: badge {lid} sub over 28 characters")
            if typ in ("label", "marker") and len(str(l.get("text", ""))) > 26:
                errs.append(f"{tag}: {typ} {lid} text over 26 characters")

    secs = words / WORDS_PER_SEC
    if not 30 <= secs <= 70:
        errs.append(f"voice is about {secs:.0f}s ({words} words); aim for 100-160 words in total")
    if errs:
        return errs, ep

    errs += _measures(ep, pov)
    if errs:
        return errs, ep
    # on-screen checks need real camera positions
    errs += _placement(ep, drop_bad)
    return errs, ep


def _measures(ep: dict, pov) -> list[str]:
    """A measure between countries that touch inside its window would draw a 0 km line."""
    import geopandas as gpd
    from shapely.geometry import box
    from .data import countries_file
    specs = [l for s in ep["scenes"] for l in s.get("show", []) if l.get("type") == "measure"]
    if not specs:
        return []
    world = _world(pov)
    errs = []
    for l in specs:
        if "points" in l:
            if len(l["points"]) != 2:
                errs.append(f"measure {l['id']}: 'points' needs two [lon, lat] pairs")
            continue
        win = box(*l["within"])
        a, b = [world[world.ADM0_A3 == c].geometry.union_all().intersection(win) for c in l["between"]]
        if a.is_empty or b.is_empty:
            errs.append(f"measure {l['id']}: a country has no land inside 'within'")
            continue
        km = gpd.GeoSeries([a], crs=4326).to_crs(3857).distance(
            gpd.GeoSeries([b], crs=4326).to_crs(3857)).iloc[0] / 1000 * math.cos(math.radians(win.centroid.y))
        if km < 1:
            errs.append(f"measure {l['id']}: the two countries touch inside 'within' (0 km); use a smaller window")
    return errs


@lru_cache(maxsize=2)
def _world(pov):
    import geopandas as gpd
    from .data import countries_file
    return gpd.read_file(countries_file(pov))


def _placement(ep: dict, drop_bad: bool) -> list[str]:
    from .render import auto_extent
    tl = Timeline(ep)
    center, _ = auto_extent(tl)
    fwd = Transformer.from_crs("EPSG:4326",
                               f"+proj=aeqd +lat_0={center[1]} +lon_0={center[0]} +units=m +datum=WGS84",
                               always_xy=True)

    def px(cam, lon, lat):
        x, y = fwd.transform(lon, lat)
        cx, cy = fwd.transform(cam["lon"], cam["lat"])
        w = cam["width_km"] * 1000
        h = w * H / W
        return (x - cx + w / 2) / w * W, (cy + h / 2 - y) / h * H

    errs = []
    for sc, raw in zip(tl.scenes, ep["scenes"]):
        if sc.mode != "map":
            continue
        keep = []
        for l in raw.get("show", []):
            typ = l.get("type")
            problem = None
            if typ in ("label", "marker"):
                cam = tl._cam_local(sc, sc.duration * 0.75)
                p = px(cam, l["lon"], l["lat"])
                if typ == "marker" and l.get("text"):
                    side = l.get("side", "right")
                    # the marker's text box decides whether it fits
                    dx = {"right": 140, "left": -140}.get(side, 0)
                    dy = {"top": -50, "bottom": 50}.get(side, 0)
                    p = (p[0] + dx, p[1] + dy)
                problem = _place_ok(*p)
            elif typ == "arrow":
                cam = tl._cam_local(sc, sc.duration * 0.75)
                for end in ("from", "to"):
                    pp = px(cam, *l[end])
                    if not (0 < pp[0] < W and 300 < pp[1] < 1700):
                        problem = f"{end} end off-screen (pixel {pp[0]:.0f},{pp[1]:.0f})"
            if problem and drop_bad and "captions" in problem and typ == "label":
                # nudge the label just above the caption band instead of dropping it
                cam = tl._cam_local(sc, sc.duration * 0.75)
                h_km = cam["width_km"] * H / W
                y = px(cam, l["lon"], l["lat"])[1]
                l["lat"] = round(l["lat"] + (y - (SAFE["cap0"] - 40)) / H * h_km / 111.0, 4)
                problem = None
            if problem:
                if drop_bad:
                    continue
                errs.append(f"scene {sc.sid}: {typ} '{l['id']}' {problem}. Move it or change the camera.")
            keep.append(l)
        raw["show"] = keep
    if drop_bad:
        # hides pointing at removed layers are harmless, but tidy them
        alive = {l["id"] for s in ep["scenes"] for l in s.get("show", [])}
        for s in ep["scenes"]:
            s["hide"] = [h for h in s.get("hide", []) if (h if isinstance(h, str) else h.get("id")) in alive]
    return errs


def main():
    ep = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    errs, _ = validate(ep)
    print("\n".join(errs) if errs else "OK")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
