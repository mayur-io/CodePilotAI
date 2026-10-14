"""Local git plumbing: mirrors, worktrees, commits, and patch application.

This app owns every ``git`` invocation in the project. Nothing else shells out
directly, so credential handling, environment hardening, output scrubbing, and
timeout behaviour are defined in exactly one place (``apps.git.runner``).

Layout on disk, rooted at ``settings.WORKSPACE_ROOT``::

    mirrors/<owner>__<repo>.git/     bare mirror, one per repository
    worktrees/<task_id>/             one checkout per agent task
    scratch/<uuid>/                  per-process HOME, askpass helper, locks
"""

from __future__ import annotations
