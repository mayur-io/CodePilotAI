"""Path sandbox and secret policy for every tool the model can call.

Two independent checks, both applied before a path is touched:

1. **Containment** -- the resolved path must stay inside the worktree. ``..``,
   absolute paths, and symlinks that point out of the tree are all rejected by
   resolving first and comparing afterwards, so a symlink named ``notes`` that
   targets ``/etc`` is caught exactly like ``../../etc``.
2. **Secret policy** -- a path that merely *looks* like a credential is refused
   even when it is perfectly well contained. The model has no legitimate reason
   to read a private key, and letting one into context would put the secret in
   every subsequent prompt and in the stored message history.

Neither check consults the model. They are the deterministic half of the
guardrails in Task.md §6/§7.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath

from apps.agent.errors import GuardViolationError

__all__: list[str] = [
    "IGNORED_DIRECTORIES",
    "IGNORED_SUFFIXES",
    "SECRET_DIRECTORIES",
    "SECRET_FILENAMES",
    "SECRET_NAME_PATTERNS",
    "assert_readable",
    "guard_change_path",
    "is_ignored",
    "is_secret_path",
    "resolve_within",
]

#: Directories that never carry source the model needs. Skipping them keeps the
#: repo map small (context budget) and keeps generated trees out of search.
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "__pycache__",
        ".ipynb_checkpoints",
        "node_modules",
        "bower_components",
        "vendor",
        "dist",
        "build",
        "out",
        "target",
        "coverage",
        ".next",
        ".nuxt",
        ".turbo",
        ".gradle",
        ".terraform",
        "venv",
        ".venv",
        "env",
        ".eggs",
    }
)

#: Directory components that are credential stores in their own right, checked
#: at any depth so ``services/.aws/credentials`` is caught like ``.aws/``.
SECRET_DIRECTORIES = frozenset({".ssh", ".aws", ".gnupg", ".azure"})

#: Generated or binary-ish files that waste context and break text search.
IGNORED_SUFFIXES = (
    ".lock",
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".o",
    ".a",
    ".class",
    ".jar",
    ".war",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".sqlite3",
    ".parquet",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".h5",
    ".pt",
    ".onnx",
    ".wasm",
)

#: Credential-shaped file names. Patterns match the *basename* only, so the rule
#: reads the same whether the file sits at the repo root or five levels down.
SECRET_NAME_PATTERNS = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.ppk",
    "*.p8",
    "*.gpg",
    "*.crt",
    "*.cer",
    "*.der",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials*",
    "credential*",
    "*.secret",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "authorized_keys",
)

#: Exact names that are unmistakably a secret store. Deliberately *not* globs:
#: `secrets.py` is ordinary application code in most Python projects, and
#: blocking it would make the agent unable to read a file it is often asked to
#: edit. Only the data formats are listed.
SECRET_FILENAMES = frozenset(
    {
        "secrets.yml",
        "secrets.yaml",
        "secrets.json",
        "secrets.toml",
        "secrets.ini",
        "secrets.conf",
        "service-account.json",
        "firebase-adminsdk.json",
    }
)


def is_secret_path(relative: str) -> bool:
    """True when ``relative`` looks like it holds credentials.

    Accepts POSIX or Windows separators and normalises to POSIX first, because
    the model writes paths the way its training data did.
    """
    normalised = relative.replace("\\", "/").strip().strip("/")
    if not normalised:
        return False

    posix = PurePosixPath(normalised)
    if any(part.lower() in SECRET_DIRECTORIES for part in posix.parts):
        return True

    lowered = posix.name.lower()
    if lowered in SECRET_FILENAMES:
        return True
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in SECRET_NAME_PATTERNS)


def is_ignored(name: str, *, is_directory: bool = False) -> bool:
    """True for directories and files that never belong in agent context."""
    if is_directory:
        if name in IGNORED_DIRECTORIES:
            return True
        # `*.egg-info` style entries, which a frozenset cannot express.
        return name.endswith(".egg-info")

    if name in {".DS_Store", "Thumbs.db"}:
        return True
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in IGNORED_SUFFIXES)


def resolve_within(root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` against ``root`` and assert it stayed inside.

    ``candidate`` may be relative, absolute, or full of ``..``. The comparison
    happens *after* ``resolve()``, so a symlink pointing out of the tree is
    rejected rather than followed.

    Raises :class:`GuardViolationError` -- never a bare ``OSError`` -- so the caller
    can hand the reason straight back to the model.
    """
    raw = "" if candidate is None else str(candidate).strip()
    if not raw:
        raise GuardViolationError("", "no path supplied")
    if "\x00" in raw:
        raise GuardViolationError(raw, "path contains a null byte")

    root_resolved = root.resolve()
    target = Path(raw)
    resolved = target.resolve() if target.is_absolute() else (root_resolved / raw).resolve()

    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise GuardViolationError(raw, "path escapes the repository workspace")

    return resolved


def guard_change_path(worktree: Path, raw: str) -> str:
    """Sandbox and policy check for a path the model wants to diff or repair.

    Judged component-by-component as *directories* as well as files, so a
    proposal that would create ``dist/output.js`` or ``.ssh/config`` is refused
    even when those directories do not exist yet.

    Returns the repo-relative POSIX path. Raises :class:`GuardViolationError`.
    """
    resolved = resolve_within(worktree, raw)
    relative = resolved.relative_to(worktree.resolve()).as_posix()
    if is_secret_path(relative):
        raise GuardViolationError(relative, "matches the secret-file policy")
    for part in PurePosixPath(relative).parts:
        if is_ignored(part, is_directory=True) or is_ignored(part):
            raise GuardViolationError(relative, "ignored or generated path")
    return relative


def assert_readable(root: Path, resolved: Path) -> None:
    """Apply the ignore and secret policy to an already-contained path.

    Containment (:func:`resolve_within`) proves the path is *inside* the
    workspace; this proves the workspace path is one the model is allowed to
    see. Both are needed: `.git/config` is perfectly contained and still must
    never reach a prompt.
    """
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise GuardViolationError(str(resolved), "path escapes the repository workspace")

    relative = resolved.relative_to(root_resolved).as_posix()
    if is_secret_path(relative):
        raise GuardViolationError(relative, "matches the secret-file policy")

    # Walk component by component so a directory named `build` is judged as a
    # directory and a file named `build.py` as a file: the two policies differ.
    cursor = root_resolved
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        is_directory = cursor.is_dir()
        if is_ignored(part, is_directory=is_directory):
            raise GuardViolationError(relative, "ignored or generated path")
        if is_directory and is_secret_path(part):
            raise GuardViolationError(relative, "matches the secret-file policy")
