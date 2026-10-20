"""Pure, offline Meta Business Discovery response normalization.

No HTTP, tokens, credentials or database writes. Only allowlisted media
fields are retained; paging.next URL is replaced with a harmless sentinel.

Carousel children are an explicitly supplied, bounded nested payload, not
automatically collected by this module. Missing children means UNKNOWN
coverage, not proof that a carousel has zero items. Paged/incomplete child
edges are rejected instead of silently treating a partial list as complete.
"""
from __future__ import annotations

import re

from three_api_paging import validate_page_edge

FIELDS = ("id", "caption", "media_type", "media_url", "permalink",
          "timestamp", "like_count", "comments_count", "thumbnail_url")
CHILD_FIELDS = ("id", "media_type", "media_url", "thumbnail_url")
_MEDIA_ID = re.compile(r"[0-9]{1,64}\Z")
_MAX_CHILDREN = 20


def _normalize_children(post, *, allow_partial=False):
    """Return allowlisted children in original order, or None if not supplied."""
    if "children" not in post:
        return None
    if post.get("media_type") != "CAROUSEL_ALBUM":
        raise ValueError("Nested child media require a carousel parent")
    children = post["children"]
    if not isinstance(children, dict) or not isinstance(children.get("data"), list):
        raise ValueError("Invalid nested carousel children envelope")
    items = children["data"]
    if len(items) > _MAX_CHILDREN:
        raise ValueError("Too many nested carousel children")
    paging = children.get("paging", {})
    if not isinstance(paging, dict):
        raise ValueError("Invalid carousel child paging evidence")
    # Existing source/mirror callers intentionally reject partial pages:
    # only the new immutable Capture/RAW contract may opt into preservation.
    if (paging.get("next") or paging.get("has_next") is True) and not allow_partial:
        raise ValueError("Partial carousel child page requires capture opt-in")
    seen = {post["id"]}
    normalized = []
    for child in items:
        if not isinstance(child, dict):
            raise ValueError("Invalid carousel child item")
        media_id = child.get("id")
        if not isinstance(media_id, str) or not _MEDIA_ID.fullmatch(media_id):
            raise ValueError("Invalid carousel child identifier")
        if media_id in seen:
            raise ValueError("Duplicate or parent-colliding carousel child")
        seen.add(media_id)
        record = {field: child[field] for field in CHILD_FIELDS if field in child}
        for field in ("media_type", "media_url", "thumbnail_url"):
            if field in record and (not isinstance(record[field], str)
                                    or len(record[field]) > 2048):
                raise ValueError("Invalid carousel child metadata")
        if "media_type" in record and record["media_type"] not in ("IMAGE", "VIDEO"):
            raise ValueError("Invalid carousel media type")
        normalized.append(record)
    return normalized


def normalize_meta_page(payload, *, requested_cursor=None, allow_partial_children=False):
    if not isinstance(payload, dict) or "error" in payload:
        raise ValueError("Meta error or invalid response")
    # Business Discovery may wrap media in business_discovery.media.
    media = payload.get("business_discovery", payload)
    if not isinstance(media, dict):
        raise ValueError("Missing media envelope")
    if "media" in media:
        media = media["media"]
    if not isinstance(media, dict) or not isinstance(media.get("data"), list):
        raise ValueError("Missing media data")
    posts = media["data"]
    if len(posts) > 4:
        raise ValueError("Response exceeds requested page limit")
    cleaned = []
    for post in posts:
        if not isinstance(post, dict):
            raise ValueError("Invalid media item")
        post_id = post.get("id")
        if not isinstance(post_id, str) or not post_id.strip():
            raise ValueError("Missing media id")
        for numeric in ("like_count", "comments_count"):
            if numeric in post and post[numeric] is not None and (type(post[numeric]) is not int or post[numeric] < 0):
                raise ValueError("Invalid engagement count")
        record = {key: post[key] for key in FIELDS if key in post}
        children = _normalize_children(post, allow_partial=allow_partial_children)
        if children is not None:
            record["children"] = children
        cleaned.append(record)
    paging = media.get("paging", {})
    if not isinstance(paging, dict):
        raise ValueError("Invalid paging")
    cursors = paging.get("cursors", {})
    if not isinstance(cursors, dict):
        raise ValueError("Invalid paging cursors")
    # Validate original structure first; do not persist paging.next query tokens.
    validate_page_edge({"data": cleaned, "paging": paging},
                       requested_cursor=requested_cursor)
    normalized = {"data": cleaned}
    if paging.get("next"):
        normalized["paging"] = {
            "next": "offline-next-page",
            "cursors": {"after": cursors["after"]},
        }
    return normalized
