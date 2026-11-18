"""Offline-only comparison of P6 process credentials and RAW B/C env files.

This module reads only the explicitly named .env.api_b and .env.api_c files.
It never reads .env.local, prints token/ID values, imports an HTTP client, or
contacts Meta. Local shape checks cannot establish that a token is active or
has the required Meta permissions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

LANES = ("B", "C")
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}\Z")
_ID_RE = re.compile(r"[0-9]{1,32}\Z")
_P6_VERSION_RE = re.compile(r"v[0-9]{1,3}\.[0-9]{1,2}\Z")
_RAW_VERSION_RE = re.compile(r"v\d+\.\d+\Z")
_DEFAULT_VERSION = "v26.0"


def _parse_env(path: Path) -> tuple[dict[str, str], str]:
    """Read one explicitly named RAW env file without exposing its contents."""
    if path.is_symlink():
        return {}, "symlink_refused"
    if not path.is_file():
        return {}, "missing"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return {}, "unreadable"

    settings: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            settings[key] = value.strip().strip("\"'")
    return settings, "readable"


def _configured(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _matches(left: object, right: object) -> bool | None:
    if not _configured(left) or not _configured(right):
        return None
    return left == right


def _distinct(left: object, right: object) -> bool | None:
    if not _configured(left) or not _configured(right):
        return None
    return left != right


def _lane_report(lane: str, environment: dict[str, str], raw_env_dir: Path) -> dict:
    raw_path = raw_env_dir / f".env.api_{lane.lower()}"
    raw, raw_file_status = _parse_env(raw_path)

    p6_token = environment.get(f"P6_META_TOKEN_{lane}")
    p6_caller = environment.get(f"P6_META_IG_ID_{lane}")
    raw_token = raw.get("META_ACCESS_TOKEN")
    raw_caller = raw.get("META_IG_USER_ID")

    p6_version = environment.get("P6_META_GRAPH_VERSION", _DEFAULT_VERSION)
    raw_version = raw.get("META_GRAPH_VERSION", _DEFAULT_VERSION)

    p6_token_ok = isinstance(p6_token, str) and _TOKEN_RE.fullmatch(p6_token) is not None
    p6_caller_ok = isinstance(p6_caller, str) and _ID_RE.fullmatch(p6_caller) is not None
    raw_token_ok = _configured(raw_token) and _TOKEN_RE.fullmatch(raw_token) is not None
    raw_caller_ok = isinstance(raw_caller, str) and _ID_RE.fullmatch(raw_caller) is not None
    p6_version_ok = isinstance(p6_version, str) and _P6_VERSION_RE.fullmatch(p6_version) is not None
    raw_version_ok = isinstance(raw_version, str) and _RAW_VERSION_RE.fullmatch(raw_version) is not None

    token_match = _matches(p6_token, raw_token)
    caller_match = _matches(p6_caller, raw_caller)
    version_match = _matches(p6_version, raw_version)

    if raw_file_status != "readable":
        comparison = "unavailable"
    elif token_match is None or caller_match is None:
        comparison = "unavailable"
    elif not (p6_token_ok and p6_caller_ok and raw_token_ok and raw_caller_ok
              and p6_version_ok and raw_version_ok):
        comparison = "invalid_format"
    elif token_match and caller_match and version_match:
        comparison = "match"
    else:
        comparison = "mismatch"

    return {
        "raw_file_status": raw_file_status,
        "p6_token_configured": _configured(p6_token),
        "p6_token_shape_ok": p6_token_ok,
        "p6_caller_id_configured": _configured(p6_caller),
        "p6_caller_id_shape_ok": p6_caller_ok,
        "raw_token_configured": _configured(raw_token),
        "raw_token_shape_ok": raw_token_ok,
        "raw_caller_id_configured": _configured(raw_caller),
        "raw_caller_id_shape_ok": raw_caller_ok,
        "p6_graph_version_shape_ok": p6_version_ok,
        "raw_graph_version_shape_ok": raw_version_ok,
        "token_exact_match": token_match,
        "caller_id_exact_match": caller_match,
        "graph_version_match": version_match,
        "configuration_comparison": comparison,
        "active_token_or_permissions": "not_checked_offline",
    }


def diagnose_credentials(*, environment: dict[str, str] | None = None,
                         raw_env_dir: str | Path = ".") -> dict:
    """Compare the P6 B/C process environment with RAW .env.api_b/.env.api_c."""
    env = os.environ if environment is None else environment
    directory = Path(raw_env_dir)
    return {
        "mode": "OFFLINE_CREDENTIAL_CONFIG_COMPARISON",
        "live_meta_called": False,
        "secret_values_emitted": False,
        "scope": "B/C only",
        "p6_b_c_token_values_distinct": _distinct(
            env.get("P6_META_TOKEN_B"), env.get("P6_META_TOKEN_C"),
        ),
        "p6_b_c_caller_ids_distinct": _distinct(
            env.get("P6_META_IG_ID_B"), env.get("P6_META_IG_ID_C"),
        ),
        "lanes": {
            lane: _lane_report(lane, env, directory)
            for lane in LANES
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-env-dir", default=".",
        help="directory containing local .env.api_b and .env.api_c files",
    )
    options = parser.parse_args(argv)
    print(json.dumps(
        diagnose_credentials(raw_env_dir=options.raw_env_dir),
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
