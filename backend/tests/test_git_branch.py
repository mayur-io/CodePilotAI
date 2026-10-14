"""Tests for branch naming, ref validation, and slug generation.

Pure functions, so these run without touching the filesystem or the database.
"""

from __future__ import annotations

import pytest

from apps.git import worktree


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Add a login form", "codepilot/add-a-login-form"),
        ("  Fix   the ~~bug~~!  ", "codepilot/fix-the-bug"),
        ("Refactor src/main.py", "codepilot/refactor-src-main-py"),
        ("!!!", "codepilot/task"),
        ("", "codepilot/task"),
        ("Add support for OAuth", "codepilot/add-support-for-oauth"),
    ],
)
def test_branch_name_is_prefixed_and_slugified(prompt: str, expected: str) -> None:
    assert worktree.branch_name_for(prompt) == expected


def test_branch_name_discriminator_avoids_collisions() -> None:
    first = worktree.branch_name_for("Add a login form", discriminator="1")
    second = worktree.branch_name_for("Add a login form", discriminator="2")

    assert first != second
    assert first.startswith("codepilot/")


def test_branch_name_is_length_bounded() -> None:
    branch = worktree.branch_name_for("word " * 200)

    assert len(branch.encode()) <= worktree.MAX_BRANCH_LENGTH
    assert branch.startswith("codepilot/")


def test_branch_name_never_contains_a_path_traversal() -> None:
    branch = worktree.branch_name_for("../../etc/passwd")

    assert ".." not in branch
    assert not branch.startswith("/")
    assert worktree.is_valid_branch(branch)


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        " leading",
        "trailing ",
        "codepilot/../escape",
        "codepilot/has..dots",
        "codepilot/has space",
        "codepilot/has~tilde",
        "codepilot/has^caret",
        "codepilot/has:colon",
        "codepilot/has?question",
        "codepilot/has*star",
        "codepilot/has[bracket",
        "codepilot/has\\backslash",
        "codepilot/has@{brace",
        "codepilot//double",
        "codepilot/",
        "/codepilot",
        "codepilot/./here",
        "codepilot/name.lock",
        "codepilot/name.",
        "codepilot/\x01control",
        "x" * 200,
    ],
)
def test_invalid_branch_names_are_rejected(name: str) -> None:
    assert worktree.is_valid_branch(name) is False


@pytest.mark.parametrize(
    "name",
    ["main", "codepilot/add-a-login-form", "codepilot/feat/1-deep-nesting", "release-1.2"],
)
def test_valid_branch_names_are_accepted(name: str) -> None:
    assert worktree.is_valid_branch(name) is True


def test_slugify_falls_back_when_input_is_all_punctuation() -> None:
    assert worktree.slugify("...///...") == "task"


def test_slugify_strips_leading_and_trailing_separators() -> None:
    assert worktree.slugify("  --hello world--  ") == "hello-world"
