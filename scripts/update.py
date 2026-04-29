#!/usr/bin/env python3
"""Check for Factorio API updates and rebuild the local index if needed.

Run this explicitly after a Factorio version update:
    python scripts/update.py

Exits 0 in all cases. Prints what changed (or nothing changed).
Network is only used here — search.py never calls out.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
API_URL = "https://lua-api.factorio.com/latest/runtime-api.json"
API_JSON = ROOT / "references" / "runtime-api.json"
DB_PATH = ROOT / "references" / "docs.db"


def _cached_version() -> str | None:
    if not API_JSON.exists():
        return None
    try:
        return json.loads(API_JSON.read_text()).get("application_version")
    except Exception:
        return None


def _fetch() -> dict:
    print(f"Fetching {API_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(API_URL, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _rebuild() -> None:
    # Import inline to avoid circular dependency
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("build_index", Path(__file__).parent / "build_index.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.build(force=True)


def main() -> None:
    cached = _cached_version()

    data = _fetch()
    remote_ver = data.get("application_version", "unknown")

    if cached == remote_ver:
        print(f"Already up to date (Factorio {remote_ver})")
        return

    if cached:
        print(f"Factorio {cached} → {remote_ver}")
    else:
        print(f"Initializing index for Factorio {remote_ver}")

    API_JSON.parent.mkdir(parents=True, exist_ok=True)
    API_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Saved {API_JSON.name}")

    _rebuild()


if __name__ == "__main__":
    main()
