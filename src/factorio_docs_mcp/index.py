"""Flat searchable index over the Factorio runtime + prototype JSON docs.

The JSON files (~1.8 MB each) load quickly. We walk them once on first
use and build a list of :class:`Record` objects with pre-computed
``search_blob`` strings for fast regex grep.

URL shapes (verified against lua-api.factorio.com/latest/):
    runtime class            classes/<Name>.html
    runtime class member     classes/<Name>.html#<Name>.<member>
    runtime event            events.html#<event_name>
    runtime concept          concepts/<Name>.html
    define                   defines.html#defines.<dotted.path>
    prototype                prototypes/<Name>.html
    prototype type           types/<Name>.html
    prototype property       prototypes/<Name>.html#<property>
    auxiliary page           auxiliary/<page>.html (or <page>.html at root)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from factorio_docs_mcp.cache import DEFAULT_BASE_URL, DocsCache
from factorio_docs_mcp.html_extract import extract_links, html_to_text

log = logging.getLogger(__name__)

# All kinds we emit. 'class' is a Python keyword so we use 'klass' internally
# when needed; the string label stays "class".
KINDS: tuple[str, ...] = (
    "class",
    "event",
    "concept",
    "define",
    "method",
    "attribute",
    "operator",
    "global_function",
    "global_object",
    "prototype",
    "type",
    "property",
    "auxiliary",
)

# Auxiliary pages we always try to fetch. The list is expanded at runtime
# by scraping index-auxiliary.html, but this seed keeps things working
# even if that fetch fails.
SEED_AUX_PATHS: tuple[str, ...] = (
    "auxiliary/libraries.html",
    "auxiliary/data-lifecycle.html",
    "auxiliary/storage.html",
    "auxiliary/mod-structure.html",
    "auxiliary/migrations.html",
    "auxiliary/prototype-tree.html",
    "auxiliary/noise-expressions.html",
    "auxiliary/instrument.html",
    "auxiliary/json-docs-runtime.html",
    "auxiliary/json-docs-prototype.html",
)


@dataclass(slots=True)
class Record:
    kind: str
    name: str            # fully qualified, e.g. "LuaSurface.create_entity"
    short_name: str      # e.g. "create_entity"
    parent: Optional[str]
    stage: str           # "runtime" | "prototype" | "auxiliary"
    description: str
    signature: str       # for methods/events: call or field signature
    url: str
    search_blob: str = field(repr=False)
    # Kept out of search_blob but returned on detail lookup.
    raw: Any = field(default=None, repr=False)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def _compact(s: Optional[str]) -> str:
    if not s:
        return ""
    # Single-line version for signatures/descriptions.
    return re.sub(r"\s+", " ", s).strip()


def _first_para(s: Optional[str]) -> str:
    if not s:
        return ""
    # Take up to the first blank line.
    return _compact(s.split("\n\n", 1)[0])


def _param_sig(param: dict) -> str:
    name = param.get("name") or "?"
    t = _type_str(param.get("type"))
    opt = "?" if param.get("optional") else ""
    return f"{name}{opt}: {t}"


def _type_str(t: Any) -> str:
    """Render the nested type objects the JSON uses as a short string."""
    if t is None:
        return "nil"
    if isinstance(t, str):
        return t
    if not isinstance(t, dict):
        return str(t)
    cx = t.get("complex_type")
    if cx == "array":
        return f"array[{_type_str(t.get('value'))}]"
    if cx == "dictionary":
        return f"dict[{_type_str(t.get('key'))} -> {_type_str(t.get('value'))}]"
    if cx == "tuple":
        return "tuple[" + ", ".join(_type_str(v) for v in (t.get("values") or [])) + "]"
    if cx == "union":
        return " | ".join(_type_str(v) for v in (t.get("options") or []))
    if cx == "literal":
        v = t.get("value")
        return repr(v)
    if cx == "type":
        return _type_str(t.get("value"))
    if cx == "LuaLazyLoadedValue":
        return f"LuaLazyLoadedValue[{_type_str(t.get('value'))}]"
    if cx == "LuaCustomTable":
        return f"LuaCustomTable[{_type_str(t.get('key'))} -> {_type_str(t.get('value'))}]"
    if cx == "function":
        params = ", ".join(_type_str(p) for p in (t.get("parameters") or []))
        return f"fun({params})"
    if cx == "struct":
        return "struct"
    if cx == "table":
        return "table"
    return t.get("name") or cx or "<type>"


def _method_signature(m: dict) -> str:
    params = m.get("parameters") or []
    fmt = [_param_sig(p) for p in params]
    if m.get("variadic_parameter"):
        fmt.append(f"...: {_type_str(m['variadic_parameter'].get('type'))}")
    ret = ""
    rv = m.get("return_values") or []
    if rv:
        rs = ", ".join(_type_str(r.get("type")) for r in rv)
        ret = f" -> {rs}"
    return f"({', '.join(fmt)}){ret}"


def _attr_signature(a: dict) -> str:
    rw = []
    if a.get("read"):
        rw.append("R")
    if a.get("write"):
        rw.append("W")
    return f": {_type_str(a.get('type'))} [{''.join(rw) or '-'}]"


# --------------------------------------------------------------------------

def _build_runtime_records(doc: dict, base_url: str) -> list[Record]:
    out: list[Record] = []

    # Classes + their methods / attributes / operators.
    for c in doc.get("classes", []):
        cname = c["name"]
        url = f"{base_url}classes/{cname}.html"
        desc = _first_para(c.get("description"))
        blob_parts = [cname, desc]
        for m in c.get("methods", []):
            blob_parts.append(m["name"])
            blob_parts.append(_first_para(m.get("description")))
        for a in c.get("attributes", []):
            blob_parts.append(a["name"])
            blob_parts.append(_first_para(a.get("description")))
        out.append(
            Record(
                kind="class",
                name=cname,
                short_name=cname,
                parent=c.get("parent"),
                stage="runtime",
                description=desc,
                signature=f"class {cname}" + (f" extends {c['parent']}" if c.get("parent") else ""),
                url=url,
                search_blob=" ".join(blob_parts).lower(),
                raw=c,
            )
        )

        for m in c.get("methods", []):
            mname = m["name"]
            fq = f"{cname}.{mname}"
            sig = f"{fq}{_method_signature(m)}"
            mdesc = _first_para(m.get("description"))
            out.append(
                Record(
                    kind="method",
                    name=fq,
                    short_name=mname,
                    parent=cname,
                    stage="runtime",
                    description=mdesc,
                    signature=sig,
                    url=f"{base_url}classes/{cname}.html#{fq}",
                    search_blob=f"{fq} {sig} {mdesc}".lower(),
                    raw=m,
                )
            )
        for a in c.get("attributes", []):
            aname = a["name"]
            fq = f"{cname}.{aname}"
            sig = f"{fq}{_attr_signature(a)}"
            adesc = _first_para(a.get("description"))
            out.append(
                Record(
                    kind="attribute",
                    name=fq,
                    short_name=aname,
                    parent=cname,
                    stage="runtime",
                    description=adesc,
                    signature=sig,
                    url=f"{base_url}classes/{cname}.html#{fq}",
                    search_blob=f"{fq} {sig} {adesc}".lower(),
                    raw=a,
                )
            )
        for op in c.get("operators", []):
            oname = op.get("name", "?")
            fq = f"{cname}.{oname}"
            odesc = _first_para(op.get("description"))
            out.append(
                Record(
                    kind="operator",
                    name=fq,
                    short_name=oname,
                    parent=cname,
                    stage="runtime",
                    description=odesc,
                    signature=fq,
                    url=f"{base_url}classes/{cname}.html#{fq}",
                    search_blob=f"{fq} {odesc}".lower(),
                    raw=op,
                )
            )

    # Events.
    for e in doc.get("events", []):
        ename = e["name"]
        edesc = _first_para(e.get("description"))
        data_sig = ", ".join(_param_sig(p) for p in (e.get("data") or []))
        sig = f"event {ename}({data_sig})"
        out.append(
            Record(
                kind="event",
                name=ename,
                short_name=ename,
                parent=None,
                stage="runtime",
                description=edesc,
                signature=sig,
                url=f"{base_url}events.html#{ename}",
                search_blob=f"{ename} {sig} {edesc}".lower(),
                raw=e,
            )
        )

    # Concepts.
    for c in doc.get("concepts", []):
        cname = c["name"]
        desc = _first_para(c.get("description"))
        out.append(
            Record(
                kind="concept",
                name=cname,
                short_name=cname,
                parent=None,
                stage="runtime",
                description=desc,
                signature=f"concept {cname}: {_type_str(c.get('type'))}",
                url=f"{base_url}concepts/{cname}.html",
                search_blob=f"{cname} {desc}".lower(),
                raw=c,
            )
        )

    # Defines (recursively walk the tree).
    def walk_defines(nodes: Iterable[dict], path: str) -> None:
        for n in nodes or []:
            fq_path = f"{path}.{n['name']}" if path else n["name"]
            desc = _first_para(n.get("description"))
            values = n.get("values") or []
            sub = n.get("subkeys") or []
            value_names = ", ".join(v.get("name", "?") for v in values) if values else ""
            sig = f"defines.{fq_path}"
            if value_names:
                sig += f" = {{ {value_names} }}"
            anchor = f"defines.{fq_path}"
            out.append(
                Record(
                    kind="define",
                    name=f"defines.{fq_path}",
                    short_name=n["name"],
                    parent=f"defines.{path}" if path else "defines",
                    stage="runtime",
                    description=desc,
                    signature=sig,
                    url=f"{base_url}defines.html#{anchor}",
                    search_blob=f"defines.{fq_path} {value_names} {desc}".lower(),
                    raw=n,
                )
            )
            for v in values:
                vfq = f"defines.{fq_path}.{v['name']}"
                vdesc = _first_para(v.get("description"))
                out.append(
                    Record(
                        kind="define",
                        name=vfq,
                        short_name=v["name"],
                        parent=f"defines.{fq_path}",
                        stage="runtime",
                        description=vdesc,
                        signature=vfq,
                        url=f"{base_url}defines.html#{vfq}",
                        search_blob=f"{vfq} {vdesc}".lower(),
                        raw=v,
                    )
                )
            walk_defines(sub, fq_path)

    walk_defines(doc.get("defines", []), "")

    # Global objects / functions live at the top-level index-runtime.html.
    for g in doc.get("global_objects", []) or []:
        gname = g["name"]
        desc = _first_para(g.get("description"))
        out.append(
            Record(
                kind="global_object",
                name=gname,
                short_name=gname,
                parent=None,
                stage="runtime",
                description=desc,
                signature=f"{gname}: {_type_str(g.get('type'))}",
                url=f"{base_url}index-runtime.html#{gname}",
                search_blob=f"{gname} {desc}".lower(),
                raw=g,
            )
        )
    for gf in doc.get("global_functions", []) or []:
        gname = gf["name"]
        desc = _first_para(gf.get("description"))
        sig = f"{gname}{_method_signature(gf)}"
        out.append(
            Record(
                kind="global_function",
                name=gname,
                short_name=gname,
                parent=None,
                stage="runtime",
                description=desc,
                signature=sig,
                url=f"{base_url}index-runtime.html#{gname}",
                search_blob=f"{gname} {sig} {desc}".lower(),
                raw=gf,
            )
        )

    return out


def _build_prototype_records(doc: dict, base_url: str) -> list[Record]:
    out: list[Record] = []
    for p in doc.get("prototypes", []):
        pname = p["name"]
        desc = _first_para(p.get("description"))
        typename = p.get("typename")
        out.append(
            Record(
                kind="prototype",
                name=pname,
                short_name=pname,
                parent=p.get("parent"),
                stage="prototype",
                description=desc,
                signature=f"prototype {pname}"
                + (f" ({typename})" if typename else "")
                + (f" extends {p['parent']}" if p.get("parent") else ""),
                url=f"{base_url}prototypes/{pname}.html",
                search_blob=f"{pname} {typename or ''} {desc}".lower(),
                raw=p,
            )
        )
        for prop in p.get("properties", []) or []:
            pn = prop["name"]
            fq = f"{pname}.{pn}"
            pdesc = _first_para(prop.get("description"))
            sig = f"{fq}: {_type_str(prop.get('type'))}"
            out.append(
                Record(
                    kind="property",
                    name=fq,
                    short_name=pn,
                    parent=pname,
                    stage="prototype",
                    description=pdesc,
                    signature=sig,
                    url=f"{base_url}prototypes/{pname}.html#{pn}",
                    search_blob=f"{fq} {sig} {pdesc}".lower(),
                    raw=prop,
                )
            )
    for t in doc.get("types", []):
        tname = t["name"]
        desc = _first_para(t.get("description"))
        out.append(
            Record(
                kind="type",
                name=tname,
                short_name=tname,
                parent=t.get("parent"),
                stage="prototype",
                description=desc,
                signature=f"type {tname}: {_type_str(t.get('type'))}",
                url=f"{base_url}types/{tname}.html",
                search_blob=f"{tname} {desc}".lower(),
                raw=t,
            )
        )
    # Defines in prototype-api.json overlap with runtime-api.json; skip to
    # avoid duplicate records — runtime build already covered them.
    return out


def _build_auxiliary_records(
    cache: DocsCache,
    base_url: str,
    paths: Iterable[str],
) -> list[Record]:
    out: list[Record] = []
    for rel in paths:
        try:
            entry = cache.get(rel)
        except Exception as exc:  # noqa: BLE001
            log.warning("skip auxiliary %s: %s", rel, exc)
            continue
        html = entry.read_text()
        text = html_to_text(html)
        # Use the first heading as a short-name fallback.
        m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        title = _compact(m.group(1)) if m else rel
        short = rel.rsplit("/", 1)[-1].removesuffix(".html")
        out.append(
            Record(
                kind="auxiliary",
                name=short,
                short_name=short,
                parent=None,
                stage="auxiliary",
                description=_first_para(text),
                signature=title,
                url=f"{base_url}{rel}",
                search_blob=(title + " " + text).lower(),
                raw={"rel": rel, "text": text},
            )
        )
    return out


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------

@dataclass(slots=True)
class IndexState:
    records: list[Record]
    by_qualified: dict[str, Record]
    runtime_meta: dict
    prototype_meta: dict
    aux_paths: list[str]
    built_at: float


class DocsIndex:
    """Lazy-built, thread-safe index. First tool call triggers the download."""

    def __init__(self, cache: Optional[DocsCache] = None) -> None:
        self.cache = cache or DocsCache()
        self._state: Optional[IndexState] = None
        self._lock = threading.Lock()

    # ---- build -------------------------------------------------------
    def ensure(self, *, force: bool = False) -> IndexState:
        with self._lock:
            if self._state is not None and not force:
                return self._state
            self._state = self._build(force=force)
            return self._state

    def _build(self, *, force: bool) -> IndexState:
        base_url = self.cache.base_url
        log.info("building index (force=%s)", force)

        # Fetch JSON sources.
        runtime = self.cache.get_json("runtime-api.json", force=force)
        proto = self.cache.get_json("prototype-api.json", force=force)

        # Discover auxiliary pages: scrape index-auxiliary.html for links,
        # merge with the seed list, then fetch each.
        aux_paths = list(dict.fromkeys(SEED_AUX_PATHS))
        try:
            aux_index_html = self.cache.get_text("index-auxiliary.html", force=force)
            for href in extract_links(aux_index_html):
                # We only keep relative 'auxiliary/...' entries.
                if href.startswith("auxiliary/"):
                    if href not in aux_paths:
                        aux_paths.append(href)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not discover auxiliary pages: %s", exc)

        records = _build_runtime_records(runtime, base_url)
        records += _build_prototype_records(proto, base_url)
        records += _build_auxiliary_records(self.cache, base_url, aux_paths)

        by_qualified = {r.name: r for r in records}
        return IndexState(
            records=records,
            by_qualified=by_qualified,
            runtime_meta={
                "application_version": runtime.get("application_version"),
                "api_version": runtime.get("api_version"),
                "stage": runtime.get("stage"),
            },
            prototype_meta={
                "application_version": proto.get("application_version"),
                "api_version": proto.get("api_version"),
                "stage": proto.get("stage"),
            },
            aux_paths=aux_paths,
            built_at=time.time(),
        )

    # ---- query -------------------------------------------------------
    def search(
        self,
        pattern: str,
        *,
        kinds: Optional[Iterable[str]] = None,
        stages: Optional[Iterable[str]] = None,
        field: str = "any",       # "name" | "description" | "signature" | "any"
        case_sensitive: bool = False,
        limit: int = 50,
    ) -> list[Record]:
        if not pattern:
            raise ValueError("pattern must not be empty")
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(pattern, flags)
        kind_set = set(kinds) if kinds else None
        stage_set = set(stages) if stages else None
        picker: Callable[[Record], str]
        if field == "name":
            picker = lambda r: r.name
        elif field == "description":
            picker = lambda r: r.description
        elif field == "signature":
            picker = lambda r: r.signature
        else:
            picker = lambda r: r.search_blob if not case_sensitive else (
                f"{r.name} {r.signature} {r.description}"
            )
        state = self.ensure()
        hits: list[Record] = []
        for r in state.records:
            if kind_set and r.kind not in kind_set:
                continue
            if stage_set and r.stage not in stage_set:
                continue
            hay = picker(r)
            if rx.search(hay):
                hits.append(r)
                if len(hits) >= limit:
                    break
        return hits

    def get(self, name: str) -> Optional[Record]:
        state = self.ensure()
        if r := state.by_qualified.get(name):
            return r
        # Try case-insensitive exact match.
        lower = name.lower()
        for r in state.records:
            if r.name.lower() == lower:
                return r
        return None

    def list_names(
        self,
        *,
        kind: Optional[str] = None,
        stage: Optional[str] = None,
        pattern: Optional[str] = None,
        limit: int = 500,
    ) -> list[str]:
        state = self.ensure()
        rx = re.compile(pattern, re.IGNORECASE) if pattern else None
        out: list[str] = []
        for r in state.records:
            if kind and r.kind != kind:
                continue
            if stage and r.stage != stage:
                continue
            if rx and not rx.search(r.name):
                continue
            out.append(r.name)
            if len(out) >= limit:
                break
        return out

    def stats(self) -> dict:
        state = self.ensure()
        counts: dict[str, int] = {}
        for r in state.records:
            counts[r.kind] = counts.get(r.kind, 0) + 1
        return {
            "total_records": len(state.records),
            "by_kind": dict(sorted(counts.items())),
            "runtime": state.runtime_meta,
            "prototype": state.prototype_meta,
            "auxiliary_pages": len(state.aux_paths),
            "built_at": state.built_at,
            "cache_dir": str(self.cache.cache_dir),
            "base_url": self.cache.base_url,
        }

    def refresh(self) -> dict:
        self.ensure(force=True)
        return self.stats()

    # ---- detail render helpers --------------------------------------
    def render(self, r: Record, *, full: bool = True) -> dict:
        out: dict = {
            "kind": r.kind,
            "name": r.name,
            "parent": r.parent,
            "stage": r.stage,
            "signature": r.signature,
            "description": r.description if not full else _compact(_raw_desc(r)),
            "url": r.url,
        }
        if full:
            out["raw"] = _jsonable(r.raw)
        return out


def _raw_desc(r: Record) -> str:
    if isinstance(r.raw, dict):
        return r.raw.get("description") or r.description
    return r.description


def _jsonable(v: Any) -> Any:
    """Ensure the value is JSON-serializable (strip sets, etc.)."""
    try:
        json.dumps(v)
        return v
    except TypeError:
        if isinstance(v, dict):
            return {k: _jsonable(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_jsonable(x) for x in v]
        if isinstance(v, set):
            return sorted(_jsonable(x) for x in v)
        return str(v)
