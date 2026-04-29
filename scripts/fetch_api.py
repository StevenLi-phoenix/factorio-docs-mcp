#!/usr/bin/env python3
"""Fetch and cache the Factorio runtime API JSON."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

API_URL = "https://lua-api.factorio.com/latest/runtime-api.json"
CACHE_PATH = Path(__file__).parent.parent / "references" / "runtime-api.json"


def fetch(force: bool = False) -> dict:
    if CACHE_PATH.exists() and not force:
        print(f"Using cached API at {CACHE_PATH}", file=sys.stderr)
        return json.loads(CACHE_PATH.read_text())

    print(f"Fetching {API_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(API_URL, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Saved to {CACHE_PATH}", file=sys.stderr)
    return data


if __name__ == "__main__":
    force = "--force" in sys.argv
    data = fetch(force=force)
    print(f"API version: {data.get('api_version')}")
    print(f"Classes: {len(data.get('classes', []))}")
    print(f"Events: {len(data.get('events', []))}")
    print(f"Defines: {len(data.get('defines', []))}")
