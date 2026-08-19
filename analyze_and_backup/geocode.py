"""Reverse geocoding for GPS coordinates -- turns gps_latitude/gps_longitude
into a human place name (e.g. "Guelph, ON") for display in the web UI,
instead of just raw coordinates.

Uses OpenStreetMap's free Nominatim API (no key required) with a small
on-disk JSON cache keyed by rounded coordinates:
  - Nominatim's usage policy caps requests at ~1/second and expects a real
    User-Agent -- the cache means the same real-world place (home, work,
    a relative's house) only ever gets geocoded once on this device, ever,
    no matter how many photos or card inserts reuse it.
  - This is called once per photo at *pipeline* time (see pipeline.py),
    not on every web page load, so a slow/rate-limited lookup never blocks
    someone browsing the dashboard.

Coordinates are rounded to 4 decimal places (~11m) before lookup so photos
taken a few steps apart in the same place share a cache entry.

Fails soft everywhere: no network, no internet at all, or an API error just
means the caller gets None back and the UI falls back to showing raw
coordinates with a "view on map" link (already implemented in webapp.py).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_CACHE_LOCK = threading.Lock()
_MEMORY_CACHE: dict[str, str | None] = {}
_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_INTERVAL = 1.1  # seconds -- stays under Nominatim's ~1 req/s usage policy

_USER_AGENT = "VisionSortAI-analyze-and-backup/1.0 (Raspberry Pi status dashboard)"
_CACHE_FILENAME = "geocode_cache.json"


def _cache_key(lat: float, lng: float) -> str:
    return f"{round(lat, 4)},{round(lng, 4)}"


def _load_disk_cache(cache_dir: Path) -> dict:
    try:
        with open(cache_dir / _CACHE_FILENAME, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_disk_cache(cache_dir: Path, cache: dict) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_dir / _CACHE_FILENAME, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass  # e.g. read-only filesystem on some deploys -- memory cache still works this run


def _format_place(address: dict) -> str:
    """Picks a short "City, Region" label out of Nominatim's address
    breakdown, matching how the approved mockup showed locations (e.g.
    "Guelph, ON") rather than a long formatted address."""
    city = (
        address.get("city") or address.get("town") or address.get("village")
        or address.get("hamlet") or address.get("municipality") or address.get("county")
    )
    region = address.get("state") or address.get("province") or address.get("region")
    country_code = (address.get("country_code") or "").upper()

    if city and region:
        return f"{city}, {region}"
    if city and country_code:
        return f"{city}, {country_code}"
    if city:
        return city
    if region:
        return region
    return address.get("country") or "Unknown location"


def reverse_geocode(lat: float, lng: float, cache_dir: str | Path, timeout: float = 3.0) -> str | None:
    """Returns a short place label for (lat, lng), or None if it can't be
    resolved right now (no network, API error). Safe to call repeatedly --
    a cache hit (memory, then disk) returns instantly; only a genuinely new
    place makes a real HTTP call."""
    global _LAST_REQUEST_TIME

    cache_dir = Path(cache_dir)
    key = _cache_key(lat, lng)
    if key in _MEMORY_CACHE:
        return _MEMORY_CACHE[key]

    with _CACHE_LOCK:
        disk_cache = _load_disk_cache(cache_dir)
        if key in disk_cache:
            _MEMORY_CACHE[key] = disk_cache[key]
            return disk_cache[key]

        # Respect Nominatim's rate limit even across different coordinates.
        elapsed = time.time() - _LAST_REQUEST_TIME
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        label = None
        try:
            params = urllib.parse.urlencode({
                "format": "jsonv2", "lat": lat, "lon": lng, "zoom": 12, "addressdetails": 1,
            })
            req = urllib.request.Request(
                f"https://nominatim.openstreetmap.org/reverse?{params}",
                headers={"User-Agent": _USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            address = data.get("address") or {}
            if address:
                label = _format_place(address)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            label = None  # offline / API down -- caller falls back to raw coordinates
        finally:
            _LAST_REQUEST_TIME = time.time()

        _MEMORY_CACHE[key] = label
        disk_cache[key] = label
        _save_disk_cache(cache_dir, disk_cache)
        return label
