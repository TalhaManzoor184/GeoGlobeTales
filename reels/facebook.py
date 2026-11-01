"""Upload a finished reel to a Facebook Page as a Reel (Graph API "video_reels").

Env: FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN (never-expiring Page token, see reels.auth_facebook)
     FACEBOOK_ENABLED      true (default) | false
     FACEBOOK_GRAPH_VERSION v26.0
The Meta app must be in Live mode, otherwise Reels are only visible to people with a role on the app.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/video-upload"


def version() -> str:
    return os.environ.get("FACEBOOK_GRAPH_VERSION") or "v26.0"


def _clean(name: str) -> str:
    v = os.environ.get(name, "").strip().strip('"').strip("'").strip()
    if v.startswith(name + "="):
        v = v[len(name) + 1:].strip().strip('"').strip("'")
    return v


def configured() -> bool:
    if os.environ.get("FACEBOOK_ENABLED", "true").lower() == "false":
        return False
    return bool(_clean("FACEBOOK_PAGE_ID") and _clean("FACEBOOK_PAGE_TOKEN"))


def _graph(method: str, path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{GRAPH}/{version()}/{path}"
    data = None
    if method == "GET":
        url += "?" + qs
    else:
        data = qs.encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error", {})
        except Exception:  # noqa: BLE001
            err = {}
        raise RuntimeError(f"Facebook {path.split('?')[0]} failed: {err.get('code', e.code)} "
                           f"{err.get('message', '')}".strip()) from None


def page_name() -> str:
    """Cheap login check: the Page's name, or an exception explaining what is wrong."""
    return _graph("GET", _clean("FACEBOOK_PAGE_ID"),
                  {"fields": "name", "access_token": _clean("FACEBOOK_PAGE_TOKEN")}).get("name", "?")


def caption(ep: dict) -> str:
    meta = ep.get("upload", {})
    tags = [t for t in meta.get("hashtags", []) if t.lower() != "#shorts"]
    tags = ["#reels"] + [t for t in tags if t.lower() != "#reels"]
    title = meta.get("title") or ep["title"]
    return f"{title}\n\n{meta.get('description', '')}\n\n{' '.join(tags)}".strip()


def upload_reel(video: str, ep: dict, publish_at: str | None = None, draft: bool = False) -> dict:
    """Upload + finish. With publish_at (RFC3339, >10 min ahead) the Reel is scheduled,
    with draft=True it is saved unpublished, otherwise it goes live at once."""
    page, token = _clean("FACEBOOK_PAGE_ID"), _clean("FACEBOOK_PAGE_TOKEN")

    # 1. start an upload session
    vid = _graph("POST", f"{page}/video_reels", {"upload_phase": "start", "access_token": token})["video_id"]

    # 2. send the file in one request
    with open(video, "rb") as f:
        data = f.read()
    req = urllib.request.Request(f"{RUPLOAD}/{version()}/{vid}", data=data, method="POST", headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data)),
        "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            up = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Facebook file upload failed: {e.code} {e.read()[:300]!r}") from None
    if not up.get("success"):
        raise RuntimeError(f"Facebook file upload failed: {up}")

    # 3. publish / schedule / draft (scheduling needs 10 min – 29 days ahead)
    when = 0
    if publish_at:
        when = int(dt.datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=dt.timezone.utc).timestamp())
    schedule = not draft and when > time.time() + 11 * 60
    meta = ep.get("upload", {})
    params = {"access_token": token, "video_id": vid, "upload_phase": "finish",
              "video_state": "DRAFT" if draft else "SCHEDULED" if schedule else "PUBLISHED",
              "description": caption(ep), "title": (meta.get("title") or ep["title"])[:250],
              "is_ai_generated": "true"}  # script and voice are AI-generated
    if schedule:
        params["scheduled_publish_time"] = str(when)
    _graph("POST", f"{page}/video_reels", params)
    state = "saved as draft" if draft else f"scheduled for {publish_at}" if schedule else "published"

    # 4. wait for processing so a broken video is reported, not silently lost
    for _ in range(30):
        time.sleep(10)
        st = _graph("GET", vid, {"fields": "status", "access_token": token}).get("status", {})
        phases = [st.get("video_status"), st.get("processing_phase", {}).get("status"),
                  st.get("publishing_phase", {}).get("status")]
        if "error" in phases:
            msg = st.get("processing_phase", {}).get("error", {}).get("message") or st.get("video_status")
            raise RuntimeError(f"Facebook processing failed: {msg}")
        pub = st.get("publishing_phase", {}).get("publish_status")
        if st.get("video_status") == "ready" or pub in ("published", "scheduled", "draft"):
            break
    else:
        print("Facebook is still processing the Reel; it should appear shortly.", flush=True)
    print(f"Facebook Reel {vid} {state}", flush=True)
    return {"facebookVideoId": vid, "facebookState": state}
