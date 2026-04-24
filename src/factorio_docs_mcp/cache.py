"""HTTP cache for Factorio docs.

Design:
- Each remote URL maps to a local file + a sidecar ``.meta.json`` storing
  the upstream ETag / Last-Modified / fetched_at timestamp.
- ``get(path)`` returns cached bytes. If the cached entry is younger than
  ``ttl``, it is returned immediately. Otherwise a conditional GET is
  issued (``If-None-Match`` / ``If-Modified-Since``); on 304 we just touch
  the sidecar; on 200 we overwrite.
- ``force=True`` forces an unconditional re-download.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://lua-api.factorio.com/latest/"
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


def default_cache_dir() -> Path:
    env = os.environ.get("FACTORIO_DOCS_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "factorio-docs-mcp"


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    meta_path: Path

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def read_meta(self) -> dict:
        if self.meta_path.exists():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {}


class DocsCache:
    """Fetch-and-cache layer keyed by relative path under ``base_url``."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        cache_dir: Optional[Path] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.cache_dir = (cache_dir or default_cache_dir()).resolve()
        self.ttl = ttl_seconds
        self.timeout = timeout
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client: Optional[httpx.Client] = None

    # ---- lifecycle ----------------------------------------------------
    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"user-agent": "factorio-docs-mcp/0.1"},
            )
        return self._client

    # ---- path mapping -------------------------------------------------
    def _entry(self, rel: str) -> CacheEntry:
        rel = rel.lstrip("/")
        local = (self.cache_dir / rel).resolve()
        # Guard against traversal via crafted rel paths.
        if not str(local).startswith(str(self.cache_dir)):
            raise ValueError(f"refusing path traversal: {rel!r}")
        local.parent.mkdir(parents=True, exist_ok=True)
        meta = local.with_suffix(local.suffix + ".meta.json")
        return CacheEntry(path=local, meta_path=meta)

    # ---- main API -----------------------------------------------------
    def get(self, rel_path: str, *, force: bool = False) -> CacheEntry:
        entry = self._entry(rel_path)
        meta = entry.read_meta()
        fresh = (
            not force
            and entry.path.exists()
            and (time.time() - meta.get("fetched_at", 0)) < self.ttl
        )
        if fresh:
            return entry

        url = self.base_url + rel_path.lstrip("/")
        headers: dict[str, str] = {}
        if not force and entry.path.exists():
            if etag := meta.get("etag"):
                headers["If-None-Match"] = etag
            if lm := meta.get("last_modified"):
                headers["If-Modified-Since"] = lm

        log.info("fetch %s (conditional=%s)", url, bool(headers))
        resp = self._http().get(url, headers=headers)

        if resp.status_code == 304 and entry.path.exists():
            meta["fetched_at"] = time.time()
            entry.meta_path.write_text(json.dumps(meta), encoding="utf-8")
            return entry

        resp.raise_for_status()
        entry.path.write_bytes(resp.content)
        new_meta = {
            "url": url,
            "fetched_at": time.time(),
            "etag": resp.headers.get("etag"),
            "last_modified": resp.headers.get("last-modified"),
            "content_type": resp.headers.get("content-type"),
            "content_length": len(resp.content),
        }
        entry.meta_path.write_text(json.dumps(new_meta), encoding="utf-8")
        return entry

    def get_json(self, rel_path: str, *, force: bool = False) -> dict:
        entry = self.get(rel_path, force=force)
        return json.loads(entry.read_bytes())

    def get_text(self, rel_path: str, *, force: bool = False) -> str:
        return self.get(rel_path, force=force).read_text()

    def info(self) -> dict:
        out: dict = {
            "base_url": self.base_url,
            "cache_dir": str(self.cache_dir),
            "ttl_seconds": self.ttl,
            "entries": [],
        }
        for meta_file in sorted(self.cache_dir.rglob("*.meta.json")):
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rel = meta_file.relative_to(self.cache_dir)
            out["entries"].append(
                {
                    "path": str(rel),
                    "url": m.get("url"),
                    "fetched_at": m.get("fetched_at"),
                    "age_seconds": time.time() - m.get("fetched_at", 0),
                    "content_length": m.get("content_length"),
                    "etag": m.get("etag"),
                }
            )
        return out
