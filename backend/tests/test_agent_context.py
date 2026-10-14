"""Tests for repository context builders: repo map, conventions, toolchain."""

from __future__ import annotations

import json
from pathlib import Path

from apps.agent.context import (
    build_repo_map,
    detect_toolchain,
    read_instruction_files,
    suggest_commands,
)


def write_tree(root: Path, entries: dict[str, str]) -> None:
    """Create files (or empty dirs when the value is empty) under ``root``."""
    for name, content in entries.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.write_text(content, encoding="utf-8")


class TestRepoMap:
    def test_renders_sorted_tree_with_dir_markers(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "README.md": "# hi\n",
                "b.txt": "b\n",
                "a.txt": "a\n",
                "src/main.py": "print(1)\n",
                "src/util.py": "print(2)\n",
            },
        )
        result = build_repo_map(tmp_path)
        lines = result.text.splitlines()

        assert lines[0] == "."
        # Directories sort first, then files alphabetically.
        src_index = next(i for i, line in enumerate(lines) if "src/" in line)
        a_index = lines.index("|-- a.txt")
        b_index = lines.index("|-- b.txt")
        assert src_index < a_index < b_index
        assert "|   |-- main.py" in result.text
        assert "|   `-- util.py" in result.text
        assert result.files == 5
        assert result.directories == 1
        assert not result.truncated

    def test_hidden_dirs_are_skipped(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "main.py": "x\n",
                "node_modules/pkg/index.js": "x\n",
                ".venv/lib/x.py": "x\n",
                "__pycache__/main.cpython-313.pyc": "x\n",
                ".git/HEAD": "ref\n",
                ".env": "SECRET=1\n",
            },
        )
        result = build_repo_map(tmp_path)
        assert "node_modules" not in result.text
        assert ".venv" not in result.text
        assert "__pycache__" not in result.text
        assert ".git" not in result.text
        assert ".env" not in result.text
        assert "main.py" in result.text

    def test_respects_max_depth(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"a/b/c/deep.py": "x\n"})
        text = build_repo_map(tmp_path, max_depth=2).text
        assert "deep.py" not in text
        text_deep = build_repo_map(tmp_path, max_depth=4).text
        assert "deep.py" in text_deep

    def test_truncation_is_reported(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {f"file_{i:03}.txt": "x" * 50 for i in range(50)})
        result = build_repo_map(tmp_path, max_bytes=120)
        assert result.truncated
        assert "truncated" in result.text

    def test_empty_repo_map(self, tmp_path: Path) -> None:
        result = build_repo_map(tmp_path)
        assert result.text == "."
        assert result.is_empty


class TestInstructionFiles:
    def test_discovers_known_names_and_globs(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "AGENTS.md": "# Agent rules\nKeep diffs tiny.\n",
                "CLAUDE.md": "Use stdlib.\n",
                ".cursor/rules/ui.mdc": "Material 3 only.\n",
                ".github/copilot-instructions.md": "No screenshots.\n",
                "CONTRIBUTING.md": "Sign commits.\n",
            },
        )
        found = read_instruction_files(tmp_path)
        paths = [item.path for item in found]
        assert "AGENTS.md" in paths
        assert "CLAUDE.md" in paths
        assert "CONTRIBUTING.md" in paths
        assert ".cursor/rules/ui.mdc" in paths
        assert ".github/copilot-instructions.md" in paths

    def test_nested_convention_files_are_not_applied_repo_wide(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"docs/AGENTS.md": "doc-only rules\n"})
        assert read_instruction_files(tmp_path) == []

    def test_empty_files_are_skipped(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"AGENTS.md": "   \n"})
        assert read_instruction_files(tmp_path) == []

    def test_total_byte_budget_is_enforced(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "AGENTS.md": "a" * 2_000,
                "CLAUDE.md": "b" * 2_000,
                "CONTRIBUTING.md": "c" * 2_000,
            },
        )
        found = read_instruction_files(tmp_path, max_total_bytes=2_500)
        total = sum(len(item.content) for item in found)
        assert total <= 2_500
        assert any(item.truncated for item in found)

    def test_per_file_budget_truncates(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"AGENTS.md": "x" * 500})
        (item,) = read_instruction_files(tmp_path, max_bytes_per_file=100)
        assert len(item.content) == 100
        assert item.truncated


class TestToolchainDetection:
    def test_python_detection(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "pyproject.toml": (
                    '[project]\nrequires-python = ">=3.12"\n'
                    "[tool.pytest.ini_options]\n"
                    "[tool.ruff]\n"
                ),
                "uv.lock": "version = 1\n",
            },
        )
        detection = detect_toolchain(tmp_path)
        assert detection["languages"] == ["Python"]
        assert "pyproject.toml" in detection["markers"]
        assert detection["managers"] == ["uv"]
        assert detection["python"]["requires_python"] == ">=3.12"
        assert "pytest" in detection["python"]["config"]
        assert "ruff" in detection["python"]["config"]

    def test_requirements_txt_without_pyproject(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"requirements.txt": "django\n"})
        detection = detect_toolchain(tmp_path)
        assert detection["languages"] == ["Python"]
        assert detection["managers"] == ["pip"]

    def test_node_detection(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": json.dumps(
                    {"name": "web", "scripts": {"test": "vitest run", "lint": "eslint ."}}
                ),
                "yarn.lock": "# yarn\n",
            },
        )
        detection = detect_toolchain(tmp_path)
        assert "JavaScript" in detection["languages"]
        assert detection["node_runner"] == "yarn"
        assert "yarn" in detection["managers"]
        assert "test" in detection["package"]["scripts"]

    def test_typescript_marker(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {"package.json": "{}", "tsconfig.json": '{"compilerOptions": {}}'},
        )
        assert "TypeScript" in detect_toolchain(tmp_path)["languages"]

    def test_go_detection(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"go.mod": "module example.com/x\n"})
        assert detect_toolchain(tmp_path)["languages"] == ["Go"]

    def test_rust_and_java(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"Cargo.toml": "[package]\n"})
        assert detect_toolchain(tmp_path)["languages"] == ["Rust"]

        write_tree(tmp_path, {"pom.xml": "<project/>"})
        detection = detect_toolchain(tmp_path)
        assert "Java" in detection["languages"]
        assert "maven" in detection["managers"]

    def test_unknown_repo_returns_empty_dict(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"README.md": "no stack\n"})
        assert detect_toolchain(tmp_path) == {}

    def test_broken_package_json_degrades_gracefully(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"package.json": "{ not json"})
        assert detect_toolchain(tmp_path) == {}


class TestSuggestCommands:
    def test_python_commands_from_config(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "pyproject.toml": "[tool.pytest.ini_options]\n[tool.ruff]\n",
            },
        )
        commands = suggest_commands(detect_toolchain(tmp_path))
        assert commands == {
            "test": "python -m pytest",
            "lint": "python -m ruff check",
            "format": "python -m ruff format",
        }

    def test_black_instead_of_ruff(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"pyproject.toml": "[tool.black]\n"})
        commands = suggest_commands(detect_toolchain(tmp_path))
        assert commands == {"format": "python -m black"}

    def test_python_without_any_tool_config_yields_nothing(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"pyproject.toml": '[project]\nname = "x"\n'})
        assert suggest_commands(detect_toolchain(tmp_path)) == {}

    def test_node_scripts_map_to_the_detected_runner(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": json.dumps(
                    {
                        "scripts": {
                            "test": "vitest run",
                            "lint": "eslint .",
                            "format": "prettier -w .",
                        }
                    }
                ),
                "pnpm-lock.yaml": "lockfileVersion: '9'\n",
            },
        )
        commands = suggest_commands(detect_toolchain(tmp_path))
        assert commands == {
            "test": "pnpm run test",
            "lint": "pnpm run lint",
            "format": "pnpm run format",
        }

    def test_go_defaults(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"go.mod": "module x\n"})
        commands = suggest_commands(detect_toolchain(tmp_path))
        assert commands["test"] == "go test ./..."
        assert commands["lint"] == "go vet ./..."
