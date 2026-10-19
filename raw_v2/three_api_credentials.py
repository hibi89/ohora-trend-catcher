"""Pure offline validation of per-lane credential references.

Never reads token values or .env files and never invokes HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_ID = re.compile(r"^[0-9]{1,32}$")


@dataclass(frozen=True)
class CredentialReference:
    lane: str
    token_env_name: str
    ig_user_id_env_name: str

    def public_summary(self) -> dict:
        return {"lane": self.lane, "token_configured": False,
                "identity_configured": False, "mode": "REFERENCE_ONLY_NO_SECRET_READ"}


def make_references(mapping: dict) -> dict[str, CredentialReference]:
    """Require distinct, explicit A/B/C env-var names (not their secret values)."""
    if not isinstance(mapping, dict) or set(mapping) != {"A", "B", "C"}:
        raise ValueError("Exactly A/B/C reference mappings are required")
    names = []
    result = {}
    for lane in ("A", "B", "C"):
        item = mapping[lane]
        if not isinstance(item, dict) or set(item) != {"token_env_name", "ig_user_id_env_name"}:
            raise ValueError("Invalid credential reference entry")
        token_name, id_name = item["token_env_name"], item["ig_user_id_env_name"]
        for name in (token_name, id_name):
            if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
                raise ValueError("Invalid environment variable name")
            names.append(name)
        if token_name == id_name:
            raise ValueError("Token and identity references must differ")
        result[lane] = CredentialReference(lane, token_name, id_name)
    if len(set(names)) != 6:
        raise ValueError("Shared reference names are forbidden until quota isolation is verified")
    return result


def validate_identity_value(value: str) -> bool:
    """Check IG user ID format without persisting/printing its value."""
    return isinstance(value, str) and bool(_ID.fullmatch(value))
