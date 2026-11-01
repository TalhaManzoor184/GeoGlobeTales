"""One-time: get FACEBOOK_PAGE_ID and a never-expiring FACEBOOK_PAGE_TOKEN.

1. In Graph API Explorer (developers.facebook.com/tools/explorer) pick your Meta app (Sunny's
   app works), "Get User Access Token" with pages_show_list, pages_read_engagement and
   pages_manage_posts, and approve it for the geography Page. Copy the token.
2. On your computer:
       FACEBOOK_APP_ID=... FACEBOOK_APP_SECRET=... python -m reels.auth_facebook
   and paste the token. Pick the geography Page.
3. Add the printed FACEBOOK_PAGE_ID and FACEBOOK_PAGE_TOKEN as secrets of this repo.
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

V = os.environ.get("FACEBOOK_GRAPH_VERSION") or "v26.0"


def get(path: str, params: dict) -> dict:
    url = f"https://graph.facebook.com/{V}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path}: {json.loads(e.read()).get('error', {}).get('message', e.code)}")


def main():
    app_id, secret = os.environ["FACEBOOK_APP_ID"], os.environ["FACEBOOK_APP_SECRET"]
    short = input("Paste the user access token from Graph API Explorer: ").strip()
    # short-lived user token -> long-lived user token; Page tokens made from it never expire
    long = get("oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                      "client_secret": secret, "fb_exchange_token": short})["access_token"]
    pages = get("me/accounts", {"access_token": long, "fields": "id,name,access_token,tasks"})["data"]
    if not pages:
        raise SystemExit("No Pages found. Select the Page when approving the token.")
    for i, p in enumerate(pages, 1):
        print(f"  {i}. {p['name']} ({p['id']})")
    n = 1 if len(pages) == 1 else int(input("Which Page is the geography Page? Number: "))
    page = pages[n - 1]
    if "CREATE_CONTENT" not in page.get("tasks", ["CREATE_CONTENT"]):
        print("Warning: your role on this Page cannot create content.")
    dbg = get("debug_token", {"input_token": page["access_token"], "access_token": f"{app_id}|{secret}"})["data"]
    missing = [s for s in ("pages_manage_posts", "pages_read_engagement", "pages_show_list")
               if s not in dbg.get("scopes", [])]
    if missing:
        print(f"Warning: token is missing {', '.join(missing)}")
    exp = dbg.get("expires_at")
    print(f"\nPage: {page['name']}")
    print(f"Expires: {dt.datetime.fromtimestamp(exp, dt.timezone.utc).isoformat() if exp else 'never'}")
    print(f"\nFACEBOOK_PAGE_ID={page['id']}\nFACEBOOK_PAGE_TOKEN={page['access_token']}\n")
    print("Add both as GitHub secrets of the GeoGlobeTales repo. Keep the token private.")


if __name__ == "__main__":
    main()
