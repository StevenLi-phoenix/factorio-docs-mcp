#!/usr/bin/env python3
"""Build a local SQLite FTS5 index from the cached Factorio runtime-api.json.

Run once after fetching the API JSON:
    python scripts/fetch_api.py
    python scripts/build_index.py

The resulting docs.db is queried at runtime by search.py with no network access.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
API_JSON = ROOT / "references" / "runtime-api.json"
DB_PATH = ROOT / "references" / "docs.db"

DDL = """
CREATE VIRTUAL TABLE docs USING fts5(
    doc_id    UNINDEXED,
    kind,
    parent,
    name,
    signature,
    body,
    tokenize = 'porter ascii'
);
"""


def _type_str(t: Any) -> str:
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        kind = t.get("complex_type") or t.get("type", "")
        if kind == "array":
            return f"array[{_type_str(t.get('value'))}]"
        if kind == "dictionary":
            return f"dict[{_type_str(t.get('key'))}, {_type_str(t.get('value'))}]"
        if kind == "union":
            options = " | ".join(_type_str(o) for o in t.get("options", []))
            return options
        if kind == "literal":
            return repr(t.get("value"))
        if kind == "LuaCustomTable":
            return f"LuaCustomTable[{_type_str(t.get('key'))}, {_type_str(t.get('value'))}]"
        return kind
    return str(t)


def _params(params: list[dict]) -> str:
    parts = []
    for p in params:
        opt = "?" if p.get("optional") else ""
        parts.append(f"{p['name']}{opt}: {_type_str(p.get('type'))}")
    return ", ".join(parts)


def _method_sig(cname: str, mname: str, params: list[dict], rvs: list[dict], fmt: dict) -> str:
    parts = []
    for p in params:
        opt = "?" if p.get("optional") else ""
        parts.append(f"{p['name']}{opt}: {_type_str(p.get('type'))}")

    if fmt.get("takes_table"):
        inner = ", ".join(parts)
        table_opt = "?" if fmt.get("table_optional") else ""
        params_str = f"{{{inner}}}{table_opt}"
    else:
        params_str = ", ".join(parts)

    ret_parts = []
    for r in rvs:
        t = _type_str(r.get("type"))
        if r.get("optional"):
            t += "?"
        ret_parts.append(t)
    ret_str = (" -> " + ", ".join(ret_parts)) if ret_parts else ""

    return f"{cname}:{mname}({params_str}){ret_str}"


def _returns(rvs: list[dict]) -> str:
    if not rvs:
        return ""
    parts = [_type_str(r.get("type")) for r in rvs]
    return " -> " + ", ".join(parts)


def index_classes(cur: sqlite3.Cursor, classes: list[dict]) -> int:
    count = 0
    for cls in classes:
        cname = cls["name"]
        # class-level entry
        cur.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?)",
            (
                f"class:{cname}",
                "class",
                "",
                cname,
                cname,
                cls.get("description", ""),
            ),
        )
        count += 1

        for attr in cls.get("attributes", []):
            aname = attr["name"]
            rtype = _type_str(attr.get("read_type") or attr.get("write_type"))
            opt = "?" if attr.get("optional") else ""
            sig = f"{cname}.{aname}{opt}: {rtype}"
            cur.execute(
                "INSERT INTO docs VALUES (?,?,?,?,?,?)",
                (
                    f"attr:{cname}.{aname}",
                    "attribute",
                    cname,
                    aname,
                    sig,
                    attr.get("description", ""),
                ),
            )
            count += 1

        for meth in cls.get("methods", []):
            mname = meth["name"]
            sig = _method_sig(
                cname, mname,
                meth.get("parameters", []),
                meth.get("return_values", []),
                meth.get("format", {}),
            )
            param_docs = " | ".join(
                f"{p['name']}: {p.get('description', '')}"
                for p in meth.get("parameters", [])
            )
            body = "\n".join(filter(None, [meth.get("description", ""), param_docs]))
            cur.execute(
                "INSERT INTO docs VALUES (?,?,?,?,?,?)",
                (
                    f"method:{cname}.{mname}",
                    "method",
                    cname,
                    mname,
                    sig,
                    body,
                ),
            )
            count += 1

    return count


def index_events(cur: sqlite3.Cursor, events: list[dict]) -> int:
    count = 0
    for ev in events:
        ename = ev["name"]
        field_docs = " | ".join(
            f"{f['name']} ({_type_str(f.get('type'))}): {f.get('description', '')}"
            for f in ev.get("data", [])
        )
        body = "\n".join(filter(None, [ev.get("description", ""), field_docs]))
        cur.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?)",
            (
                f"event:{ename}",
                "event",
                "",
                ename,
                f"defines.events.{ename}",
                body,
            ),
        )
        count += 1
    return count


def index_concepts(cur: sqlite3.Cursor, concepts: list[dict]) -> int:
    count = 0
    for c in concepts:
        cname = c["name"]
        cur.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?)",
            (
                f"concept:{cname}",
                "concept",
                "",
                cname,
                cname,
                c.get("description", ""),
            ),
        )
        count += 1
    return count


def index_defines(cur: sqlite3.Cursor, defines: list[dict], prefix: str = "defines") -> int:
    count = 0
    for d in defines:
        dname = f"{prefix}.{d['name']}"
        values_str = ", ".join(v["name"] for v in d.get("values", []))
        body = "\n".join(filter(None, [d.get("description", ""), values_str]))
        cur.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?)",
            (
                f"define:{dname}",
                "define",
                prefix,
                d["name"],
                dname,
                body,
            ),
        )
        count += 1
        # recurse into sub-defines
        if d.get("subkeys"):
            count += index_defines(cur, d["subkeys"], prefix=dname)
    return count


def build(force: bool = False) -> None:
    if not API_JSON.exists():
        print(f"ERROR: {API_JSON} not found. Run: python scripts/fetch_api.py", file=sys.stderr)
        sys.exit(1)

    if DB_PATH.exists():
        if not force:
            print(f"Index already exists at {DB_PATH}. Use --force to rebuild.", file=sys.stderr)
            return
        DB_PATH.unlink()

    data = json.loads(API_JSON.read_text())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(DDL)

    n_classes = index_classes(cur, data.get("classes", []))
    n_events = index_events(cur, data.get("events", []))
    n_concepts = index_concepts(cur, data.get("concepts", []))
    n_defines = index_defines(cur, data.get("defines", []))

    conn.commit()
    conn.close()

    total = n_classes + n_events + n_concepts + n_defines
    print(f"Built {DB_PATH.name}: {total} docs indexed")
    print(f"  classes/methods/attrs: {n_classes}")
    print(f"  events:   {n_events}")
    print(f"  concepts: {n_concepts}")
    print(f"  defines:  {n_defines}")
    print(f"  api_version: {data.get('api_version')}, game: {data.get('application_version')}")


if __name__ == "__main__":
    build(force="--force" in sys.argv)
