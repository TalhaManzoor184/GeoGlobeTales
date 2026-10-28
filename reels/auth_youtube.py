"""One-time: get YOUTUBE_REFRESH_TOKEN for the channel the reels go to.

Run on your own computer (it opens a browser):

    pip install google-auth-oauthlib google-api-python-client
    YOUTUBE_CLIENT_ID=... YOUTUBE_CLIENT_SECRET=... python -m reels.auth_youtube

Sign in with the Google account that owns the NEW geography channel and pick that channel
(not Sunny's) on the account chooser. Copy the printed token into the GitHub secret
YOUTUBE_REFRESH_TOKEN of this repo. The same "Desktop app" OAuth client as Sunny works.
"""
import os

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]


def main():
    cfg = {"installed": {"client_id": os.environ["YOUTUBE_CLIENT_ID"],
                         "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token",
                         "redirect_uris": ["http://127.0.0.1"]}}
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    me = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute()
    name = me["items"][0]["snippet"]["title"] if me.get("items") else "(no channel)"
    print(f"\nAuthorised channel: {name}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}\n")
    if name == "(no channel)":
        print("This account has no channel selected. Run again and pick the geography channel.")


if __name__ == "__main__":
    main()
