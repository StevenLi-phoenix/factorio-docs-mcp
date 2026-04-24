"""Spawn the stdio server, run the MCP handshake, and exercise each tool."""

from __future__ import annotations

import asyncio
import json
import sys


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "factorio_docs_mcp"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("TOOLS:", sorted(t.name for t in tools.tools))

            # 1. stats
            res = await session.call_tool("stats", {})
            body = json.loads(res.content[0].text)
            print(
                "STATS:",
                body.get("total_records"),
                "records · runtime",
                body["runtime"]["application_version"],
            )

            # 2. search for a method
            res = await session.call_tool(
                "search",
                {"pattern": "create_entity", "kinds": ["method"], "limit": 3},
            )
            body = json.loads(res.content[0].text)
            print("SEARCH count:", body["count"])
            for r in body["results"]:
                print("  -", r["name"], "->", r["url"])

            # 3. get detail
            res = await session.call_tool(
                "get", {"name": "LuaSurface.create_entity"}
            )
            body = json.loads(res.content[0].text)
            print("GET name:", body.get("name"), "kind:", body.get("kind"))
            assert body.get("url", "").endswith(
                "classes/LuaSurface.html#LuaSurface.create_entity"
            ), body.get("url")

            # 4. auxiliary
            res = await session.call_tool("auxiliary", {"page": "libraries"})
            body = json.loads(res.content[0].text)
            print(
                "AUX libraries:",
                body.get("url"),
                "text_len:",
                len(body.get("text", "")),
            )
            assert "serpent" in body.get("text", "").lower()

            # 5. list_entries
            res = await session.call_tool(
                "list_entries",
                {"kind": "event", "pattern": "^on_player_", "limit": 3},
            )
            body = json.loads(res.content[0].text)
            print("LIST events:", body["count"], "first:", body["names"][:3])

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
