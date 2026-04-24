"""Exercise every MCP tool with realistic and edge-case inputs."""

from __future__ import annotations

import asyncio
import json
import sys
import time


def _pp(title: str, body: dict, limit: int = 800) -> None:
    s = json.dumps(body, indent=2, ensure_ascii=False, default=str)
    print(f"\n=== {title} ===")
    if len(s) > limit:
        print(s[:limit] + f"\n  ...[truncated, total {len(s)} chars]")
    else:
        print(s)


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "factorio_docs_mcp"],
    )

    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        tag = "OK " if cond else "FAIL"
        print(f"[{tag}] {name}{(' — ' + detail) if detail else ''}")
        if not cond:
            failures.append(name)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ---- list tools --------------------------------------------------
            t0 = time.time()
            tools = await session.list_tools()
            tool_names = sorted(t.name for t in tools.tools)
            print("tools:", tool_names, f"({time.time() - t0:.2f}s)")
            check(
                "all tools registered",
                set(tool_names)
                == {
                    "auxiliary",
                    "cache_info",
                    "get",
                    "list_entries",
                    "refresh",
                    "search",
                    "stats",
                },
            )

            async def call(tool: str, args: dict) -> dict:
                r = await session.call_tool(tool, args)
                return json.loads(r.content[0].text)

            # ---- stats -------------------------------------------------------
            t0 = time.time()
            s = await call("stats", {})
            dt = time.time() - t0
            _pp("stats", s)
            check(
                "stats returns records",
                s.get("total_records", 0) > 5000,
                f"{s.get('total_records')} records, first call took {dt:.2f}s",
            )
            check("runtime version present", bool(s["runtime"].get("application_version")))
            check("prototype counts present", "prototype" in s["by_kind"])

            # Second stats call — must be sub-100ms (cache hit, no rebuild).
            t0 = time.time()
            await call("stats", {})
            check("stats warm < 0.2s", (time.time() - t0) < 0.2)

            # ---- search: plain substring -------------------------------------
            r = await call(
                "search",
                {"pattern": "create_entity", "kinds": ["method"], "limit": 5},
            )
            _pp("search create_entity method", r)
            names = [h["name"] for h in r["results"]]
            check(
                "create_entity found under LuaSurface",
                "LuaSurface.create_entity" in names,
            )

            # ---- search: regex anchors ---------------------------------------
            r = await call(
                "search",
                {"pattern": r"^on_player_", "kinds": ["event"], "limit": 20},
            )
            _pp("events ^on_player_", {"count": r["count"], "first5": [x["name"] for x in r["results"][:5]]})
            check("many on_player_ events", r["count"] >= 10)
            check(
                "every result starts with on_player_",
                all(x["name"].startswith("on_player_") for x in r["results"]),
            )

            # ---- search: field=name, case_sensitive --------------------------
            r = await call(
                "search",
                {
                    "pattern": r"^LuaSurface$",
                    "field": "name",
                    "case_sensitive": True,
                    "limit": 3,
                },
            )
            _pp("exact LuaSurface class", r)
            check(
                "single LuaSurface class hit",
                r["count"] == 1 and r["results"][0]["kind"] == "class",
            )

            # ---- search: description field -----------------------------------
            r = await call(
                "search",
                {
                    "pattern": r"serpent",
                    "stages": ["auxiliary"],
                    "field": "any",
                    "limit": 5,
                },
            )
            _pp("mentions of serpent in auxiliary", {"count": r["count"], "names": [x["name"] for x in r["results"]]})
            check("libraries page mentions serpent", any(x["name"] == "libraries" for x in r["results"]))

            # ---- search: kind filter validation ------------------------------
            r = await call("search", {"pattern": ".", "kinds": ["not_a_kind"]})
            _pp("bad kind filter", r)
            check("bad kind returns error", "error" in r)

            # ---- get: class --------------------------------------------------
            r = await call("get", {"name": "LuaSurface"})
            _pp("get LuaSurface", {k: r.get(k) for k in ("kind", "name", "url", "signature")})
            check("get LuaSurface is class", r.get("kind") == "class")
            check("has methods in raw", isinstance(r.get("raw", {}).get("methods"), list))

            # ---- get: method (fully qualified) -------------------------------
            r = await call("get", {"name": "LuaSurface.create_entity"})
            _pp("get LuaSurface.create_entity", {
                "kind": r.get("kind"),
                "parent": r.get("parent"),
                "url": r.get("url"),
                "signature_head": (r.get("signature") or "")[:200],
            })
            check("url includes anchor", "#LuaSurface.create_entity" in r.get("url", ""))
            check("parent is LuaSurface", r.get("parent") == "LuaSurface")

            # ---- get: event --------------------------------------------------
            r = await call("get", {"name": "on_tick"})
            _pp("get on_tick", {"kind": r.get("kind"), "url": r.get("url")})
            check("on_tick is event", r.get("kind") == "event")
            check("on_tick url is anchor on events.html", r.get("url", "").endswith("events.html#on_tick"))

            # ---- get: nested define ------------------------------------------
            r = await call("get", {"name": "defines.alert_type"})
            _pp("get defines.alert_type", {"kind": r.get("kind"), "url": r.get("url"), "sig": (r.get("signature") or "")[:200]})
            check("defines.alert_type is define", r.get("kind") == "define")

            # ---- get: prototype + type ---------------------------------------
            r = await call("get", {"name": "ContainerPrototype"})
            _pp("get ContainerPrototype", {"kind": r.get("kind"), "url": r.get("url")})
            check("ContainerPrototype is prototype", r.get("kind") == "prototype")
            check("proto url correct", r.get("url", "").endswith("prototypes/ContainerPrototype.html"))

            r = await call("get", {"name": "BoundingBox"})
            _pp("get BoundingBox", {"kind": r.get("kind"), "url": r.get("url")})
            check("BoundingBox is type", r.get("kind") == "type")

            # ---- get: not found ---------------------------------------------
            r = await call("get", {"name": "LuaDoesNotExist"})
            _pp("get nonexistent", r)
            check("missing returns error field", "error" in r)

            # ---- auxiliary ---------------------------------------------------
            r = await call("auxiliary", {"page": "libraries"})
            _pp("aux libraries (head)", {"url": r.get("url"), "text_len": len(r.get("text", "")), "head": r.get("text", "")[:220]})
            check("libraries has content", len(r.get("text", "")) > 2000)
            check("libraries mentions table_size", "table_size" in r.get("text", ""))

            r = await call("auxiliary", {"page": "data-lifecycle"})
            check("data-lifecycle fetched", len(r.get("text", "")) > 1000)

            # bare filename form
            r = await call("auxiliary", {"page": "mod-structure.html"})
            check("mod-structure.html bare form", len(r.get("text", "")) > 1000)

            # non-existent page should return an error, not crash
            r = await call("auxiliary", {"page": "nope-no-such-page"})
            _pp("aux nope", r)
            check("bad aux returns error", "error" in r)

            # ---- list_entries -------------------------------------------------
            r = await call(
                "list_entries",
                {"kind": "class", "pattern": r"^Lua[A-D]", "limit": 50},
            )
            _pp("classes ^Lua[A-D]", {"count": r["count"], "first10": r["names"][:10]})
            check("Lua[A-D] classes present", r["count"] > 5)
            check(
                "every name matches regex",
                all(n.startswith(("LuaA", "LuaB", "LuaC", "LuaD")) for n in r["names"]),
            )

            r = await call(
                "list_entries",
                {"kind": "event", "pattern": r"robot", "limit": 10},
            )
            _pp("events matching 'robot'", r)
            check("at least one robot event", r["count"] >= 1)

            # ---- cache_info --------------------------------------------------
            r = await call("cache_info", {})
            n = len(r.get("entries", []))
            print(f"\n=== cache_info: {n} entries ===")
            # Show a representative subset
            for e in r["entries"][:3]:
                print(f"  {e['path']:50s} age={e['age_seconds']:.1f}s len={e['content_length']}")
            check("cache has >= 14 files", n >= 14)

            # ---- refresh -----------------------------------------------------
            t0 = time.time()
            r = await call("refresh", {})
            dt = time.time() - t0
            _pp("refresh (304 path)", {"records": r.get("total_records"), "took_seconds": round(dt, 2)})
            check("refresh returns stats", r.get("total_records", 0) > 5000)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
