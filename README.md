![factorio-docs-skill banner](./banner.png)

# factorio-docs-skill

An **Agent Skill** that gives AI agents offline access to the full **Factorio Lua API** — 4,195 indexed entries (classes, methods, attributes, events, concepts, defines) with BM25-ranked search. No network needed at query time.

## How it works

```
runtime-api.json  →  SQLite FTS5 Index  →  BM25 Search  →  Agent Context
  (one-time fetch)     (build_index.py)    (search.py)
```

Queries hit the local `docs.db` (1.7 MB, pre-built). Network is only used when you explicitly run `update.py` to check for a new Factorio version.

## Usage

```bash
# Full-text search (BM25, porter stemming)
python scripts/search.py "inventory insert"

# Filter by kind: class · method · attribute · event · concept · define
python scripts/search.py --kind event "player built"
python scripts/search.py --kind method "fluid temperature"

# Exact name lookup (case-insensitive)
python scripts/search.py --exact LuaEntity
python scripts/search.py --exact on_built_entity

# All members of a class
python scripts/search.py --parent LuaEntity --kind method

# More results / full body
python scripts/search.py --top 30 --verbose "circuit network signal"
```

## Index maintenance

The pre-built index covers **Factorio 2.0.76 / API v6**. To update after a game version bump:

```bash
python scripts/update.py   # fetches only if remote version changed
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/search.py` | Query the local index (offline) |
| `scripts/build_index.py` | Build `references/docs.db` from JSON |
| `scripts/fetch_api.py` | Download `runtime-api.json` |
| `scripts/update.py` | Version-aware update (fetch + rebuild if changed) |

## Index stats

| Kind | Count |
|------|-------|
| classes + methods + attributes | 3,411 |
| events | 219 |
| concepts | 418 |
| defines | 147 |
| **total** | **4,195** |

## Tests

```bash
python -m pytest tests/test_e2e.py -v   # 31 E2E tests, ~1s
```
