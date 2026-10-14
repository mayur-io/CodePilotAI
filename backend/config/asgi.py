"""ASGI entrypoint for CodePilotAI.

SSE streaming is served over WSGI via ``StreamingHttpResponse``; ASGI is kept
available for a future Channels/WebSocket transport.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_asgi_application()
