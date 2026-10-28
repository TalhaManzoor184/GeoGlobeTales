"""Render an episode to MP4.

    python -m reels.render episodes/wakhan-corridor.json            # full reel
    python -m reels.render episodes/wakhan-corridor.json --stills 1,8,20   # PNG checks only
    python -m reels.render episodes/wakhan-corridor.json --music bg.mp3
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np

from . import FPS, H, W
from .data import countries_file, rivers_file
from .globe import Globe
from .mapview import MapView
from .overlay import Overlay, finish
from .style import STYLE, color as C
from .timeline import FADE, Timeline, load_episode, smooth

XFADE = 0.6  # globe -> map crossfade


class Renderer:
    def __init__(self, ep: dict, timings: dict):
        self.ep = ep
        self.style = {**STYLE, **ep.get("style", {})}
        self.tl = Timeline(ep, timings)
        m = ep["map"]
        world = gpd.read_file(countries_file(m.get("pov")))
        self.has_globe = any(s.mode == "globe" for s in self.tl.scenes)
        self.globe = Globe(world) if self.has_globe else None
        rivers = gpd.read_file(rivers_file()) if m.get("rivers", True) else None
        self.map = MapView(world, tuple(m["center"]), tuple(m["extent"]), rivers, self.style)
        self.ov = Overlay(self.style)
        # pre-build fill layers and measurements
        self.measures = {}
        for lid, layer in self.tl.layers.items():
            spec = layer.spec
            if spec["type"] in ("country", "region"):
                self.map.add_fill_layer(spec)
            elif spec["type"] == "measure":
                self.measures[lid] = self.map.measure_points(spec)
        self._globe_end_cache = None

    # ---------- frame ----------
    def frame(self, t: float):
        s, cam = self.tl.camera(t)
        if s.mode == "globe":
            img = self._globe_frame(s, cam, t)
            proj = lambda lon, lat: self._globe_proj(lon, lat, cam)
        else:
            img = self._map_frame(cam, t)
            proj = lambda lon, lat: (*self.map.to_px(lon, lat), True)
            lt = t - s.start
            if s.idx > 0 and self.tl.scenes[s.idx - 1].mode == "globe" and lt < XFADE:
                g = self._globe_end(self.tl.scenes[s.idx - 1])
                k = smooth(lt / XFADE)
                img = g * (1 - k) + img * k
        base = finish(img)
        self.ov.reset()
        self._overlays(t, s, proj)
        base.alpha_composite(self.ov.img)
        return base.convert("RGB")

    def _globe_frame(self, s, cam, t):
        hl = {k: C(v) for k, v in s.raw.get("globe_highlight", {}).items()}
        a = smooth((t - s.start - s.raw.get("highlight_at", 0.8)) / 0.6) if hl else 0.0
        return self.globe.render(cam["lon"], cam["lat"], cam["radius"], hl, a)

    def _globe_end(self, s):
        if self._globe_end_cache is None:
            cam = self.tl.cam_end(s)
            self._globe_end_cache = self._globe_frame(s, cam, s.end)
        return self._globe_end_cache

    def _globe_proj(self, lon, lat, cam):
        x, y, vis = Globe.project(lon, lat, cam["lon"], cam["lat"], cam["radius"])
        return x, y, vis

    def _map_frame(self, cam, t):
        self.map.set_camera(cam["lon"], cam["lat"], cam["width_km"])
        for lid, layer in self.tl.layers.items():
            spec = layer.spec
            if spec["type"] not in ("country", "region"):
                continue
            a = layer.alpha(t) * float(spec.get("alpha", 0.9))
            if spec.get("pulse") and a > 0:
                a *= 0.72 + 0.28 * (0.5 + 0.5 * np.sin(t * 2 * np.pi * 0.8))
            self.map.set_layer_alpha(lid, a)
        return self.map.render()

    def _overlays(self, t, s, proj):
        ov = self.ov
        for lid, layer in self.tl.layers.items():
            spec = layer.spec
            typ = spec["type"]
            a = layer.alpha(t)
            if a <= 0.01 or typ in ("country", "region"):
                continue
            if s.mode == "globe" and not spec.get("on_globe", typ == "marker"):
                continue
            age = layer.age(t)
            if typ == "label":
                x, y, vis = proj(spec["lon"], spec["lat"])
                if vis:
                    ov.label((x, y), spec, a, age)
            elif typ == "marker":
                x, y, vis = proj(spec["lon"], spec["lat"])
                if vis:
                    ov.marker((x, y), spec, a, t)
            elif typ == "measure":
                (la, pa), (lb, pb), _ = self.measures[lid]
                ov.measure(proj(la, pa)[:2], proj(lb, pb)[:2], spec, a, age)
            elif typ == "arrow":
                ov.arrow(proj(*spec["from"])[:2], proj(*spec["to"])[:2], spec, a, age)
            elif typ == "badge":
                ov.badge(spec, a, age)
        if s.title:
            # the title pops in on the scene that set it
            setter = next(sc for sc in reversed(self.tl.scenes[:s.idx + 1]) if sc.title_changed)
            ov.title(s.title, t - setter.start)
        ov.caption(self.tl.caption_words(s), t)
        ov.progress(t / self.tl.duration)
        ov.brand(self.ep.get("brand", ""))


# ---------- audio / sidecar files ----------
def build_audio(tl: Timeline, voice_dir: Path, music: Path | None, out: Path) -> Path | None:
    clips = [(s, voice_dir / f"{s.sid}.mp3") for s in tl.scenes]
    clips = [(s, p) for s, p in clips if p.exists()]
    if not clips and not music:
        return None
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    filt, labels = [], []
    for i, (s, p) in enumerate(clips):
        cmd += ["-i", str(p)]
        ms = int(s.start * 1000 + 100)
        filt.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms}[v{i}]")
        labels.append(f"[v{i}]")
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        mi = len(clips)
        filt.append(f"[{mi}:a]aresample=48000,volume=0.12,atrim=0:{tl.duration:.2f}[m]")
        labels.append("[m]")
    filt.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
                f"apad,atrim=0:{tl.duration:.2f}[a]")
    cmd += ["-filter_complex", ";".join(filt), "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_sidecars(ep: dict, tl: Timeline, out_dir: Path):
    lines, n = [], 1
    for s in tl.scenes:
        if not s.vo_roman:
            continue
        end = s.start + (s.vo_len or s.duration - 0.3)
        lines += [str(n), f"{srt_time(s.start + 0.1)} --> {srt_time(end)}", s.vo_roman, ""]
        n += 1
    (out_dir / "captions_roman.srt").write_text("\n".join(lines), encoding="utf-8")

    md = [f"# {ep['title']}", "", f"Total: ~{tl.duration:.0f}s", ""]
    for s in tl.scenes:
        md += [f"## {s.sid}  ({s.start:.1f}s – {s.end:.1f}s)", "",
               f"**Urdu (TTS):** {s.vo_ur}", "", f"**Roman Urdu:** {s.vo_roman}", ""]
    meta = ep.get("upload", {})
    if meta:
        md += ["## Upload", "", f"**Title:** {meta.get('title', '')}", "",
               f"**Description:** {meta.get('description', '')}", "",
               f"**Hashtags:** {' '.join(meta.get('hashtags', []))}", ""]
    (out_dir / "script.md").write_text("\n".join(md), encoding="utf-8")


# ---------- main ----------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--stills", help="comma-separated seconds; write PNGs instead of a video")
    ap.add_argument("--music", help="background music file (mixed at low volume)")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--crf", type=int, default=20)
    args = ap.parse_args(argv)

    ep, timings = load_episode(Path(args.episode))
    root = Path(args.episode).resolve().parent.parent
    out_dir = root / "out" / ep["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    r = Renderer(ep, timings)
    tl = r.tl
    print(f"{ep['slug']}: {len(tl.scenes)} scenes, {tl.duration:.1f}s "
          f"({'voice timings' if timings else 'config timings'})", flush=True)

    if args.stills:
        for ts in args.stills.split(","):
            t = float(ts)
            r.frame(t).save(out_dir / f"still_{t:05.1f}.png")
            print("still", t, flush=True)
        return

    write_sidecars(ep, tl, out_dir)
    silent = out_dir / "video_silent.mp4"
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(args.fps), "-i", "-",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(args.crf),
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent)],
        stdin=subprocess.PIPE)
    n = int(round(tl.duration * args.fps))
    t0 = time.time()
    thumb_t = float(ep.get("thumbnail_at", 3.0))
    for i in range(n):
        t = i / args.fps
        img = r.frame(t)
        if abs(t - thumb_t) < 0.5 / args.fps:
            img.save(out_dir / "thumbnail.png")
        enc.stdin.write(img.tobytes())
        if i % (args.fps * 5) == 0:
            el = time.time() - t0
            print(f"  {t:5.1f}s / {tl.duration:.1f}s  ({el:.0f}s elapsed)", flush=True)
    enc.stdin.close()
    if enc.wait() != 0:
        sys.exit("ffmpeg failed")

    final = out_dir / f"{ep['slug']}.mp4"
    audio = build_audio(tl, out_dir / "voice", Path(args.music) if args.music else None,
                        out_dir / "audio.m4a")
    if audio:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(audio),
                        "-c:v", "copy", "-c:a", "copy", "-shortest", str(final)], check=True)
        silent.unlink()
    else:
        shutil.move(silent, final)
    print(f"done: {final} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
