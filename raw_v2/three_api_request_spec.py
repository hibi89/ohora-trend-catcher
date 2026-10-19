"""Pure, credential-free Meta Business Discovery request specification.

Builds a logical request, NOT a URL or HTTP call. No tokens, network,
environment variables, or production database access.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

_FIELDS = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,thumbnail_url"
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_=-]{1,512}$")


@dataclass(frozen=True)
class RequestSpec:
    lane: str
    username: str
    limit: int
    media_fields: str
    after: str | None
    method: str = "GET"
    endpoint: str = "/{ig-user-id}"

    def as_safe_dict(self):
        return {"lane": self.lane, "username": self.username,
                "limit": self.limit, "media_fields": self.media_fields,
                "after_present": self.after is not None,
                "method": self.method, "endpoint": self.endpoint}


def build_request_spec(lane, username, *, after=None, limit=4):
    """Validate the intended query before any future authenticated adapter."""
    if lane not in ("A", "B", "C"):
        raise ValueError("Unknown lane")
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise ValueError("Invalid Instagram username")
    if type(limit) is not int or limit != 4:
        raise ValueError("Only the approved four-post page size is supported")
    if after is not None and (
        not isinstance(after, str) or not _CURSOR.fullmatch(after)
    ):
        raise ValueError("Invalid opaque cursor")
    return RequestSpec(lane, username, limit, _FIELDS, after)


def business_discovery_field(spec):
    """Build a field expression only; caller must supply a verified IG user ID.

    Never append credentials or an HTTP host. Never follow paging.next URLs.
    """
    if not isinstance(spec, RequestSpec):
        raise TypeError("Expected RequestSpec")
    fields = f"media.limit({spec.limit}){{{spec.media_fields}}}"
    if spec.after is not None:
        fields = f"media.after({spec.after}).limit({spec.limit}){{{spec.media_fields}}}"
    return f"business_discovery.username({spec.username}){{id,username,{fields}}}"
