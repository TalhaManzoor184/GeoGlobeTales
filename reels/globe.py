"""Shaded 3D globe: an equirectangular texture projected orthographically per frame (numpy)."""
from __future__ import annotations

import hashlib
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from . import H, W
from .data import CACHE
from .style import STYLE, hex_rgb

TEX_W, TEX_H = 8192, 4096


def _texture(countries, highlight: dict[str, str]) -> np.ndarray:
    key = hashlib.md5(repr(sorted(highlight.items())).encode()).hexdigest()[:10]
    path = CACHE / f"globe_tex_{key}.png"
    if not path.exists():
        g = countries.copy()
        g["geometry"] = g.geometry.simplify(0.02)
        fig = plt.figure(figsize=(TEX_W / 100, TEX_H / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        fig.patch.set_facecolor(STYLE["ocean"])
        colors = [highlight.get(a, STYLE["land"]) for a in g["ADM0_A3"]]
        g.plot(ax=ax, color=colors, edgecolor=STYLE["border"], linewidth=0.9)
        ax.set_aspect("auto")
        ax.set_xlim(-180, 180)
        ax.set_ylim(-90, 90)
        fig.savefig(path, dpi=100, facecolor=STYLE["ocean"])
        plt.close(fig)
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0


class Globe:
    def __init__(self, countries):
        self.countries = countries
        self.tex_cache: dict[str, np.ndarray] = {}
        rng = np.random.default_rng(7)
        bg = np.zeros((H, W, 3), np.float32)
        top, bot = np.array(hex_rgb(STYLE["space_top"])), np.array(hex_rgb(STYLE["space_bottom"]))
        ramp = np.linspace(0, 1, H)[:, None, None]
        bg[:] = top * (1 - ramp) + bot * ramp
        n = 900
        ys, xs = rng.integers(0, H, n), rng.integers(0, W, n)
        bg[ys, xs] = np.clip(bg[ys, xs] + rng.uniform(0.3, 0.9, (n, 1)), 0, 1)
        self.bg = bg
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.xx, self.yy = xx, yy

    def texture(self, highlight: dict[str, str] | None) -> np.ndarray:
        key = repr(sorted((highlight or {}).items()))
        if key not in self.tex_cache:
            self.tex_cache[key] = _texture(self.countries, highlight or {})
        return self.tex_cache[key]

    def render(self, lon0: float, lat0: float, radius: float, highlight=None, hl_alpha=0.0,
               cy: float = H * 0.5) -> np.ndarray:
        cx = W * 0.5
        nx = (self.xx - cx) / radius
        ny = -(self.yy - cy) / radius
        rho2 = nx * nx + ny * ny
        inside = rho2 <= 1.0
        out = self.bg.copy()

        # atmosphere glow just outside the disc
        rho = np.sqrt(rho2)
        ring = (rho > 1.0) & (rho < 1.12)
        g = ((1.12 - rho[ring]) / 0.12) ** 2 * 0.65
        glow = np.array(hex_rgb(STYLE["atmosphere"]), np.float32)
        out[ring] = out[ring] * (1 - g[:, None]) + glow * g[:, None]

        if inside.any():
            x, y = nx[inside], ny[inside]
            z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
            la0, lo0 = math.radians(lat0), math.radians(lon0)
            lat = np.arcsin(np.clip(z * math.sin(la0) + y * math.cos(la0), -1, 1))
            lon = lo0 + np.arctan2(x, z * math.cos(la0) - y * math.sin(la0))
            u = ((np.degrees(lon) + 180.0) % 360.0) / 360.0 * (TEX_W - 1)
            v = (90.0 - np.degrees(lat)) / 180.0 * (TEX_H - 1)
            col = _bilinear(self.texture(None), u, v)
            if highlight and hl_alpha > 0:
                col2 = _bilinear(self.texture(highlight), u, v)
                col = col * (1 - hl_alpha) + col2 * hl_alpha
            # lambert light from upper-left + soft rim
            light = np.clip(-0.45 * x + 0.55 * y + 0.70 * z, 0, 1)
            shade = 0.45 + 0.65 * light
            rim = np.clip((1 - z) * 1.6, 0, 1) ** 2 * 0.35
            col = col * shade[:, None] * (1 - rim[:, None]) + glow * rim[:, None]
            out[inside] = np.clip(col, 0, 1)
        return out

    @staticmethod
    def project(lon: float, lat: float, lon0: float, lat0: float, radius: float,
                cy: float = H * 0.5):
        la, lo, la0, lo0 = map(math.radians, (lat, lon, lat0, lon0))
        cosc = math.sin(la0) * math.sin(la) + math.cos(la0) * math.cos(la) * math.cos(lo - lo0)
        x = radius * math.cos(la) * math.sin(lo - lo0)
        y = radius * (math.cos(la0) * math.sin(la) - math.sin(la0) * math.cos(la) * math.cos(lo - lo0))
        return W * 0.5 + x, cy - y, cosc > 0


def _bilinear(tex: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    x0 = np.floor(u).astype(np.int32)
    y0 = np.floor(v).astype(np.int32)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    x1 = (x0 + 1) % TEX_W
    y1 = np.minimum(y0 + 1, TEX_H - 1)
    return (tex[y0, x0] * (1 - fx) * (1 - fy) + tex[y0, x1] * fx * (1 - fy)
            + tex[y1, x0] * (1 - fx) * fy + tex[y1, x1] * fx * fy)
