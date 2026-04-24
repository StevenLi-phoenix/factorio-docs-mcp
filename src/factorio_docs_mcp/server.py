"""MCP stdio server exposing the Factorio docs grep engine."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from factorio_docs_mcp.cache import DEFAULT_BASE_URL, DocsCache
from factorio_docs_mcp.index import KINDS, DocsIndex, Record

log = logging.getLogger("factorio_docs_mcp")

# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------

def _make_index() -> DocsIndex:
    base_url = os.environ.get("FACTORIO_DOCS_BASE_URL", DEFAULT_BASE_URL)
    ttl = int(os.environ.get("FACTORIO_DOCS_TTL_SECONDS", 24 * 60 * 60))
    cache = DocsCache(base_url=base_url, ttl_seconds=ttl)
    return DocsIndex(cache=cache)


INDEX = _make_index()
mcp = FastMCP(
    "factorio-docs",
    instructions=(
        "Grep engine over the official Factorio Lua API documentation "
        "(https://lua-api.factorio.com/latest/). Use `search` with a regex "
        "pattern to find classes, events, concepts, defines, prototypes, or "
        "members; use `get` to retrieve a full entry by its fully-qualified "
        "name (e.g. 'LuaSurface.create_entity', 'on_tick', "
        "'defines.alert_type'). Use `auxiliary` to fetch auxiliary pages "
        "(e.g. 'libraries', 'data-lifecycle'). Docs are cached locally and "
        "refreshed with ETag-conditional GETs; call `refresh` to force."
    ),
)


# --------------------------------------------------------------------------
# Render helpers
# --------------------------------------------------------------------------

def _summary(r: Record) -> dict:
    return {
        "kind": r.kind,
        "name": r.name,
        "stage": r.stage,
        "parent": r.parent,
        "signature": r.signature,
        "description": r.description,
        "url": r.url,
    }


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

@mcp.tool()
def search(
    pattern: Annotated[
        str,
        Field(description="Python regex (re.search). Case-insensitive by default."),
    ],
    kinds: Annotated[
        Optional[list[str]],
        Field(
            default=None,
            description=(
                "Filter by record kind. Any of: "
                "class, event, concept, define, method, attribute, operator, "
                "global_function, global_object, prototype, type, property, auxiliary."
            ),
        ),
    ] = None,
    stages: Annotated[
        Optional[list[str]],
        Field(
            default=None,
            description="Filter by stage: runtime, prototype, auxiliary.",
        ),
    ] = None,
    field: Annotated[
        str,
        Field(
            default="any",
            description='Which field the regex is matched against: "name", "description", "signature", or "any" (default).',
        ),
    ] = "any",
    case_sensitive: Annotated[
        bool,
        Field(default=False, description="Regex case sensitivity (default False)."),
    ] = False,
    limit: Annotated[
        int,
        Field(default=50, ge=1, le=500, description="Max results (1-500)."),
    ] = 50,
) -> str:
    """Grep the flattened Factorio docs index for a regex pattern."""
    if kinds:
        bad = [k for k in kinds if k not in KINDS]
        if bad:
            return _json({"error": f"unknown kinds: {bad}", "allowed": list(KINDS)})
    hits = INDEX.search(
        pattern,
        kinds=kinds,
        stages=stages,
        field=field,
        case_sensitive=case_sensitive,
        limit=limit,
    )
    return _json(
        {
            "pattern": pattern,
            "count": len(hits),
            "results": [_summary(h) for h in hits],
        }
    )


@mcp.tool()
def get(
    name: Annotated[
        str,
        Field(
            description=(
                "Fully-qualified name. Examples: 'LuaSurface', "
                "'LuaSurface.create_entity', 'on_tick', 'defines.alert_type', "
                "'defines.alert_type.entity_destroyed', 'ContainerPrototype', "
                "'BoundingBox'."
            )
        ),
    ],
) -> str:
    """Return the full JSON record for a named entry, including upstream URL."""
    rec = INDEX.get(name)
    if rec is None:
        return _json({"error": f"not found: {name!r}"})
    return _json(INDEX.render(rec, full=True))


@mcp.tool()
def list_entries(
    kind: Annotated[
        Optional[str],
        Field(default=None, description="Filter by kind (see `search` for values)."),
    ] = None,
    stage: Annotated[
        Optional[str],
        Field(default=None, description="runtime | prototype | auxiliary"),
    ] = None,
    pattern: Annotated[
        Optional[str],
        Field(default=None, description="Regex filter on the fully-qualified name."),
    ] = None,
    limit: Annotated[
        int,
        Field(default=500, ge=1, le=5000),
    ] = 500,
) -> str:
    """List fully-qualified names in the index, optionally filtered."""
    names = INDEX.list_names(kind=kind, stage=stage, pattern=pattern, limit=limit)
    return _json({"count": len(names), "names": names})


@mcp.tool()
def auxiliary(
    page: Annotated[
        str,
        Field(
            description=(
                "Auxiliary page slug (e.g. 'libraries', 'data-lifecycle'). "
                "Also accepts full relative path like 'auxiliary/libraries.html' "
                "or a bare filename."
            )
        ),
    ],
    max_chars: Annotated[
        int,
        Field(default=20_000, ge=200, le=200_000, description="Truncate output to this many chars."),
    ] = 20_000,
) -> str:
    """Return the extracted text of an auxiliary documentation page."""
    slug = page.strip()
    if slug.startswith("auxiliary/"):
        target = slug
    elif slug.endswith(".html"):
        target = f"auxiliary/{slug}"
    else:
        target = f"auxiliary/{slug}.html"

    short = target.rsplit("/", 1)[-1].removesuffix(".html")
    rec = INDEX.get(short)
    if rec is not None and rec.kind == "auxiliary":
        text = rec.raw.get("text", "") if isinstance(rec.raw, dict) else ""
        truncated = len(text) > max_chars
        return _json(
            {
                "page": short,
                "url": rec.url,
                "truncated": truncated,
                "text": text[:max_chars],
            }
        )
    # Fall back to a live fetch + extraction — handles pages we haven't
    # pre-discovered.
    try:
        from factorio_docs_mcp.html_extract import html_to_text

        html = INDEX.cache.get_text(target)
        text = html_to_text(html)
        truncated = len(text) > max_chars
        return _json(
            {
                "page": short,
                "url": INDEX.cache.base_url + target,
                "truncated": truncated,
                "text": text[:max_chars],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc), "page": page})


@mcp.tool()
def refresh() -> str:
    """Force re-download of all docs (bypassing the TTL / conditional-GET path)."""
    return _json(INDEX.refresh())


@mcp.tool()
def stats() -> str:
    """Return index counts, upstream version, and cache metadata."""
    return _json(INDEX.stats())


@mcp.tool()
def cache_info() -> str:
    """Return per-file cache metadata (URL, fetched_at, ETag, size)."""
    return _json(INDEX.cache.info())


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

def main() -> None:
    level = os.environ.get("FACTORIO_DOCS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log.info("factorio-docs-mcp starting; base_url=%s", INDEX.cache.base_url)
    mcp.run()


if __name__ == "__main__":
    main()
