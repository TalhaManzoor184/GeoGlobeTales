"""Upload a finished reel to YouTube (Data API v3, OAuth refresh token).

Env: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
     UPLOAD_MODE   schedule (default: private now, public at PUBLISH_TIME) | private | public
     PUBLISH_TIME  "21:00"   PUBLISH_UTC_OFFSET  5   (Pakistan time)
"""
from __future__ import annotations

import datetime as dt
import os

FOOTER = "\n\nA new map secret every day. Maps: Natural Earth.\n"


def publish_at_iso(now: dt.datetime | None = None) -> str:
    """Next PUBLISH_TIME local slot at least 15 minutes away, as RFC3339 UTC."""
    now = now or dt.datetime.now(dt.timezone.utc)
    hh, mm = map(int, os.environ.get("PUBLISH_TIME", "21:00").split(":"))
    off = dt.timedelta(hours=float(os.environ.get("PUBLISH_UTC_OFFSET", "5")))
    local = now + off
    slot = dt.datetime(local.year, local.month, local.day, hh, mm, tzinfo=dt.timezone.utc) - off
    while slot < now + dt.timedelta(minutes=15):
        slot += dt.timedelta(days=1)
    return slot.strftime("%Y-%m-%dT%H:%M:%SZ")


def secret(name: str) -> str:
    """Secret value with the usual copy-paste mistakes removed (spaces, quotes, NAME= prefix)."""
    v = os.environ.get(name, "").strip().strip('"').strip("'").strip()
    if v.startswith(name + "="):
        v = v[len(name) + 1:].strip().strip('"').strip("'")
    return v


def diagnose() -> str:
    """Shape of the secrets, without revealing them."""
    tok, cid, cs = secret("YOUTUBE_REFRESH_TOKEN"), secret("YOUTUBE_CLIENT_ID"), secret("YOUTUBE_CLIENT_SECRET")
    notes = [f"refresh token: {len(tok)} chars, {'starts with 1//' if tok.startswith('1//') else 'does NOT start with 1// (not a Google refresh token)'}",
             f"client id ends with ...{cid[-28:-27] if len(cid) > 28 else ''}{'.apps.googleusercontent.com OK' if cid.endswith('.apps.googleusercontent.com') else ' — not an OAuth client id'}",
             f"client id project number {cid.split('-')[0] if '-' in cid else '?'}",
             f"client secret {'starts with GOCSPX-' if cs.startswith('GOCSPX-') else 'has an unusual format'}"]
    return "; ".join(notes)


def youtube():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=secret("YOUTUBE_REFRESH_TOKEN"),
                        client_id=secret("YOUTUBE_CLIENT_ID"),
                        client_secret=secret("YOUTUBE_CLIENT_SECRET"),
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(video: str, ep: dict, mode: str | None = None) -> dict:
    from googleapiclient.http import MediaFileUpload
    mode = mode or os.environ.get("UPLOAD_MODE", "schedule")
    meta = ep.get("upload", {})
    tags = [h.lstrip("#") for h in meta.get("hashtags", [])]
    desc = meta.get("description", "") + FOOTER + " ".join(meta.get("hashtags", ["#shorts"]))
    status = {"privacyStatus": "public" if mode == "public" else "private",
              "selfDeclaredMadeForKids": False, "embeddable": True}
    publish_at = None
    if mode == "schedule":
        publish_at = publish_at_iso()
        status["publishAt"] = publish_at
    body = {
        "snippet": {"title": (meta.get("title") or ep["title"])[:100], "description": desc[:4900],
                    "tags": tags[:15], "categoryId": "27",  # Education
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": status,
    }
    req = youtube().videos().insert(part="snippet,status", body=body, notifySubscribers=True,
                                    media_body=MediaFileUpload(video, mimetype="video/mp4",
                                                               chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    print(f"uploaded https://youtube.com/shorts/{vid} ({mode}{', public at ' + publish_at if publish_at else ''})")
    return {"videoId": vid, "publishAt": publish_at}
