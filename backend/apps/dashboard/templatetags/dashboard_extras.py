"""Template filters for the operations dashboard."""

from __future__ import annotations

import json
from typing import Any

from django import template

register = template.Library()


@register.filter(name="prettyjson")
def prettyjson(value: Any) -> str:
    """Render a JSON-ish value as indented text for a ``<pre>`` block.

    Verification and diff-stat payloads have a loose, evolving shape, so the
    dashboard prints them verbatim rather than guessing at keys.
    """
    if value in (None, "", {}, []):
        return "—"
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
