"""End-to-end tests for the factorio-docs skill CLI pipeline.

Tests exercise the full stack: build_index → docs.db → search CLI.
No network access is made; the pre-built references/docs.db is used throughout.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
REFS = ROOT / "references"
DB = REFS / "docs.db"
API_JSON = REFS / "runtime-api.json"
PYTHON = sys.executable


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def test_api_json_exists():
    assert API_JSON.exists(), "runtime-api.json missing — run fetch_api.py first"


def test_db_exists():
    assert DB.exists(), "docs.db missing — run build_index.py first"


def test_db_row_count():
    conn = sqlite3.connect(DB)
    (count,) = conn.execute("SELECT count(*) FROM docs").fetchone()
    conn.close()
    assert count >= 4000, f"Expected ≥4000 rows, got {count}"


def test_api_json_structure():
    data = json.loads(API_JSON.read_text())
    for key in ("classes", "events", "concepts", "defines", "api_version"):
        assert key in data, f"Missing key: {key}"
    assert len(data["classes"]) >= 100
    assert len(data["events"]) >= 100


# ---------------------------------------------------------------------------
# update.py
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_up_to_date_skips_rebuild(self):
        """When cached version matches remote, update.py exits 0 and skips rebuild."""
        result = run(str(SCRIPTS / "update.py"))
        assert result.returncode == 0
        assert "up to date" in result.stdout.lower()
        # index was NOT rebuilt — no "Built docs.db" line
        assert "built docs.db" not in result.stdout.lower()

    def test_reports_game_version(self):
        result = run(str(SCRIPTS / "update.py"))
        assert result.returncode == 0
        # should mention a version string like "2.0.xx"
        import re
        assert re.search(r"\d+\.\d+\.\d+", result.stdout), "No version string in output"


# ---------------------------------------------------------------------------
# build_index.py
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_skip_if_exists(self, tmp_path):
        """No --force → prints 'already exists', exits 0, does not rebuild."""
        result = run(str(SCRIPTS / "build_index.py"))
        assert result.returncode == 0
        assert "already exists" in result.stderr

    def test_force_rebuild(self, tmp_path):
        """--force → rebuilds successfully and reports correct counts."""
        # build into a temp db to avoid clobbering the real one
        tmp_db = tmp_path / "docs.db"
        shutil.copy(DB, tmp_db)

        # Patch DB_PATH via env is not straightforward; instead we test the
        # real --force path and verify the existing db is still valid after.
        result = run(str(SCRIPTS / "build_index.py"), "--force")
        assert result.returncode == 0
        assert "Built docs.db" in result.stdout
        assert "classes/methods/attrs" in result.stdout

    def test_force_rebuild_row_count(self):
        """After --force, row count matches what the script reports."""
        result = run(str(SCRIPTS / "build_index.py"), "--force")
        assert result.returncode == 0

        import re
        match = re.search(r"(\d+) docs indexed", result.stdout)
        assert match, f"Could not parse count from: {result.stdout}"
        reported = int(match.group(1))

        conn = sqlite3.connect(DB)
        (actual,) = conn.execute("SELECT count(*) FROM docs").fetchone()
        conn.close()
        assert actual == reported

    def test_missing_json_exits_nonzero(self, tmp_path, monkeypatch):
        """If runtime-api.json is absent, the script exits with code 1."""
        fake_json = tmp_path / "runtime-api.json"
        # Do NOT create it — simulate missing file by temporarily renaming.
        backup = API_JSON.with_suffix(".json.bak")
        API_JSON.rename(backup)
        try:
            result = run(str(SCRIPTS / "build_index.py"), "--force")
            assert result.returncode != 0
            assert "not found" in result.stderr
        finally:
            backup.rename(API_JSON)


# ---------------------------------------------------------------------------
# search.py — happy paths
# ---------------------------------------------------------------------------

class TestSearchFullText:
    def test_returns_results(self):
        result = run(str(SCRIPTS / "search.py"), "inventory insert")
        assert result.returncode == 0
        assert "result(s)" in result.stdout

    def test_insert_method_top_ranked(self):
        result = run(str(SCRIPTS / "search.py"), "inventory insert")
        lines = result.stdout.splitlines()
        top = next(l for l in lines if l.startswith("["))
        assert "insert" in top.lower()

    def test_multi_word_or_semantics(self):
        """Both 'fluid' and 'temperature' should appear in results."""
        result = run(str(SCRIPTS / "search.py"), "fluid box temperature")
        assert "fluid" in result.stdout.lower()
        assert result.stdout.count("[") >= 3

    def test_porter_stemming(self):
        """'inserting' (stemmed form) matches 'insert' entries."""
        result = run(str(SCRIPTS / "search.py"), "inserting items inventory")
        assert result.returncode == 0
        assert "insert" in result.stdout.lower()


class TestSearchKindFilter:
    def test_event_filter(self):
        result = run(str(SCRIPTS / "search.py"), "--kind", "event", "entity died")
        assert result.returncode == 0
        lines = [l for l in result.stdout.splitlines() if l.startswith("[")]
        assert all(l.startswith("[event]") for l in lines)

    def test_method_filter(self):
        result = run(str(SCRIPTS / "search.py"), "--kind", "method", "fluid")
        lines = [l for l in result.stdout.splitlines() if l.startswith("[")]
        assert all(l.startswith("[method]") for l in lines)

    def test_attribute_filter(self):
        result = run(str(SCRIPTS / "search.py"), "--kind", "attribute", "energy source")
        lines = [l for l in result.stdout.splitlines() if l.startswith("[")]
        assert all(l.startswith("[attribute]") for l in lines)

    def test_on_built_entity_is_event(self):
        result = run(str(SCRIPTS / "search.py"), "--kind", "event", "built entity")
        assert "on_built_entity" in result.stdout


class TestSearchExact:
    def test_exact_class(self):
        result = run(str(SCRIPTS / "search.py"), "--exact", "LuaEntity")
        assert result.returncode == 0
        assert "[class] LuaEntity" in result.stdout
        assert "1 result(s)" in result.stdout

    def test_exact_event(self):
        result = run(str(SCRIPTS / "search.py"), "--exact", "on_built_entity")
        assert result.returncode == 0
        assert "on_built_entity" in result.stdout

    def test_exact_case_insensitive(self):
        lower = run(str(SCRIPTS / "search.py"), "--exact", "luaentity")
        upper = run(str(SCRIPTS / "search.py"), "--exact", "LuaEntity")
        assert lower.returncode == 0
        # Header line differs (contains original query), compare result entries only
        lower_entries = [l for l in lower.stdout.splitlines() if l.startswith("[")]
        upper_entries = [l for l in upper.stdout.splitlines() if l.startswith("[")]
        assert lower_entries == upper_entries

    def test_exact_nonexistent(self):
        result = run(str(SCRIPTS / "search.py"), "--exact", "NonExistentClass999")
        assert result.returncode == 0
        assert "No results" in result.stdout


class TestSearchParent:
    def test_parent_returns_members(self):
        result = run(str(SCRIPTS / "search.py"), "--parent", "LuaInventory")
        assert result.returncode == 0
        assert result.stdout.count("[") >= 10

    def test_parent_method_filter(self):
        result = run(str(SCRIPTS / "search.py"), "--parent", "LuaInventory", "--kind", "method")
        lines = [l for l in result.stdout.splitlines() if l.startswith("[")]
        assert all(l.startswith("[method]") for l in lines)
        assert len(lines) >= 10

    def test_parent_contains_insert(self):
        result = run(str(SCRIPTS / "search.py"), "--parent", "LuaInventory", "--kind", "method")
        assert "insert" in result.stdout.lower()


# ---------------------------------------------------------------------------
# search.py — edge cases
# ---------------------------------------------------------------------------

class TestSearchEdgeCases:
    def test_no_results_graceful(self):
        result = run(str(SCRIPTS / "search.py"), "xyzzy_nonexistent_zorg")
        assert result.returncode == 0
        assert "No results" in result.stdout

    def test_special_chars_no_crash(self):
        for query in ['()', '"hello"', "it's", "a*b", "a OR b"]:
            result = run(str(SCRIPTS / "search.py"), query)
            assert result.returncode == 0, f"Crashed on query: {query!r}\n{result.stderr}"

    def test_empty_args_prints_help(self):
        result = run(str(SCRIPTS / "search.py"))
        assert result.returncode == 0
        assert "Usage" in result.stdout or "usage" in result.stdout.lower()

    def test_top_flag(self):
        default = run(str(SCRIPTS / "search.py"), "fluid")
        limited = run(str(SCRIPTS / "search.py"), "--top", "3", "fluid")
        default_hits = default.stdout.count("\n[")
        limited_hits = limited.stdout.count("\n[")
        assert limited_hits <= 3
        assert default_hits > limited_hits

    def test_missing_db_exits_nonzero(self, tmp_path):
        backup = DB.with_suffix(".db.bak")
        DB.rename(backup)
        try:
            result = run(str(SCRIPTS / "search.py"), "test")
            assert result.returncode != 0
            assert "not found" in result.stderr or "not found" in result.stdout
        finally:
            backup.rename(DB)

    def test_verbose_flag_shows_full_body(self):
        normal = run(str(SCRIPTS / "search.py"), "inventory insert")
        verbose = run(str(SCRIPTS / "search.py"), "--verbose", "inventory insert")
        assert len(verbose.stdout) >= len(normal.stdout)
