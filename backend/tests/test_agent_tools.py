"""Tests for the read-only tool surface the model is allowed to call.

Everything funnels through the guards, so the security assertions here are the
same class of guarantee as test_agent_guards: a traversal attempt must come
back as a *tool error the model can read*, never as a filesystem result.
"""

from __future__ import annotations

from pathlib import Path

from apps.agent.tools import ToolRegistry

EXPECTED_TOOLS = {"list_dir", "read_file", "search_code", "read_repo_instructions"}


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef greet(name):\n    return f'hi {name}'\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Greeter\n\nUses the add function.\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("Use stdlib only.\n", encoding="utf-8")
    (root / ".env").write_text("DB_PASSWORD=topsecret\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg.js").write_text("module.exports = {}\n", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"\x00\x01\x02\x03not text")
    return root


class TestRegistrySurface:
    def test_exactly_the_expected_tools_are_advertised(self, tmp_path: Path) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        names = {spec.name for spec in registry.specs()}
        assert names == EXPECTED_TOOLS

    def test_openai_schema_shape(self, tmp_path: Path) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        schema = registry.to_openai()
        assert {entry["function"]["name"] for entry in schema} == EXPECTED_TOOLS
        entry = next(e for e in schema if e["function"]["name"] == "read_file")
        assert entry["function"]["parameters"]["type"] == "object"
        assert "path" in entry["function"]["parameters"]["properties"]

    def test_unknown_tool_comes_back_as_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("rm_rf", {})
        assert result.is_error
        assert "Unknown tool" in result.content


class TestListDir:
    def test_lists_entries_with_dir_markers(self, tmp_path: Path) -> None:
        root = make_repo(tmp_path)
        registry = ToolRegistry(root)
        result = registry.run("list_dir", {"path": "."})
        assert not result.is_error
        assert "src/" in result.content
        assert "README.md" in result.content

    def test_omits_ignored_and_secret_entries(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("list_dir", {"path": "."})
        assert ".env" not in result.content
        assert "node_modules/" not in result.content
        # Listing does not sniff file contents, but read_file/search_code refuse
        # binaries, so a binary entry is harmless to list.
        assert "binary.dat" in result.content

    def test_nonexistent_path_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("list_dir", {"path": "nope"})
        assert result.is_error
        assert "does not exist" in result.content

    def test_file_path_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("list_dir", {"path": "README.md"})
        assert result.is_error
        assert "is a file" in result.content

    def test_depth_limits_recursion(self, tmp_path: Path) -> None:
        root = make_repo(tmp_path)
        (root / "src" / "nested").mkdir()
        (root / "src" / "nested" / "deep.py").write_text("x = 1\n", encoding="utf-8")
        registry = ToolRegistry(root)
        # depth 1 shows only the top level; depth 2 reaches src/ but not its
        # children's children; depth 3 reaches src/nested/deep.py.
        assert "deep.py" not in registry.run("list_dir", {"path": ".", "max_depth": 1}).content
        assert "nested/" not in registry.run("list_dir", {"path": ".", "max_depth": 1}).content
        at_depth_two = registry.run("list_dir", {"path": ".", "max_depth": 2}).content
        assert "nested/" in at_depth_two
        assert "deep.py" not in at_depth_two
        assert "deep.py" in registry.run("list_dir", {"path": ".", "max_depth": 3}).content


class TestReadFile:
    def test_returns_numbered_lines_with_header(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": "src/app.py"})
        assert not result.is_error
        assert "(lines 1-6 of 6)" in result.content
        assert "def add(a, b):" in result.content
        assert "1 | def add" in result.content or "1 |" in result.content

    def test_pagination_hints_at_more_lines(self, tmp_path: Path) -> None:
        root = make_repo(tmp_path)
        (root / "long.py").write_text("\n".join(f"line_{i}" for i in range(500)), encoding="utf-8")
        registry = ToolRegistry(root, max_read_lines=50)
        result = registry.run("read_file", {"path": "long.py"})
        assert "(lines 1-50 of 500)" in result.content
        assert "more lines" in result.content

    def test_start_line_and_end_line(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "read_file", {"path": "src/app.py", "start_line": 4, "end_line": 5}
        )
        assert "(lines 4-5 of 6)" in result.content
        assert "greet" in result.content

    def test_nonexistent_file_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": "missing.py"})
        assert result.is_error
        assert "does not exist" in result.content

    def test_directory_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": "src"})
        assert result.is_error
        assert "directory" in result.content

    def test_start_past_end_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "read_file", {"path": "src/app.py", "start_line": 999}
        )
        assert result.is_error

    def test_binary_file_is_refused(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": "binary.dat"})
        assert result.is_error
        assert "not a text file" in result.content

    def test_traversal_is_blocked_and_reported(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": "../../beyond"})
        assert result.is_error
        assert "escapes" in result.content

    def test_secret_file_is_blocked(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": ".env"})
        assert result.is_error
        assert "secret" in result.content or "matches the secret-file policy" in result.content

    def test_absolute_host_path_is_blocked(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_file", {"path": str(tmp_path.parent)})
        assert result.is_error


class TestSearchCode:
    def test_finds_matches_with_locations(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("search_code", {"query": "def add"})
        assert not result.is_error
        assert "src/app.py:1: def add(a, b):" in result.content

    def test_case_insensitive_by_default(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("search_code", {"query": "GREET"})
        assert not result.is_error
        assert "src/app.py:" in result.content

    def test_case_sensitive_flag(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "search_code", {"query": "GREET", "case_sensitive": True}
        )
        assert "No matches" in result.content

    def test_glob_restricts_results(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "search_code", {"query": "uses the", "glob": "*.md"}
        )
        assert "README.md:3: Uses the add function." in result.content

    def test_regex_search(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "search_code", {"query": r"def (add|greet)", "is_regex": True}
        )
        assert not result.is_error
        assert "src/app.py:1" in result.content

    def test_invalid_regex_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "search_code", {"query": "(", "is_regex": True}
        )
        assert result.is_error
        assert "invalid regular expression" in result.content

    def test_no_matches_reports_files_scanned(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("search_code", {"query": "zzzzzz"})
        assert not result.is_error
        assert "No matches" in result.content

    def test_secret_files_are_not_searched(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("search_code", {"query": "DB_PASSWORD"})
        assert "No matches" in result.content

    def test_empty_query_is_an_error(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("search_code", {"query": ""})
        assert result.is_error


class TestReadInstructions:
    def test_returns_repo_conventions(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("read_repo_instructions", {})
        assert not result.is_error
        assert "AGENTS.md" in result.content
        assert "Use stdlib only." in result.content

    def test_reports_when_no_conventions_exist(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()
        result = ToolRegistry(bare).run("read_repo_instructions", {})
        assert not result.is_error
        assert "No convention files found" in result.content


class TestArgumentValidation:
    def test_non_dict_arguments_are_rejected(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run("list_dir", ["."])  # type: ignore[arg-type]
        assert result.is_error
        assert "JSON object" in result.content

    def test_unknown_keyword_is_rejected(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "read_file", {"path": "src/app.py", "bogus": 1}
        )
        assert result.is_error
        assert "Invalid arguments" in result.content

    def test_bool_is_not_accepted_as_line_number(self, tmp_path: Path) -> None:
        result = ToolRegistry(make_repo(tmp_path)).run(
            "read_file", {"path": "src/app.py", "start_line": True}
        )
        assert result.is_error
