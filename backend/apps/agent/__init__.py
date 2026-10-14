"""LangGraph agent core.

Deliberately split into layers that can be tested without a model or a network:

``guards``      path sandbox + secret-file policy (pure functions)
``context``     repo map, instruction files, toolchain detection (filesystem)
``tools``       the read-only tool surface the model is allowed to call
``prompts``     the anti-slop contract and the per-phase prompt builders
``budget``      token and wall-clock limits
``providers``   the Groq transport and gpt-oss message normalization

Nothing in here writes to the working tree. Writing happens only through the
approval gate in :mod:`apps.agent_tasks`, which applies diffs via
:mod:`apps.git.patch`.
"""

from __future__ import annotations
