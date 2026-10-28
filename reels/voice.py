"""Generate the Urdu voiceover per scene with edge-tts and record each line's length.

    python -m reels.voice episodes/wakhan-corridor.json

Writes out/<slug>/voice/<scene>.mp3 and timings.json. The renderer then stretches each
scene to its voice line so captions and animations stay in sync.

edge-tts is free and needs no key but uses Microsoft's online service, so it must run on a
machine with normal internet (your PC or GitHub Actions). To use your own recording or
ElevenLabs instead, drop files named <scene-id>.mp3 into out/<slug>/voice/ and run
`python -m reels.voice <episode> --measure-only`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path


def duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


async def synth(text: str, voice: str, rate: str, pitch: str, dest: Path):
    import edge_tts  # imported lazily so rendering works without it
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(dest))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--measure-only", action="store_true", help="only measure existing mp3s")
    args = ap.parse_args(argv)

    path = Path(args.episode)
    ep = json.loads(path.read_text(encoding="utf-8"))
    v = ep.get("voice", {})
    vdir = path.resolve().parent.parent / "out" / ep["slug"] / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    timings = {}
    for i, s in enumerate(ep["scenes"]):
        sid = s.get("id", f"s{i + 1}")
        dest = vdir / f"{sid}.mp3"
        text = s.get("vo") or s.get("vo_ur") or s.get("caption") or s.get("vo_roman")
        if not text:
            continue
        if not args.measure_only and not dest.exists():
            asyncio.run(synth(text, v.get("name", "ur-PK-AsadNeural"), v.get("rate", "+6%"),
                              v.get("pitch", "+0Hz"), dest))
        if dest.exists():
            timings[sid] = round(duration(dest), 3)
            print(f"{sid}: {timings[sid]:.2f}s")
    (vdir / "timings.json").write_text(json.dumps(timings, indent=2))
    print(f"total voice {sum(timings.values()):.1f}s -> {vdir / 'timings.json'}")


if __name__ == "__main__":
    main()
