"""Write an episode for one queue topic with Gemini, validated before it is accepted.

    GEMINI_API_KEY=... python -m reels.generate pakistan-8000ers

The topic's facts and coordinates come from topics/queue.json; Gemini only turns them into
a voice script and map choreography. Each draft goes through reels.validate; errors are sent
back to Gemini for up to 3 attempts, then the last draft is used with off-screen labels removed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .data import ROOT
from .validate import validate

QUEUE = ROOT / "topics" / "queue.json"
GEN_DIR = ROOT / "data" / "episodes"
EXAMPLE = ROOT / "episodes" / "wakhan-corridor-en.json"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RULES = """You write YouTube Shorts about maps and geography for an English-speaking audience.
You are given ONE topic with verified facts and coordinates. Turn it into an episode JSON
for our map-animation engine, in exactly the same format as the example episode.

Content rules:
- Use ONLY the facts given. Do not add numbers, dates, names or claims that are not in the facts.
- Scene 1 is the hook: a question or surprising claim, under 12 spoken words.
- Total spoken words across all scenes: 110-150 (about 45-55 seconds). 6-8 scenes.
- Short, punchy sentences. Conversational, curious tone. No "in this video", no emojis in "vo".
- The last scene ends with a one-line payoff, then: "Follow for more map secrets!"
- "vo" is what the narrator says. Write numbers the way they should be spoken in "vo"
  ("4,693 metres" is fine). "caption" may be omitted; it defaults to "vo".

Format rules:
- Scene 1: "mode": "globe", camera {"from": {"lon","lat","radius": 400}, "lon","lat","radius": 1900}
  pointing at the topic area, "globe_highlight": {"<ISO>": "orange"} for the main country.
- Every other scene: "mode" omitted (map), camera {"lon","lat","width_km"} (40-9000 km).
  The visible area is width_km wide and 1.78x that tall, centred on lon/lat.
- Change the camera every scene: zoom out for context, zoom in for details.
- Layer types: country (iso: code or list), region (part of a country: "of" + "clip" box),
  label, marker, measure, arrow, badge. Use ONLY these country codes: the topic's "countries"
  list. Use the topic's "regions" boxes for region layers. If the topic has a "measure", add exactly one measure layer that copies those keys as given ("between"+"within" or "points", and "text").
- Every layer needs a unique "id" and an "at" offset (seconds into the scene). Layers stay
  visible until a later scene lists their id in "hide". Hide what is no longer relevant.
- Labels and markers must be inside the camera view of their scene. Keep them away from the
  top 22% of the frame (title) and from the band 65-74% down the frame (captions).
- Badges: "text" max 12 characters (a number like "4,693 M"), "sub" max 28 characters.
- "title" (on-screen headline) max 40 characters, set on scene 1 and when the topic shifts.
  Wrap one key word in *asterisks* to colour it.
- Colours: yellow (main subject), orange, green, blue, red, purple, teal.
- Fill alpha 0.5-0.65 for countries, 0.95 with "glow": true, "pulse": true for the hero region.
- Top-level keys: slug, title, upload {title (max 90 chars, may end with 1-2 emojis),
  description (2 sentences), hashtags (6-8, include #shorts #geography #maps)}, scenes.
Return ONLY the JSON object."""


def load_topic(tid: str) -> dict:
    topics = json.loads(QUEUE.read_text(encoding="utf-8"))["topics"]
    for t in topics:
        if t["id"] == tid:
            return t
    raise SystemExit(f"topic '{tid}' not in {QUEUE}")


def ask_gemini(prompt: str, model: str) -> str:
    key = os.environ["GEMINI_API_KEY"]
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
    }).encode()
    req = urllib.request.Request(API.format(model=model), data=body, method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 503) and attempt < 3:
                wait = 20 * (attempt + 1)
                print(f"  {model}: HTTP {e.code}, retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini {model} HTTP {e.code}: {detail}") from e
    raise RuntimeError("unreachable")


def call(prompt: str) -> str:
    fake = os.environ.get("GEMINI_FAKE_RESPONSE")  # offline testing: path to a JSON reply
    if fake:
        return Path(fake).read_text(encoding="utf-8")
    model = os.environ.get("GEMINI_TEXT_MODEL") or "gemini-3.8-flash"
    fallback = os.environ.get("GEMINI_TEXT_FALLBACK", "gemini-3.5-flash-lite")
    try:
        return ask_gemini(prompt, model)
    except RuntimeError as e:
        if not fallback or any(f"HTTP {c}" in str(e) for c in (400, 401, 403)):
            raise  # bad key or request: the fallback model would fail the same way
        print(f"  {model} unavailable ({str(e)[:80]}); using {fallback}", flush=True)
        return ask_gemini(prompt, fallback)


def finish(ep: dict, topic: dict) -> dict:
    """House settings the model should not decide."""
    ep["slug"] = topic["id"]
    ep["map"] = {"pov": os.environ.get("MAP_POV", "PAK"), "rivers": True}
    ep["voice"] = {"name": os.environ.get("VOICE", "en-US-GuyNeural"), "rate": "+8%", "pitch": "+0Hz"}
    ep["brand"] = os.environ.get("BRAND", "")
    ep["thumbnail_at"] = round(sum(s.get("duration", 5) for s in ep["scenes"][:2]) - 0.8, 1)
    return ep


def generate(tid: str, recent_titles: list[str] | None = None) -> Path:
    topic = load_topic(tid)
    example = EXAMPLE.read_text(encoding="utf-8")
    base = (f"{RULES}\n\n<example_episode>\n{example}\n</example_episode>\n\n"
            f"<topic>\n{json.dumps(topic, ensure_ascii=False, indent=1)}\n</topic>\n")
    if recent_titles:
        base += "\n<recent_titles_do_not_repeat_style>\n" + "\n".join(recent_titles[-15:]) + "\n</recent_titles_do_not_repeat_style>\n"
    prompt, last, errs = base, None, []
    for attempt in range(1, 4):
        raw = call(prompt)
        try:
            ep = json.loads(raw)
        except json.JSONDecodeError as e:
            errs = [f"not valid JSON: {e}"]
            prompt = base + f"\nYour previous answer was not valid JSON ({e}). Return only JSON."
            continue
        ep = finish(ep, topic)
        errs, ep = validate(ep)
        last = ep
        if not errs:
            break
        print(f"  attempt {attempt}: {len(errs)} problems: {errs[:3]}", flush=True)
        prompt = (base + "\n<previous_attempt>\n" + json.dumps(ep, ensure_ascii=False)
                  + "\n</previous_attempt>\nFix ONLY these problems and return the full corrected JSON:\n- "
                  + "\n- ".join(errs[:15]))
    if last is None:
        raise RuntimeError(f"Gemini never returned usable JSON: {errs}")
    if errs:
        hard, last = validate(last, drop_bad=True)
        if hard:
            raise RuntimeError(f"episode for {tid} still invalid after 3 attempts: {hard[:5]}")
        print("  used last draft with off-screen layers removed", flush=True)
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    out = GEN_DIR / f"{tid}.json"
    out.write_text(json.dumps(finish(last, topic), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"episode written: {out}")
    return out


if __name__ == "__main__":
    generate(sys.argv[1])
