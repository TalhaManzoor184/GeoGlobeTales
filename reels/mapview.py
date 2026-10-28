"""Flat zoomable map (azimuthal equidistant around the episode centre) drawn with matplotlib.

All artists are created once; each frame only moves the camera and changes alphas, so a
frame costs one canvas.draw().
"""
from __future__ import annotations

import math

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MPath
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points

from . import H, W
from .style import color as C

LODS = [150.0, 600.0, 2500.0, 9000.0]   # simplify tolerance (m) per level of detail


def _polys(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def geom_path(geom) -> MPath | None:
    verts, codes = [], []
    for poly in _polys(geom):
        poly = orient(poly, 1.0)
        for ring in [poly.exterior, *poly.interiors]:
            c = np.asarray(ring.coords)[:, :2]
            if len(c) < 4:
                continue
            verts.append(c)
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY]
    if not verts:
        return None
    return MPath(np.concatenate(verts), codes)


class MapView:
    def __init__(self, countries: gpd.GeoDataFrame, center: tuple[float, float],
                 extent: tuple[float, float, float, float], rivers: gpd.GeoDataFrame | None,
                 style: dict):
        self.style = style
        self.crs = f"+proj=aeqd +lat_0={center[1]} +lon_0={center[0]} +units=m +datum=WGS84"
        self.fwd = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        clip = box(*extent)
        reg = countries[countries.intersects(clip)].copy()
        reg["geometry"] = reg.geometry.intersection(clip)
        self.g = reg.to_crs(self.crs)
        self.geo_ll = reg  # lon/lat version (for clipping regions)
        self.by_iso = {iso: grp.geometry.union_all() for iso, grp in self.g.groupby("ADM0_A3")}

        self.fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.axis("off")
        self.ax.set_aspect("auto")
        self.fig.patch.set_facecolor(C(style["ocean"]))
        self.ax.set_facecolor(C(style["ocean"]))

        self._graticule(extent)
        self.base: list[list[PathPatch]] = [[] for _ in LODS]
        for geom in self.g.geometry:
            for li, tol in enumerate(LODS):
                p = geom_path(geom.simplify(tol))
                if p is None:
                    continue
                patch = PathPatch(p, facecolor=C(style["land"]), edgecolor=C(style["border"]),
                                  linewidth=1.1, zorder=2)
                self.ax.add_patch(patch)
                self.base[li].append(patch)

        self.rivers = None
        if rivers is not None:
            rv = rivers[rivers.intersects(clip)].to_crs(self.crs)
            segs = []
            for geom in rv.geometry:
                for ln in (geom.geoms if isinstance(geom, MultiLineString) else [geom]):
                    if isinstance(ln, LineString):
                        segs.append(np.asarray(ln.simplify(200).coords)[:, :2])
            self.rivers = LineCollection(segs, colors=C(style["river"]), linewidths=1.4, zorder=3)
            self.ax.add_collection(self.rivers)

        self.layer_artists: dict[str, list[tuple[list[PathPatch], float]]] = {}
        self.lod = -1

    # ---------- setup ----------
    def _graticule(self, extent):
        lon0, lat0, lon1, lat1 = extent
        segs = []
        for lon in range(int(math.floor(lon0 / 5) * 5), int(lon1) + 5, 5):
            lats = np.linspace(lat0, lat1, 80)
            segs.append(np.column_stack(self.fwd.transform(np.full_like(lats, lon), lats)))
        for lat in range(int(math.floor(lat0 / 5) * 5), int(lat1) + 5, 5):
            lons = np.linspace(lon0, lon1, 160)
            segs.append(np.column_stack(self.fwd.transform(lons, np.full_like(lons, lat))))
        self.ax.add_collection(LineCollection(segs, colors=C(self.style["graticule"]),
                                              linewidths=1.0, zorder=1))

    def geometry_for(self, spec: dict):
        """Projected geometry of a country/region layer spec."""
        isos = spec.get("iso") or spec.get("of")
        isos = [isos] if isinstance(isos, str) else list(isos)
        parts = [self.by_iso[i] for i in isos if i in self.by_iso]
        if not parts:
            raise ValueError(f"layer {spec['id']}: no country found for {isos}")
        geom = parts[0]
        for p in parts[1:]:
            geom = geom.union(p)
        if "clip" in spec:
            cb = gpd.GeoSeries([box(*spec["clip"])], crs=4326).to_crs(self.crs).iloc[0]
            geom = geom.intersection(cb)
        return geom

    def add_fill_layer(self, spec: dict):
        """country / region layers: filled shape + optional glow outline."""
        if spec["id"] in self.layer_artists:
            return
        geom = self.geometry_for(spec)
        fill = C(spec.get("fill", "yellow"))
        entries = []
        z = 4 + float(spec.get("z", 0))
        for li, tol in enumerate(LODS):
            g = geom.simplify(tol * 0.6)
            p = geom_path(g)
            if p is None:
                continue
            patches = [(PathPatch(p, facecolor=fill, edgecolor="none", zorder=z), 1.0)]
            outline = C(spec.get("outline", "#ffffff"))
            if spec.get("glow"):
                for lw, a in ((16, 0.10), (9, 0.22), (4, 0.55)):
                    patches.append((PathPatch(p, facecolor="none", edgecolor=fill, linewidth=lw,
                                              zorder=z + 0.1, joinstyle="round"), a))
            patches.append((PathPatch(p, facecolor="none", edgecolor=outline, linewidth=2.0,
                                      zorder=z + 0.2), 0.9))
            for pp, _ in patches:
                pp.set_alpha(0)
                self.ax.add_patch(pp)
            entries.append((li, patches))
        self.layer_artists[spec["id"]] = entries

    def measure_points(self, spec: dict):
        """Nearest points between two countries inside a lon/lat window -> lon/lat pair + km."""
        a_iso, b_iso = spec["between"]
        win = gpd.GeoSeries([box(*spec["within"])], crs=4326).to_crs(self.crs).iloc[0]
        a, b = nearest_points(self.by_iso[a_iso].intersection(win), self.by_iso[b_iso].intersection(win))
        km = a.distance(b) / 1000.0
        inv = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        return inv.transform(a.x, a.y), inv.transform(b.x, b.y), km

    # ---------- per frame ----------
    def set_camera(self, lon: float, lat: float, width_km: float):
        x, y = self.fwd.transform(lon, lat)
        w = width_km * 1000.0
        h = w * H / W
        self.view = (x - w / 2, y - h / 2, w, h)
        self.ax.set_xlim(x - w / 2, x + w / 2)
        self.ax.set_ylim(y - h / 2, y + h / 2)
        mpp = w / W
        # coarsest LOD whose simplification error stays under ~0.8 px
        lod = next((i for i in range(len(LODS) - 1, -1, -1) if LODS[i] <= mpp * 0.8), 0)
        if lod != self.lod:
            for i, patches in enumerate(self.base):
                for p in patches:
                    p.set_visible(i == lod)
            self.lod = lod
        if self.rivers is not None:
            # rivers only read well once we are zoomed in
            self.rivers.set_alpha(float(np.clip((1000 - width_km) / 500, 0, 0.6)))

    def set_layer_alpha(self, lid: str, alpha: float):
        for li, patches in self.layer_artists.get(lid, []):
            for pp, base_a in patches:
                vis = li == self.lod and alpha > 0.001
                pp.set_visible(vis)
                if vis:
                    pp.set_alpha(base_a * alpha)

    def to_px(self, lon: float, lat: float):
        x, y = self.fwd.transform(lon, lat)
        x0, y0, w, h = self.view
        return (x - x0) / w * W, (y0 + h - y) / h * H

    def render(self) -> np.ndarray:
        self.fig.canvas.draw()
        buf = np.asarray(self.fig.canvas.buffer_rgba())[:, :, :3]
        return buf.astype(np.float32) / 255.0
