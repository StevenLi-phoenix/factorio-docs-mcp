#!/usr/bin/env python3
"""Query the local Factorio docs index (no network access required).

Usage:
    python scripts/search.py <query>              # full-text search
    python scripts/search.py --kind class <query> # filter by kind
    python scripts/search.py --exact LuaEntity    # exact name lookup
    python scripts/search.py --parent LuaEntity   # all members of a class

Kinds: class, method, attribute, event, concept, define

Output: formatted doc chunks ready to paste into agent context.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from textwrap import indent

DB_PATH = Path(__file__).parent.parent / "references" / "docs.db"
TOP_K = 15


def _open() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: index not found at {DB_PATH}", file=sys.stderr)
        print("Run: python scripts/build_index.py", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(row: sqlite3.Row, verbose: bool = False) -> str:
    sig = row["signature"]
    body = (row["body"] or "").strip()
    kind = row["kind"]
    header = f"[{kind}] {sig}"
    if not body:
        return header
    if verbose or len(body) <= 200:
        return f"{header}\n{indent(body, '  ')}"
    return f"{header}\n  {body[:200]}…"


def search_fts(conn: sqlite3.Connection, query: str, kind: str | None, top_k: int) -> list[sqlite3.Row]:
    # Escape special FTS5 chars to avoid syntax errors on raw user input
    safe = query.replace('"', '""').replace("*", "").replace("(", "").replace(")", "")
    fts_query = " OR ".join(f'"{t}"' for t in safe.split() if t)
    if not fts_query:
        return []
    if kind:
        sql = "SELECT * FROM docs WHERE docs MATCH ? AND kind = ? ORDER BY rank LIMIT ?"
        return conn.execute(sql, (fts_query, kind, top_k)).fetchall()
    sql = "SELECT * FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?"
    return conn.execute(sql, (fts_query, top_k)).fetchall()


def search_exact(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    sql = "SELECT * FROM docs WHERE name = ? COLLATE NOCASE"
    return conn.execute(sql, (name,)).fetchall()


def search_parent(conn: sqlite3.Connection, parent: str, kind: str | None) -> list[sqlite3.Row]:
    if kind:
        sql = "SELECT * FROM docs WHERE parent = ? AND kind = ? ORDER BY name"
        return conn.execute(sql, (parent, kind)).fetchall()
    sql = "SELECT * FROM docs WHERE parent = ? ORDER BY kind, name"
    return conn.execute(sql, (parent,)).fetchall()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    conn = _open()
    kind: str | None = None
    exact = False
    parent: str | None = None
    top_k = TOP_K
    verbose = False

    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--kind" and args:
            kind = args.pop(0)
        elif flag == "--exact":
            exact = True
        elif flag == "--parent" and args:
            parent = args.pop(0)
        elif flag == "--top" and args:
            top_k = int(args.pop(0))
        elif flag == "--verbose":
            verbose = True

    query = " ".join(args)

    if parent:
        rows = search_parent(conn, parent, kind)
    elif exact:
        rows = search_exact(conn, query)
    else:
        rows = search_fts(conn, query, kind, top_k)

    if not rows:
        print(f"No results for: {query!r}" + (f" [kind={kind}]" if kind else ""))
        return

    print(f"# {len(rows)} result(s) for {query!r}\n")
    for row in rows:
        print(_fmt(row, verbose=verbose))
        print()


if __name__ == "__main__":
    main()
