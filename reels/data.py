"""Downloads and caches open map data (Natural Earth) and fonts (Google Fonts)."""
from __future__ import annotations

import shutil
import subprocess
import urllib.request
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"

NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
FONT_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/"


def fetch(url: str, name: str) -> Path:
    """Download url into cache/ once and return the local path."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / name
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception:
        # Fallback for environments where Python's SSL store differs from curl's.
        subprocess.run(["curl", "-sSfL", "-o", str(tmp), url], check=True)
    tmp.rename(dest)
    return dest


def countries_file(pov: str | None) -> Path:
    """Natural Earth 1:10m countries. pov='PAK' gives Pakistan's official point of view."""
    suffix = f"_{pov.lower()}" if pov else ""
    name = f"ne_10m_admin_0_countries{suffix}.geojson"
    return fetch(NE_BASE + name, name)


OUT = ROOT / "out"


def lakes_file() -> Path:
    name = "ne_10m_lakes.geojson"
    return fetch(NE_BASE + name, name)


def rivers_file() -> Path:
    name = "ne_10m_rivers_lake_centerlines.geojson"
    return fetch(NE_BASE + name, name)


@lru_cache(maxsize=None)
def font_file(weight: str = "ExtraBold") -> Path:
    name = f"Poppins-{weight}.ttf"
    return fetch(FONT_BASE + name, name)
