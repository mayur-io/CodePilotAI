"""Tests for the path sandbox and secret policy.

These are the deterministic guardrails: nothing here consults a model or a
network, so the tests must be exhaustive and cheap. Every rule the agent relies
on is asserted explicitly rather than implied.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.agent.errors import GuardViolationError
from apps.agent.guards import (
    assert_readable,
    is_ignored,
    is_secret_path,
    resolve_within,
)


class TestSecretPaths:
    def test_plain_source_is_not_secret(self) -> None:
        assert not is_secret_path("src/main.py")
        assert not is_secret_path("README.md")
        assert not is_secret_path("app/settings/base.py")

    def test_dotenv_files_are_secret_at_any_depth(self) -> None:
        assert is_secret_path(".env")
        assert is_secret_path(".env.local")
        assert is_secret_path(".env.production")
        assert is_secret_path("config/.env")

    def test_key_formats_are_secret(self) -> None:
        assert is_secret_path("keys/deploy.pem")
        assert is_secret_path("id_rsa")
        # Conservatively blocked too: `id_ed25519.pub` still matches the
        # `id_ed25519*` glob, and the model never needs a key file at all.
        assert is_secret_path("id_ed25519.pub")
        assert is_secret_path("id_ed25519")
        assert is_secret_path("cert.p12")
        assert is_secret_path("server.keystore")

    def test_credentials_names_are_secret(self) -> None:
        assert is_secret_path("credentials.json")
        assert is_secret_path("config/credential.yaml")
        assert is_secret_path("service-account.json")

    def test_secrets_data_files_are_secret_but_secrets_py_is_code(self) -> None:
        # `secrets.py` is ordinary Django application code; blocking it would
        # prevent the agent from editing a file it is often asked to touch.
        assert not is_secret_path("secrets.py")
        assert not is_secret_path("app/secrets/__init__.py")
        assert is_secret_path("secrets.yml")
        assert is_secret_path("secrets.json")
        assert is_secret_path("deploy/secrets.toml")

    def test_secret_directories_block_everything_under_them(self) -> None:
        assert is_secret_path(".ssh/config")
        assert is_secret_path(".aws/credentials")
        assert is_secret_path(".gnupg/private-keys-v1.d/key")
        assert is_secret_path("services/.azure/tenant.json")

    def test_windows_separators_are_normalised(self) -> None:
        assert is_secret_path(".ssh\\id_ed25519")
        assert is_secret_path("config\\.env.local")
        assert is_secret_path(".env")

    def test_empty_and_root_paths_are_not_secret(self) -> None:
        assert not is_secret_path("")
        assert not is_secret_path("/")

    def test_dotnetrc_and_npmrc(self) -> None:
        assert is_secret_path(".netrc")
        assert is_secret_path(".npmrc")
        assert is_secret_path(".pypirc")


class TestIgnored:
    def test_generated_directories(self) -> None:
        assert is_ignored("node_modules", is_directory=True)
        assert is_ignored("__pycache__", is_directory=True)
        assert is_ignored(".venv", is_directory=True)
        assert is_ignored("dist", is_directory=True)
        assert is_ignored("build", is_directory=True)
        assert is_ignored(".git", is_directory=True)
        assert is_ignored("my.egg-info", is_directory=True)

    def test_generated_file_suffixes(self) -> None:
        assert is_ignored("poetry.lock")
        assert is_ignored("app.pyc")
        assert is_ignored("logo.png")
        assert is_ignored("data.sqlite3")

    def test_source_names_are_not_ignored(self) -> None:
        assert not is_ignored("main.py")
        assert not is_ignored("README.md")
        assert not is_ignored("Makefile")

    def test_os_junk_files(self) -> None:
        assert is_ignored(".DS_Store")
        assert is_ignored("Thumbs.db")


class TestResolveWithin:
    def test_normal_relative_path(self, tmp_path: Path) -> None:
        resolved = resolve_within(tmp_path, "src/main.py")
        assert resolved == (tmp_path / "src" / "main.py").resolve()

    def test_dotted_relative_path(self, tmp_path: Path) -> None:
        assert (
            resolve_within(tmp_path, "./src/../src/main.py")
            == (tmp_path / "src" / "main.py").resolve()
        )

    def test_root_itself(self, tmp_path: Path) -> None:
        assert resolve_within(tmp_path, ".") == tmp_path.resolve()

    def test_absolute_path_inside_root(self, tmp_path: Path) -> None:
        assert (
            resolve_within(tmp_path, str(tmp_path / "a" / "b.py"))
            == (tmp_path / "a" / "b.py").resolve()
        )

    def test_parent_traversal_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(GuardViolationError, match="escapes"):
            resolve_within(tmp_path, "../other/secret.txt")

    def test_deep_traversal_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(GuardViolationError, match="escapes"):
            resolve_within(tmp_path, "a/b/../../../etc/passwd")

    def test_absolute_path_outside_root_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "not-a-workspace"
        with pytest.raises(GuardViolationError, match="escapes"):
            resolve_within(tmp_path, str(outside))

    def test_absolute_etc_path_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(GuardViolationError, match="escapes"):
            resolve_within(tmp_path, "/etc/passwd")

    def test_null_byte_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(GuardViolationError, match="null byte"):
            resolve_within(tmp_path, "a\x00b.py")

    def test_empty_and_none_paths_are_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(GuardViolationError, match="no path"):
            resolve_within(tmp_path, "")
        with pytest.raises(GuardViolationError, match="no path"):
            resolve_within(tmp_path, "   ")

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
    def test_symlink_escaping_the_root_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "symlink-target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not permitted here")
        with pytest.raises(GuardViolationError, match="escapes"):
            resolve_within(tmp_path, "linked/secret.txt")


class TestAssertReadable:
    def _tree(self, tmp_path: Path) -> Path:
        root = tmp_path / "repo"
        (root / "app").mkdir(parents=True)
        (root / "app" / "main.py").write_text("print(1)\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "pkg").write_text("x\n", encoding="utf-8")
        (root / "keys").mkdir()
        (root / "keys" / "deploy.pem").write_text("PRIVATE", encoding="utf-8")
        return root

    def test_plain_source_is_readable(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        path = resolve_within(root, "app/main.py")
        assert_readable(root, path)  # must not raise

    def test_git_internals_are_blocked(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        with pytest.raises(GuardViolationError, match="ignored"):
            assert_readable(root, resolve_within(root, ".git/config"))

    def test_generated_trees_are_blocked(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        with pytest.raises(GuardViolationError, match="ignored"):
            assert_readable(root, resolve_within(root, "node_modules/pkg"))

    def test_secret_files_are_blocked(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        with pytest.raises(GuardViolationError, match="secret"):
            assert_readable(root, resolve_within(root, ".env"))
        with pytest.raises(GuardViolationError, match="secret"):
            assert_readable(root, resolve_within(root, "keys/deploy.pem"))

    def test_path_outside_root_is_blocked(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        with pytest.raises(GuardViolationError, match="escapes"):
            assert_readable(root, tmp_path / "other")
