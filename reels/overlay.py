"""Text and graphics drawn on top of the map with Pillow: title, karaoke captions, labels,
markers, measurement lines, arrows and stat badges."""
from __future__ import annotations

import math
import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import H, W
from .data import font_file
from .style import hex_rgba255 as RGBA
from .timeline import ease, smooth

TITLE_Y = 250          # below the Shorts top bar
CAPTION_Y = 1330       # above the Shorts bottom UI
SAFE_X = 70


@lru_cache(maxsize=256)
def F(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_file(weight)), max(8, int(size)))


def _vignette() -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2)
    v = 1 - np.clip((d - 0.55) / 0.6, 0, 1) ** 1.6 * 0.55
    # darken top & bottom bands so text always reads
    band = np.ones(H, np.float32)
    band[:520] *= np.linspace(0.62, 1, 520)
    band[-620:] *= np.linspace(1, 0.55, 620)
    return (v * band[:, None])[:, :, None]


VIGNETTE = None


def finish(base: np.ndarray) -> Image.Image:
    global VIGNETTE
    if VIGNETTE is None:
        VIGNETTE = _vignette()
    arr = np.clip(base * VIGNETTE * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


class Overlay:
    def __init__(self, style: dict):
        self.st = style
        self.img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def reset(self):
        self.img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    # ---------- primitives ----------
    def text(self, xy, s, weight, size, fill, alpha=1.0, anchor="mm", stroke=None, sw=None):
        if alpha <= 0.01:
            return
        f = F(weight, size)
        sw = sw if sw is not None else max(3, int(size * 0.10))
        self.d.text(xy, s, font=f, fill=RGBA(fill, alpha), anchor=anchor,
                    stroke_width=sw, stroke_fill=RGBA(stroke or self.st["stroke"], alpha))

    def rich_lines(self, text: str, weight: str, size: int, max_w: int):
        """Wrap words; *word* marks accent words. Returns lines of [(word, accent)]."""
        f = F(weight, size)
        words = [(m.group(1) or m.group(2), bool(m.group(1))) for m in re.finditer(r"\*([^*]+)\*|(\S+)", text)]
        lines, cur = [], []
        for w, acc in words:
            trial = " ".join(x for x, _ in cur + [(w, acc)])
            if cur and f.getlength(trial) > max_w:
                lines.append(cur)
                cur = []
            cur.append((w, acc))
        if cur:
            lines.append(cur)
        return lines

    def rich_block(self, text, cx, cy, weight, size, alpha, max_w, accent_color=None,
                   base_color=None, line_gap=1.12):
        accent_color = accent_color or self.st["accent"]
        base_color = base_color or self.st["text"]
        lines = self.rich_lines(text, weight, size, max_w)
        while len(lines) > 3 and size > 40:
            size = int(size * 0.88)
            lines = self.rich_lines(text, weight, size, max_w)
        f = F(weight, size)
        lh = size * line_gap
        y = cy - lh * (len(lines) - 1) / 2
        space = f.getlength(" ")
        for line in lines:
            widths = [f.getlength(w) for w, _ in line]
            x = cx - (sum(widths) + space * (len(line) - 1)) / 2
            for (w, acc), wd in zip(line, widths):
                self.text((x, y), w, weight, size, accent_color if acc else base_color, alpha, anchor="lm")
                x += wd + space
            y += lh

    # ---------- elements ----------
    def title(self, text: str, age: float):
        k = ease(age / 0.35)
        size = int(84 * (0.86 + 0.14 * k))
        self.rich_block(text.upper(), W / 2, TITLE_Y, "Black", size, k, W - 2 * SAFE_X)

    def caption(self, words: list[tuple[str, float, float]], t: float, max_chars: int = 18):
        if not words or t < words[0][1] or t > words[-1][2] + 0.4:
            return
        chunks, cur = [], []
        for w in words:
            if cur and len(" ".join(x[0] for x in cur + [w])) > max_chars:
                chunks.append(cur)
                cur = []
            cur.append(w)
            if re.search(r"[.?!,]$", w[0]):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        chunk = next((c for c in chunks if c[0][1] <= t <= c[-1][2] + 0.05), None)
        if chunk is None:
            chunk = chunks[-1] if t > chunks[-1][-1][2] else None
        if chunk is None:
            return
        pop = 1 + 0.14 * (1 - ease((t - chunk[0][1]) / 0.14))
        size = int(78 * pop)
        f = F("ExtraBold", size)
        toks = [re.sub(r"[,]$", "", w[0]).upper() for w in chunk]
        widths = [f.getlength(s) for s in toks]
        space = f.getlength(" ")
        total = sum(widths) + space * (len(toks) - 1)
        scale = min(1.0, (W - 2 * SAFE_X) / max(total, 1))
        if scale < 1:
            size = int(size * scale)
            f = F("ExtraBold", size)
            widths = [f.getlength(s) for s in toks]
            space = f.getlength(" ")
            total = sum(widths) + space * (len(toks) - 1)
        x = W / 2 - total / 2
        for (w, s0, s1), tok, wd in zip(chunk, toks, widths):
            active = s0 <= t <= s1 + 0.05
            col = self.st["accent"] if active else self.st["text"]
            self.text((x, CAPTION_Y), tok, "ExtraBold", size, col, 1.0, anchor="lm", sw=8)
            x += wd + space

    def label(self, xy, spec, alpha, age):
        if not _onscreen(xy, 200):
            return
        k = ease(age / 0.4)
        size = int(spec.get("size", 46) * (0.9 + 0.1 * k))
        y = xy[1] + (1 - k) * 14
        if spec.get("dot"):
            r = 9
            self.d.ellipse([xy[0] - r, xy[1] - r, xy[0] + r, xy[1] + r],
                           fill=RGBA(spec.get("color", "white"), alpha), outline=RGBA(self.st["stroke"], alpha), width=3)
            y -= size * 0.9
        txt = spec["text"].upper() if spec.get("upper", True) else spec["text"]
        lines = txt.split("\n")
        for i, ln in enumerate(lines):
            self.text((xy[0], y + (i - (len(lines) - 1) / 2) * size * 1.05), ln,
                      spec.get("weight", "ExtraBold"), size, spec.get("color", "white"), alpha)

    def marker(self, xy, spec, alpha, t):
        if not _onscreen(xy, 300):
            return
        col = spec.get("color", "yellow")
        ph = (t * 0.75) % 1.0
        rr = 14 + 46 * ph
        self.d.ellipse([xy[0] - rr, xy[1] - rr, xy[0] + rr, xy[1] + rr],
                       outline=RGBA(col, alpha * (1 - ph)), width=5)
        r = 13
        self.d.ellipse([xy[0] - r, xy[1] - r, xy[0] + r, xy[1] + r], fill=RGBA(col, alpha),
                       outline=RGBA("#ffffff", alpha), width=4)
        if spec.get("text"):
            side = spec.get("side", "right")
            dx = 34 if side == "right" else -34
            anchor = "lm" if side == "right" else "rm"
            if side in ("top", "bottom"):
                dx, anchor = 0, "mm"
            dy = {"top": -52, "bottom": 52}.get(side, 0)
            lines = spec["text"].upper().split("\n")
            size = spec.get("size", 40)
            for i, ln in enumerate(lines):
                self.text((xy[0] + dx, xy[1] + dy + (i - (len(lines) - 1) / 2) * size * 1.1), ln,
                          "ExtraBold", size, spec.get("text_color", "white"), alpha, anchor=anchor)

    def measure(self, a, b, spec, alpha, age):
        k = ease(age / 0.7)
        col = spec.get("color", "yellow")
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        pa = (mx + (a[0] - mx) * k, my + (a[1] - my) * k)
        pb = (mx + (b[0] - mx) * k, my + (b[1] - my) * k)
        self.d.line([pa, pb], fill=RGBA(self.st["stroke"], alpha), width=14)
        self.d.line([pa, pb], fill=RGBA(col, alpha), width=7)
        ang = math.atan2(b[1] - a[1], b[0] - a[0]) + math.pi / 2
        for p in (pa, pb):
            dx, dy = math.cos(ang) * 22, math.sin(ang) * 22
            self.d.line([(p[0] - dx, p[1] - dy), (p[0] + dx, p[1] + dy)], fill=RGBA(col, alpha), width=7)
        if k > 0.6:
            la = alpha * smooth((k - 0.6) / 0.4)
            off = spec.get("label_offset", [150, -40])
            lx, ly = mx + off[0], my + off[1]
            f = F("Black", spec.get("size", 64))
            tw = f.getlength(spec["text"])
            pad = 22
            self.d.rounded_rectangle([lx - tw / 2 - pad, ly - 46, lx + tw / 2 + pad, ly + 46], radius=26,
                                     fill=RGBA(col, la), outline=RGBA(self.st["stroke"], la), width=5)
            self.text((lx, ly), spec["text"], "Black", spec.get("size", 64), self.st["stroke"], la,
                      stroke=col, sw=0)

    def arrow(self, a, b, spec, alpha, age):
        k = ease(age / spec.get("grow", 0.9))
        col = spec.get("color", "white")
        tip = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        width = spec.get("width", 14)
        self.d.line([a, tip], fill=RGBA(self.st["stroke"], alpha), width=width + 8)
        self.d.line([a, tip], fill=RGBA(col, alpha), width=width)
        ang = math.atan2(tip[1] - a[1], tip[0] - a[0])
        hl = width * 3.2
        pts = [tip,
               (tip[0] - hl * math.cos(ang - 0.45), tip[1] - hl * math.sin(ang - 0.45)),
               (tip[0] - hl * math.cos(ang + 0.45), tip[1] - hl * math.sin(ang + 0.45))]
        self.d.polygon(pts, fill=RGBA(col, alpha), outline=RGBA(self.st["stroke"], alpha))

    def badge(self, spec, alpha, age):
        k = ease(age / 0.45)
        cy = spec.get("y", 0.30) * H + (1 - k) * 40
        big, sub = spec["text"], spec.get("sub", "")
        fb = F("Black", spec.get("size", 120))
        fs = F("ExtraBold", spec.get("sub_size", 42))
        bw = max(fb.getlength(big), fs.getlength(sub.upper()) if sub else 0) + 90
        bh = spec.get("size", 120) * 1.15 + (spec.get("sub_size", 42) * 1.5 if sub else 0) + 40
        x0, x1 = W / 2 - bw / 2, W / 2 + bw / 2
        y0, y1 = cy - bh / 2, cy + bh / 2
        self.d.rounded_rectangle([x0, y0, x1, y1], radius=38, fill=RGBA(self.st["panel"], alpha),
                                 outline=RGBA(spec.get("color", "yellow"), alpha), width=5)
        by = y0 + 20 + spec.get("size", 120) * 0.6
        self.text((W / 2, by), big, "Black", spec.get("size", 120), spec.get("color", "yellow"), alpha, sw=0)
        if sub:
            self.text((W / 2, y1 - 20 - spec.get("sub_size", 42) * 0.75), sub.upper(), "ExtraBold",
                      spec.get("sub_size", 42), "white", alpha, sw=0)

    def progress(self, frac: float):
        self.d.rectangle([0, 0, W, 9], fill=(255, 255, 255, 50))
        self.d.rectangle([0, 0, int(W * frac), 9], fill=RGBA(self.st["accent"]))

    def brand(self, text: str):
        if text:
            self.text((W / 2, 1660), text, "SemiBold", 34, "white", 0.75, sw=3)


def _onscreen(xy, margin):
    return -margin < xy[0] < W + margin and -margin < xy[1] < H + margin
