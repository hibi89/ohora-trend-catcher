"""Credential-safe, pre-normalization HTTP response evidence (P1).

This is a SAFE retained projection of the received response, not the original
byte-identical HTTP payload. It preserves unknown nonsensitive fields so that
future parsers can be repaired without another Meta request. It does not send
HTTP, inspect the filesystem, or persist arbitrary credentials.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping

SANITIZER_VERSION = "p6-response-evidence-v1"
_MAX_EVIDENCE_BYTES = 512 * 1024
_MAX_DEPTH = 16
_MAX_ITEMS = 15000
_SENSITIVE_KEY = re.compile(
    r"(access[_-]?token|refresh[_-]?token|authorization|client[_-]?secret|"
    r"app[_-]?secret|password|passwd|cookie|session[_-]?key|"
    r"secret|bearer|headers|set[_-]?cookie)", re.IGNORECASE)
_SENSITIVE_VALUE = re.compile(
    r"(access_token\s*=|client_secret\s*=|app_secret\s*=|"
    r"authorization\s*:|bearer\s+[a-zA-Z0-9._~-]{8,})", re.IGNORECASE)


class UnsafeResponseEvidence(ValueError):
    """No raw value or secret is included in the error message."""


def prepare_safe_response(payload):
    """Return (sanitized evidence tree, redacted field paths).

    Security-keyed objects are removed; paging.next URLs are replaced by a
    harmless marker, while opaque after cursors and media signed URLs survive.
    Unrecognized *safe* fields remain intact. Suspicious value strings result
    in evidence_unavailable rather than a risky partial original payload.
    """
    if not isinstance(payload, Mapping):
        raise UnsafeResponseEvidence("Response object unavailable")
    paths = []
    seen = [0]

    def visit(value, path, depth):
        seen[0] += 1
        if seen[0] > _MAX_ITEMS or depth > _MAX_DEPTH:
            raise UnsafeResponseEvidence("Response evidence exceeds safety bounds")
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise UnsafeResponseEvidence("Unsafe response field name")
                here = path + "." + key if path else key
                if _SENSITIVE_KEY.search(key):
                    paths.append(here)
                    continue
                if key == "next" and path.rsplit(".", 1)[-1] == "paging":
                    if item:
                        result["next"] = "sanitized-next-present"
                    paths.append(here)
                    continue
                result[key] = visit(item, here, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            return [visit(item, path + "[]", depth + 1) for item in value]
        if isinstance(value, str):
            if len(value) > 8192 or _SENSITIVE_VALUE.search(value):
                raise UnsafeResponseEvidence("Unsafe response field value")
            return value
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        raise UnsafeResponseEvidence("Unsupported response evidence value")

    safe = visit(payload, "", 0)
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise UnsafeResponseEvidence("Response evidence too large")
    return safe, sorted(set(paths))


def evidence_digest(safe_payload):
    encoded = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
