"""Turns an episode config into a timeline: scene times, camera path, layer visibility, captions."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

FADE = 0.45          # seconds for layer fade in/out
DRIFT = 0.012        # slow zoom-in per second while the camera holds (keeps frames alive)
VO_PAD = 0.45        # silence kept after each voice line


def ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)  # smootherstep


def smooth(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


@dataclass
class Scene:
    idx: int
    sid: str
    mode: str                 # "globe" | "map"
    start: float
    duration: float
    cam: dict                 # target camera
    move: float               # seconds to reach target
    title: str | None         # active title (inherited)
    title_changed: bool
    vo_roman: str             # caption text ("caption", or legacy "vo_roman")
    vo_ur: str                # text sent to TTS ("vo", or legacy "vo_ur")
    vo_len: float | None      # measured voice length, if any
    raw: dict

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class LayerInterval:
    on: float
    off: float = math.inf


@dataclass
class Layer:
    lid: str
    spec: dict
    intervals: list[LayerInterval] = field(default_factory=list)

    def alpha(self, t: float) -> float:
        a = 0.0
        for iv in self.intervals:
            if t < iv.on or t > iv.off + FADE:
                continue
            a = max(a, smooth((t - iv.on) / FADE) * (1 - smooth((t - iv.off) / FADE)))
        return a

    def age(self, t: float) -> float:
        """Seconds since the most recent show (for grow/pop animations)."""
        ons = [iv.on for iv in self.intervals if iv.on <= t]
        return t - max(ons) if ons else -1.0


class Timeline:
    def __init__(self, episode: dict, timings: dict | None = None):
        self.ep = episode
        timings = timings or {}
        self.scenes: list[Scene] = []
        self.layers: dict[str, Layer] = {}
        t = 0.0
        title = None
        for i, s in enumerate(episode["scenes"]):
            sid = s.get("id", f"s{i + 1}")
            vo_len = timings.get(sid)
            cfg_dur = float(s.get("duration", 5.0))
            dur = cfg_dur
            if vo_len:
                # the voice decides the pace; min_duration keeps room for long animations
                dur = max(float(s.get("min_duration", 0)), vo_len + VO_PAD)
            scale = dur / cfg_dur  # keeps cue offsets ("at") proportional to the new length
            changed = "title" in s
            if changed:
                title = s["title"]
            cam = s.get("camera", {})
            move = min(float(cam.get("move", min(1.6, cfg_dur * 0.6))) * min(scale, 1.0), dur)
            sc = Scene(i, sid, s.get("mode", "map"), t, dur, cam, move, title, changed,
                       s.get("caption", s.get("vo_roman", "")), s.get("vo", s.get("vo_ur", "")), vo_len, s)
            self.scenes.append(sc)
            for h in s.get("hide", []):
                hid, at = (h, 0.0) if isinstance(h, str) else (h["id"], float(h.get("at", 0)) * scale)
                if hid in self.layers and self.layers[hid].intervals:
                    self.layers[hid].intervals[-1].off = t + at
            for spec in s.get("show", []):
                lid = spec["id"]
                on = t + float(spec.get("at", 0.0)) * scale
                if lid not in self.layers:
                    self.layers[lid] = Layer(lid, spec)
                elif len(spec) > 2:
                    self.layers[lid].spec = {**self.layers[lid].spec, **spec}
                self.layers[lid].intervals.append(LayerInterval(on))
            t += dur
        self.duration = t
        self._cam_cache: dict[int, dict] = {}

    # ---------- scenes ----------
    def scene_at(self, t: float) -> Scene:
        for s in self.scenes:
            if t < s.end:
                return s
        return self.scenes[-1]

    # ---------- camera ----------
    def _target(self, s: Scene) -> dict:
        if s.mode == "globe":
            return {"lon": s.cam.get("lon", 0.0), "lat": s.cam.get("lat", 20.0),
                    "radius": s.cam.get("radius", 430.0)}
        return {"lon": s.cam["lon"], "lat": s.cam["lat"], "width_km": s.cam["width_km"]}

    def _start_cam(self, s: Scene) -> dict:
        if s.idx == 0:
            st = s.cam.get("from")
            return dict(st) if st else self._target(s)
        prev = self.scenes[s.idx - 1]
        pc = self.cam_end(prev)
        if prev.mode == s.mode:
            return pc
        if prev.mode == "globe" and s.mode == "map":
            # equivalent flat view of the globe's final framing
            from . import W
            return {"lon": pc["lon"], "lat": pc["lat"], "width_km": W / pc["radius"] * 6371.0}
        return self._target(s)

    def cam_end(self, s: Scene) -> dict:
        if s.idx not in self._cam_cache:
            self._cam_cache[s.idx] = self._cam_local(s, s.duration)
        return self._cam_cache[s.idx]

    def _cam_local(self, s: Scene, lt: float) -> dict:
        a = self._start_cam(s)
        b = self._target(s)
        k = ease(lt / s.move) if s.move > 0 else 1.0
        hold = max(0.0, lt - s.move)
        drift = s.cam.get("drift", DRIFT)
        out = {"lon": a["lon"] + (b["lon"] - a["lon"]) * k,
               "lat": a["lat"] + (b["lat"] - a["lat"]) * k}
        if s.mode == "globe":
            out["radius"] = math.exp(math.log(a["radius"]) + (math.log(b["radius"]) - math.log(a["radius"])) * k)
        else:
            w = math.exp(math.log(a["width_km"]) + (math.log(b["width_km"]) - math.log(a["width_km"])) * k)
            out["width_km"] = w * (1 - drift) ** hold
        return out

    def camera(self, t: float) -> tuple[Scene, dict]:
        s = self.scene_at(t)
        return s, self._cam_local(s, t - s.start)

    # ---------- captions ----------
    def caption_words(self, s: Scene) -> list[tuple[str, float, float]]:
        """Roman-Urdu caption words with absolute (start, end) times spread over the voice line."""
        words = s.vo_roman.split()
        if not words:
            return []
        span = (s.vo_len or max(0.5, s.duration - VO_PAD)) - 0.1
        weights = [len(re.sub(r"\W", "", w)) + 2 for w in words]
        total = sum(weights)
        out, acc = [], s.start + 0.1
        for w, wt in zip(words, weights):
            d = span * wt / total
            out.append((w, acc, acc + d))
            acc += d
        return out


def load_episode(path: Path) -> tuple[dict, dict]:
    ep = json.loads(Path(path).read_text(encoding="utf-8"))
    out_dir = Path(path).resolve().parent.parent / "out" / ep["slug"]
    tf = out_dir / "voice" / "timings.json"
    timings = json.loads(tf.read_text()) if tf.exists() else {}
    return ep, timings
