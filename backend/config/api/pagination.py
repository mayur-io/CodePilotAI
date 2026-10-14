"""Pagination classes.

Mobile clients should never render an unbounded list, so the default is
page-number based with a client-settable (and server-clamped) page size.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

MAX_PAGE_SIZE = 100


class DefaultPagination(PageNumberPagination):
    """Page-number pagination with a clamped, client-controllable page size."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def get_paginated_response(self, data: Any) -> Response:
        """Return a stable, explicitly-typed envelope the Android client can rely on."""
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("page", self.page.number),
                    ("page_size", self.get_page_size(self.request)),
                    ("total_pages", self.page.paginator.num_pages),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )
