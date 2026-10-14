"""The read-only tools the model may call during exploration.

The list is closed and deliberate. There is no write, no shell, and no git
tool: the only way to change a file is to propose a unified diff, which the
user then approves. A tool that could write would make the approval gate
advisory rather than enforced.

Every handler funnels through :func:`apps.agent.guards.resolve_within` and
:func:`apps.agent.guards.assert_readable` before touching the filesystem, and
every handler caps its own output, because tool results are re-injected into
the prompt on every subsequent turn.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.agent.context import read_instruction_files
from apps.agent.errors import GuardViolationError, ToolError
from apps.agent.guards import (
    assert_readable,
    is_ignored,
    is_secret_path,
    resolve_within,
)

__all__: list[str] = [
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
]

#: Lines returned by one `read_file` call.
DEFAULT_READ_LINES = 120
#: Hard ceiling on a single `read_file`, however the model asks.
MAX_READ_LINES = 400
#: Bytes of one file we are willing to load before declaring it binary/too large.
MAX_FILE_BYTES = 512 * 1024
#: Search limits. Both matter: results are re-sent to the model every turn.
DEFAULT_SEARCH_RESULTS = 30
MAX_SEARCH_RESULTS = 100
MAX_FILES_SCANNED = 5_000
#: NUL bytes in the first block means "not text"; do not feed it to a model.
_BINARY_SNIFF = 4_096


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as advertised to the model (OpenAI function-calling shape)."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of one tool call.

    ``is_error`` results are returned to the model as a failed tool message so
    it can correct itself -- a guard refusal is information, not a crash.
    """

    name: str
    content: str
    is_error: bool = False

    def to_message_dict(self, tool_call_id: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": self.name,
            "content": self.content,
        }


class ToolRegistry:
    """Read-only tools bound to one worktree."""

    def __init__(
        self,
        root: Path,
        *,
        max_read_lines: int = DEFAULT_READ_LINES,
        max_search_results: int = DEFAULT_SEARCH_RESULTS,
    ) -> None:
        self.root = root.resolve()
        self.max_read_lines = max_read_lines
        self.max_search_results = max_search_results
        self._handlers: dict[str, Callable[..., ToolResult]] = {
            "list_dir": self._list_dir,
            "read_file": self._read_file,
            "search_code": self._search_code,
            "read_repo_instructions": self._read_instructions,
        }
        self._specs: dict[str, ToolSpec] = {
            "list_dir": ToolSpec(
                name="list_dir",
                description=(
                    "List the contents of a directory inside the repository. "
                    "Paths are relative to the repository root. Directories are "
                    "suffixed with '/'. Hidden, generated, and credential files "
                    "are omitted."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory to list, relative to the repo root.",
                            "default": ".",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Levels to descend (1 = this level only).",
                            "minimum": 1,
                            "maximum": 4,
                            "default": 1,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            "read_file": ToolSpec(
                name="read_file",
                description=(
                    "Read a text file. Returns line-numbered content, paginated. "
                    "Call again with a different start_line to page through a long "
                    "file. Reading never leaves the repository root."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File to read, relative to the repo root.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "1-based first line to return.",
                            "minimum": 1,
                            "default": 1,
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "1-based last line to return (inclusive).",
                            "minimum": 1,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            "search_code": ToolSpec(
                name="search_code",
                description=(
                    "Search file contents across the repository and return "
                    "path:line:match triples. Substring match by default; set "
                    "is_regex for a regular expression. Skips binaries, generated "
                    "trees, and credential files."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text or pattern to search for.",
                        },
                        "glob": {
                            "type": "string",
                            "description": 'Restrict to paths matching this glob, e.g. "*.py".',
                        },
                        "is_regex": {
                            "type": "boolean",
                            "default": False,
                            "description": "Treat query as a regular expression.",
                        },
                        "case_sensitive": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            "read_repo_instructions": ToolSpec(
                name="read_repo_instructions",
                description=(
                    "Read this repository's own convention files (AGENTS.md, "
                    "CLAUDE.md, CONTRIBUTING.md, .cursorrules, .cursor/rules). "
                    "Call this once before writing any code: these override "
                    "generic style guidance."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        }

    # ── public surface ───────────────────────────────────────────────────────

    def specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def to_openai(self) -> list[dict[str, Any]]:
        return [spec.to_openai() for spec in self.specs()]

    def run(self, name: str, arguments: dict[str, Any] | None) -> ToolResult:
        """Execute a tool, converting any guard failure into model feedback."""
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(
                name=name,
                content=f"Unknown tool {name!r}. Available: {', '.join(sorted(self._handlers))}.",
                is_error=True,
            )

        args = arguments or {}
        if not isinstance(args, dict):
            return ToolResult(
                name=name,
                content=f"Arguments must be a JSON object, got {type(args).__name__}.",
                is_error=True,
            )

        try:
            return handler(**args)
        except TypeError as exc:
            # Raised for unknown/missing keyword arguments, i.e. a malformed
            # call -- not a programming error inside the registry.
            return ToolResult(
                name=name,
                content=f"Invalid arguments for {name}: {exc}",
                is_error=True,
            )
        except (GuardViolationError, ToolError) as exc:
            return ToolResult(name=name, content=str(exc), is_error=True)
        except OSError as exc:
            return ToolResult(
                name=name,
                content=f"Filesystem error while running {name}: {exc.strerror or exc}",
                is_error=True,
            )

    # ── handlers ─────────────────────────────────────────────────────────────

    def _list_dir(
        self,
        path: str = ".",
        max_depth: int = 1,
    ) -> ToolResult:
        depth = _as_int(max_depth, "max_depth", "list_dir", minimum=1, maximum=4)
        target = self._resolve(path)

        if not target.exists():
            raise ToolError("list_dir", f"{path!r} does not exist")
        if not target.is_dir():
            raise ToolError("list_dir", f"{path!r} is a file, not a directory")

        lines: list[str] = []
        budget = 400
        truncated = False

        def walk(directory: Path, prefix: str, level: int) -> None:
            nonlocal truncated
            if truncated:
                return
            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda entry: (not entry.is_dir(), entry.name.lower()),
                )
            except OSError:
                return

            shown = 0
            for entry in entries:
                if is_secret_path(entry.name):
                    continue
                is_dir = entry.is_dir()
                if is_dir and is_ignored(entry.name, is_directory=True):
                    continue
                if not is_dir and is_ignored(entry.name):
                    continue
                if shown >= 200:
                    truncated = True
                    return
                shown += 1
                lines.append(f"{prefix}{entry.name}{'/' if is_dir else ''}")
                if is_dir and level < depth:
                    walk(entry, prefix + "  ", level + 1)
                if len(lines) >= budget:
                    truncated = True
                    return

        walk(target, "", 1)

        body = "\n".join(lines) if lines else "(empty)"
        if truncated:
            body += "\n... (truncated)"
        return ToolResult(
            name="list_dir",
            content=f"{path}/\n{body}",
        )

    def _read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ToolResult:
        target = self._resolve(path)
        if not target.exists():
            raise ToolError("read_file", f"{path!r} does not exist")
        if target.is_dir():
            raise ToolError("read_file", f"{path!r} is a directory, use list_dir")

        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ToolError(
                "read_file",
                f"{path!r} is {size} bytes, over the {MAX_FILE_BYTES} byte limit; "
                "search inside it instead",
            )
        if _looks_binary(target):
            raise ToolError("read_file", f"{path!r} is not a text file")

        text = target.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)
        if total == 0:
            return ToolResult(name="read_file", content=f"{path}  (empty file)")

        first = _as_int(start_line, "start_line", "read_file", minimum=1)
        if end_line is None:
            last = min(first + self.max_read_lines - 1, total)
        else:
            last = min(
                _as_int(end_line, "end_line", "read_file", minimum=1),
                first + MAX_READ_LINES - 1,
                total,
            )
        if last < first:
            last = first

        if first > total:
            raise ToolError(
                "read_file",
                f"{path!r} has {total} line(s); start_line {first} is past the end",
            )

        slice_ = all_lines[first - 1 : last]
        width = len(str(last))
        numbered = [f"{first + index:>{width}} | {line}" for index, line in enumerate(slice_)]

        header = f"{path}  (lines {first}-{first + len(slice_) - 1} of {total})"
        more = ""
        next_line = first + len(slice_)
        if next_line <= total:
            remaining = total - next_line + 1
            more = f"\n... ({remaining} more lines; continue from line {next_line})"
        return ToolResult(name="read_file", content=f"{header}\n" + "\n".join(numbered) + more)

    def _search_code(
        self,
        query: str,
        glob: str | None = None,
        is_regex: bool = False,
        case_sensitive: bool = False,
    ) -> ToolResult:
        if not isinstance(query, str) or not query:
            raise ToolError("search_code", "query must be a non-empty string")

        pattern: Any
        if is_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(query, flags)
            except re.error as exc:
                raise ToolError("search_code", f"invalid regular expression: {exc}") from exc
        else:
            pattern = query.lower() if not case_sensitive else query

        matcher = _line_matcher(pattern)
        hits: list[str] = []
        scanned = 0
        truncated = False

        for directory, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                name for name in dirnames if not is_ignored(name, is_directory=True)
            )
            for filename in sorted(filenames):
                if scanned >= MAX_FILES_SCANNED:
                    truncated = True
                    break
                if is_ignored(filename) or is_secret_path(filename):
                    continue
                file_path = Path(directory) / filename
                if not file_path.is_file():
                    continue
                if glob and not fnmatch.fnmatch(file_path.relative_to(self.root).as_posix(), glob):
                    continue
                scanned += 1
                if _looks_binary(file_path):
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                relative = file_path.relative_to(self.root).as_posix()
                for number, line in enumerate(content.splitlines(), start=1):
                    if matcher(line):
                        hits.append(f"{relative}:{number}: {line.strip()[:300]}")
                        if len(hits) >= self.max_search_results:
                            truncated = True
                            break
                if truncated:
                    break
            if truncated:
                break

        if not hits:
            return ToolResult(
                name="search_code",
                content=f"No matches for {query!r} in {scanned} file(s).",
            )

        body = "\n".join(hits)
        suffix = "\n... (more matches exist; narrow the query or the glob)" if truncated else ""
        return ToolResult(
            name="search_code",
            content=f"{len(hits)} match(es), {scanned} file(s) scanned:\n{body}{suffix}",
        )

    def _read_instructions(self) -> ToolResult:
        files = read_instruction_files(self.root)
        if not files:
            return ToolResult(
                name="read_repo_instructions",
                content=(
                    "No convention files found (looked for AGENTS.md, CLAUDE.md, "
                    "CONTRIBUTING.md, .cursorrules, .cursor/rules)."
                ),
            )

        blocks = []
        for item in files:
            note = " (truncated)" if item.truncated else ""
            blocks.append(f"### {item.path}{note}\n{item.content.strip()}")
        return ToolResult(
            name="read_repo_instructions",
            content="\n\n".join(blocks),
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        resolved = resolve_within(self.root, path)
        assert_readable(self.root, resolved)
        return resolved


def _line_matcher(pattern: Any) -> Callable[[str], bool]:
    if isinstance(pattern, str):
        # `pattern` is already lowercased by the caller for the default
        # case-insensitive match, so the line must be lowered to match too.
        return lambda line: pattern in line.lower()
    return lambda line: bool(pattern.search(line))


def _looks_binary(path: Path) -> bool:
    """Cheap binary sniff: a NUL byte in the first block means "not text"."""
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(_BINARY_SNIFF)
    except OSError:
        return True


def _as_int(value: Any, name: str, tool: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(tool, f"{name} must be an integer, got {value!r}")
    if value < minimum:
        raise ToolError(tool, f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ToolError(tool, f"{name} must be <= {maximum}, got {value}")
    return value
