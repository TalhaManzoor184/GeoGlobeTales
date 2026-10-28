"""Daily job: next topic -> episode (Gemini) -> voice -> render -> YouTube.

    python -m reels.daily --mode scheduled   # cron: skip if today's slot is already filled
    python -m reels.daily --mode full        # make one now, scheduled for the next slot
    python -m reels.daily --mode publish-now # make one now, public immediately
    python -m reels.daily --mode no-upload   # make one, keep the video, no YouTube
    python -m reels.daily --mode test        # Wakhan episode, no Gemini, no YouTube
    python -m reels.daily --mode upload-test # Wakhan episode uploaded PRIVATE (checks YouTube keys)

data/history.json records every uploaded episode, so a topic is used once.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from .data import OUT, ROOT

HISTORY = ROOT / "data" / "history.json"
QUEUE = ROOT / "topics" / "queue.json"
TEST_EPISODE = ROOT / "episodes" / "wakhan-corridor-en.json"


def load_history() -> list[dict]:
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else []


def save_history(h: list[dict]):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(h, indent=2, ensure_ascii=False) + "\n")


def summary(text: str):
    print(text, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")


def run(*args: str):
    subprocess.run([sys.executable, "-m", *args], check=True, cwd=ROOT)


def next_topics(history: list[dict]) -> list[dict]:
    done = {h["topic"] for h in history if h.get("videoId")}
    topics = json.loads(QUEUE.read_text(encoding="utf-8"))["topics"]
    return [t for t in topics if t["id"] not in done]


def make_episode(topic: dict, history: list[dict]) -> Path:
    if topic.get("episode"):
        return ROOT / topic["episode"]
    ready = ROOT / "data" / "episodes" / f"{topic['id']}.json"
    if ready.exists():  # written earlier (e.g. a run whose upload failed); reuse it
        return ready
    from .generate import GEN_DIR, generate  # noqa: F401
    return generate(topic["id"], [h["title"] for h in history])


def produce(ep_path: Path, allow_silent: bool = False) -> tuple[dict, Path]:
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    try:
        run("reels.voice", str(ep_path))
    except subprocess.CalledProcessError:
        # a silent Short is worse than none: stop instead of uploading it
        if not allow_silent:
            raise SystemExit("voice generation failed; not uploading a silent video")
        print("::warning::voice failed; rendering silent (test mode)", flush=True)
    run("reels.render", str(ep_path))
    return ep, OUT / ep["slug"] / f"{ep['slug']}.mp4"


UPLOAD_SECRETS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")


def preflight(mode: str):
    """Fail in seconds, with a readable reason, instead of after a full render."""
    need = []
    if mode not in ("test", "no-upload"):
        need += UPLOAD_SECRETS
    if mode not in ("test", "upload-test"):
        need.append("GEMINI_API_KEY")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing GitHub secrets: {', '.join(missing)}. "
                         "Add them in Settings → Secrets and variables → Actions.")
    if mode not in ("test", "no-upload"):
        from .upload import diagnose, youtube
        try:
            ch = youtube().channels().list(part="snippet", mine=True).execute()
            name = ch["items"][0]["snippet"]["title"] if ch.get("items") else "(no channel)"
            print(f"YouTube channel: {name}", flush=True)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "invalid_grant" in msg:
                msg = ("YouTube refresh token is invalid or expired (invalid_grant). The token must be "
                       "created with the SAME client id/secret that are in this repo's secrets. "
                       f"Secrets look like: {diagnose()}")
            elif "insufficient" in msg.lower() or "scope" in msg.lower():
                msg = "Refresh token lacks youtube.readonly scope; re-run reels.auth_youtube. " + msg[:200]
            raise SystemExit(f"YouTube login failed: {msg[:400]}")


def main():
    try:
        _main()
    except BaseException as e:  # surface the reason as an annotation, readable without logs
        if isinstance(e, SystemExit) and e.code in (0, None):
            raise
        msg = str(e) if not isinstance(e, SystemExit) else str(e.code)
        print(f"::error title=Daily short failed::{type(e).__name__}: {msg[:900]}".replace("\n", " "), flush=True)
        raise


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="scheduled",
                    choices=["scheduled", "full", "publish-now", "no-upload", "test", "upload-test"])
    mode = ap.parse_args().mode
    history = load_history()
    preflight(mode)

    if mode in ("test", "upload-test"):
        ep, video = produce(TEST_EPISODE, allow_silent=mode == "test")
        if mode == "upload-test":
            from .upload import upload
            res = upload(str(video), ep, mode="private")
            summary(f"Test upload (private): https://youtube.com/shorts/{res['videoId']}")
        else:
            summary(f"Test render OK: {video.name}")
        return

    if mode == "scheduled":
        from .upload import publish_at_iso
        slot = publish_at_iso()
        taken = next((h for h in history if h.get("publishAt") == slot), None)
        if taken:
            summary(f"Slot {slot} already has \"{taken['title']}\". Nothing to do.")
            return
        print(f"Slot {slot} is free.", flush=True)

    queue = next_topics(history)
    if not queue:
        summary("::warning::Topic queue is empty. Add topics to topics/queue.json.")
        sys.exit(1)
    if len(queue) <= 5:
        print(f"::warning::Only {len(queue)} topics left in topics/queue.json", flush=True)

    # try up to 3 topics in case Gemini cannot produce a valid episode for one
    last_err = None
    for topic in queue[:3]:
        try:
            ep_path = make_episode(topic, history)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"::warning::topic {topic['id']} failed: {str(e)[:300]}", flush=True)
    else:
        raise SystemExit(f"no episode could be generated: {last_err}")

    ep, video = produce(ep_path)
    entry = {"date": dt.date.today().isoformat(), "topic": topic["id"],
             "title": ep.get("upload", {}).get("title") or ep["title"],
             "episode": str(ep_path.relative_to(ROOT))}
    if mode == "no-upload":
        summary(f"Rendered {topic['id']} without uploading.")
        return
    from .upload import upload
    res = upload(str(video), ep, mode="public" if mode == "publish-now" else None)
    entry.update(res)
    history.append(entry)
    save_history(history)
    when = f"goes public {res['publishAt']}" if res.get("publishAt") else "uploaded"
    summary(f"**{entry['title']}** → https://youtube.com/shorts/{res['videoId']} ({when})")


if __name__ == "__main__":
    main()
