"""Repository context: what the model is told about the codebase before it acts.

Three builders, all read-only and all budgeted:

* :func:`build_repo_map` -- a depth-limited tree, so the model knows the shape
  of the repo without paying for every file.
* :func:`read_instruction_files` -- the repo's own conventions, injected
  verbatim. Repo conventions beat generic style rules (Task.md §6).
* :func:`detect_toolchain` -- language, package manager, and the formatter /
  linter / test commands the repo already uses, so nothing is invented.

Everything here filters through :mod:`apps.agent.guards`, so a secret file can
never be listed, summarised, or quoted into a prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.agent.guards import is_ignored, is_secret_path

__all__: list[str] = [
    "INSTRUCTION_FILENAMES",
    "InstructionFile",
    "RepoMap",
    "build_repo_map",
    "detect_toolchain",
    "read_instruction_files",
    "suggest_commands",
]

#: Files whose entire purpose is to tell a contributor how this repo works.
INSTRUCTION_FILENAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    "COPILOT.md",
    "GEMINI.md",
    "CONTRIBUTING.md",
    "HACKING.md",
    ".cursorrules",
)

#: Additional instruction sources matched relative to the repository root.
#: Deliberately only the three well-known locations: a convention file buried
#: in an arbitrary subdirectory documents that subdirectory, not the repo.
INSTRUCTION_GLOBS = (
    ".cursor/rules/*.mdc",
    ".github/copilot-instructions.md",
)


@dataclass(frozen=True, slots=True)
class RepoMap:
    """A rendered directory tree and the bookkeeping needed to trust it."""

    text: str
    files: int
    directories: int
    truncated: bool

    @property
    def is_empty(self) -> bool:
        return self.files == 0 and self.directories == 0


@dataclass(frozen=True, slots=True)
class InstructionFile:
    """One convention file, already read and size-checked."""

    path: str
    content: str
    truncated: bool = False


@dataclass(slots=True)
class _Cursor:
    """Running limits for one repo-map render."""

    max_entries: int
    max_bytes: int
    entries: int = 0
    directories: int = 0
    files: int = 0
    bytes_used: int = 0
    truncated: bool = False

    def spend(self, line: str, *, is_dir: bool) -> bool:
        """Record one line. Returns ``False`` once any limit is reached."""
        if self.truncated:
            return False
        if self.entries >= self.max_entries or self.bytes_used + len(line) > self.max_bytes:
            self.truncated = True
            return False
        self.entries += 1
        self.bytes_used += len(line)
        if is_dir:
            self.directories += 1
        else:
            self.files += 1
        return True


def build_repo_map(
    root: Path,
    *,
    max_depth: int = 4,
    max_entries: int = 400,
    max_bytes: int = 12_000,
) -> RepoMap:
    """Render a depth-limited tree of ``root``.

    Sorted at every level so the map is deterministic: the model sees the same
    order a human would get from ``tree``, and repeated calls produce identical
    context (which keeps Groq's prompt cache warm -- see Task.md §3).
    """
    cursor = _Cursor(max_entries=max_entries, max_bytes=max_bytes)
    lines = ["."]

    def walk(directory: Path, prefix: str, depth: int) -> None:
        try:
            children = sorted(
                (entry for entry in directory.iterdir()),
                key=lambda entry: (not entry.is_dir(), entry.name.lower()),
            )
        except OSError:
            return

        visible: list[Path] = []
        for child in children:
            if is_secret_path(child.name):
                continue
            if child.is_dir() and is_ignored(child.name, is_directory=True):
                continue
            if child.is_file() and is_ignored(child.name):
                continue
            visible.append(child)

        for index, child in enumerate(visible):
            last = index == len(visible) - 1
            branch = "`-- " if last else "|-- "
            continuation = "    " if last else "|   "
            is_dir = child.is_dir()
            line = f"{prefix}{branch}{child.name}{'/' if is_dir else ''}"
            if not cursor.spend(line, is_dir=is_dir):
                return
            lines.append(line)
            if is_dir and depth + 1 <= max_depth:
                walk(child, prefix + continuation, depth + 1)

    walk(root, "", 1)

    if cursor.truncated:
        lines.append("... (truncated)")

    return RepoMap(
        text="\n".join(lines),
        files=cursor.files,
        directories=cursor.directories,
        truncated=cursor.truncated,
    )


def read_instruction_files(
    root: Path,
    *,
    max_files: int = 8,
    max_bytes_per_file: int = 4_000,
    max_total_bytes: int = 16_000,
) -> list[InstructionFile]:
    """Collect the repo's convention files, largest-value first.

    Files are read from the repository root only (plus the known ``.cursor``
    and ``.github`` locations): a convention file buried in a subdirectory
    usually documents that subdirectory and would mislead the model if applied
    repo-wide.
    """
    candidates: list[Path] = []
    for name in INSTRUCTION_FILENAMES:
        path = root / name
        if path.is_file():
            candidates.append(path)
    for pattern in INSTRUCTION_GLOBS:
        candidates.extend(sorted(root.glob(pattern)))

    collected: list[InstructionFile] = []
    total = 0
    seen: set[Path] = set()

    for path in candidates:
        if len(collected) >= max_files or total >= max_total_bytes:
            break
        resolved = path.resolve()
        if resolved in seen or not resolved.is_relative_to(root.resolve()):
            continue
        seen.add(resolved)

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        truncated = len(raw) > max_bytes_per_file
        if truncated:
            raw = raw[:max_bytes_per_file]
        remaining = max_total_bytes - total
        if len(raw) > remaining:
            raw = raw[:remaining]
            truncated = True
        if not raw.strip():
            continue

        total += len(raw)
        collected.append(
            InstructionFile(
                path=path.relative_to(root).as_posix(),
                content=raw,
                truncated=truncated,
            )
        )

    return collected


def detect_toolchain(root: Path) -> dict[str, Any]:
    """Identify the languages, package manager, and build markers present.

    Returns an empty dict when nothing is recognisable -- an unknown repo is a
    valid answer, and the caller must not invent a toolchain to fill the gap.
    """
    languages: list[str] = []
    managers: list[str] = []
    markers: list[str] = []
    details: dict[str, Any] = {}

    pyproject = _read(root / "pyproject.toml")
    if pyproject or any(
        (root / name).is_file() for name in ("setup.py", "setup.cfg", "requirements.txt", "Pipfile")
    ):
        languages.append("Python")
        markers.extend(
            name
            for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile")
            if (root / name).is_file()
        )
        managers.extend(_python_managers(root))
        details["python"] = _python_details(pyproject, root)

    package_json = _read_json(root / "package.json")
    if package_json is not None:
        languages.extend(
            ("JavaScript", "TypeScript") if _uses_typescript(root, package_json) else ["JavaScript"]
        )
        markers.append("package.json")
        node_runner = _node_manager(root)
        managers.append(node_runner)
        details["node_runner"] = node_runner
        details["package"] = _node_details(package_json)

    if (root / "go.mod").is_file():
        languages.append("Go")
        markers.append("go.mod")
    if (root / "Cargo.toml").is_file():
        languages.append("Rust")
        markers.append("Cargo.toml")
    if (root / "pom.xml").is_file():
        languages.append("Java")
        markers.append("pom.xml")
        managers.append("maven")
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        languages.append("Java")
        markers.append(
            "build.gradle.kts" if (root / "build.gradle.kts").is_file() else "build.gradle"
        )
        managers.append("gradle")
    if (root / "Gemfile").is_file():
        languages.append("Ruby")
        markers.append("Gemfile")
    if (root / "composer.json").is_file():
        languages.append("PHP")
        markers.append("composer.json")
    if (root / "mix.exs").is_file():
        languages.append("Elixir")
        markers.append("mix.exs")

    if not languages:
        return {}

    return {
        "languages": sorted(set(languages)),
        "managers": sorted({manager for manager in managers if manager}),
        "markers": sorted(set(markers)),
        **details,
    }


def suggest_commands(detection: dict[str, Any]) -> dict[str, str]:
    """Derive test / lint / format commands from detected config only.

    Every branch requires evidence in the repo. When nothing is found the key
    is simply absent, and the verify step refuses to run rather than guessing.
    """
    commands: dict[str, str] = {}

    if "Python" in detection.get("languages", []):
        if _has_pytest(detection):
            commands["test"] = "python -m pytest"
        if _has_ruff(detection):
            commands["lint"] = "python -m ruff check"
            commands["format"] = "python -m ruff format"
        elif _has_black(detection):
            commands["format"] = "python -m black"

    package = detection.get("package") or {}
    scripts = package.get("scripts") or {}
    runner = detection.get("node_runner") or "npm"
    run = f"{runner} run" if runner != "yarn" else "yarn"
    if "test" in scripts:
        commands["test"] = f"{runner} test" if runner in {"npm", "yarn"} else f"{runner} run test"
    if "lint" in scripts:
        commands["lint"] = f"{run} lint"
    if "lint:fix" in scripts and "lint" not in commands:
        commands["lint"] = f"{run} lint"
    for name in ("format", "fmt", "prettier"):
        if name in scripts:
            commands["format"] = f"{run} {name}"
            break

    if "Go" in detection.get("languages", []):
        commands.setdefault("test", "go test ./...")
        commands.setdefault("lint", "go vet ./...")
    if "Rust" in detection.get("languages", []):
        commands.setdefault("test", "cargo test")
        commands.setdefault("lint", "cargo clippy -- -D warnings")
        commands.setdefault("format", "cargo fmt --check")
    if "Java" in detection.get("languages", []):
        commands.setdefault(
            "test", "mvn -q test" if "maven" in detection.get("managers", []) else "./gradlew test"
        )

    return commands


# ── helpers ──────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parse a JSON file, returning ``None`` for missing *or* malformed input.

    A broken ``package.json`` is common enough that treating it as an error
    would fail tasks on unrelated repos; treating it as "no package manifest"
    degrades to a smaller context instead.
    """
    raw = _read(path)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _python_managers(root: Path) -> list[str]:
    managers = []
    if (root / "uv.lock").is_file():
        managers.append("uv")
    if (root / "poetry.lock").is_file():
        managers.append("poetry")
    if (root / "Pipfile.lock").is_file():
        managers.append("pipenv")
    if (root / "pdm.lock").is_file():
        managers.append("pdm")
    if not managers:
        managers.append("pip")
    return managers


def _python_details(pyproject: str, root: Path) -> dict[str, Any]:
    details: dict[str, Any] = {}
    requires = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', pyproject)
    if requires:
        details["requires_python"] = requires.group(1)

    flags = details.setdefault("config", [])
    for needle, label in (
        ("[tool.pytest", "pytest"),
        ("[tool.ruff", "ruff"),
        ("[tool.black", "black"),
        ("[tool.mypy", "mypy"),
        ("[tool.isort", "isort"),
        ("[tool.coverage", "coverage"),
    ):
        if needle in pyproject:
            flags.append(label)
    for name in (
        "pytest.ini",
        "tox.ini",
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        ".pre-commit-config.yaml",
    ):
        if (root / name).is_file():
            flags.append(name)
    if (root / "conftest.py").is_file() and "pytest" not in flags:
        flags.append("pytest")
    return details


def _uses_typescript(root: Path, package_json: dict[str, Any]) -> bool:
    if (root / "tsconfig.json").is_file():
        return True
    dependencies = {
        **(package_json.get("devDependencies") or {}),
        **(package_json.get("dependencies") or {}),
    }
    return any(str(name).startswith("typescript") for name in dependencies)


def _node_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun"
    return "npm"


def _node_details(package_json: dict[str, Any]) -> dict[str, Any]:
    scripts = package_json.get("scripts")
    return {
        "name": str(package_json.get("name") or ""),
        "scripts": sorted(scripts) if isinstance(scripts, dict) else [],
    }


def _has_pytest(detection: dict[str, Any]) -> bool:
    config = detection.get("python", {}).get("config", [])
    return any(entry in {"pytest", "pytest.ini", "tox.ini"} for entry in config)


def _has_ruff(detection: dict[str, Any]) -> bool:
    config = detection.get("python", {}).get("config", [])
    return any(entry in {"ruff", "ruff.toml", ".ruff.toml"} for entry in config)


def _has_black(detection: dict[str, Any]) -> bool:
    config = detection.get("python", {}).get("config", [])
    return "black" in config
