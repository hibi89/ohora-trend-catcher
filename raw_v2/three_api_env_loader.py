"""Offline-only loader for the three local P6/RAW credential files.

The files use RAW's META_* names. This module maps those values to the P6
process environment names in memory and never makes HTTP requests.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LANES = ("A", "B", "C")
KEYS = ("META_ACCESS_TOKEN", "META_IG_USER_ID", "META_GRAPH_VERSION")
GRAPH_VERSION = "v26.0"


class CredentialConfigurationError(ValueError):
    """Sanitized configuration error that never includes credential values."""


def _read_lane(path: Path, lane: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise CredentialConfigurationError(
            f"Lane {lane} credential file is missing or not a regular file."
        )
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise CredentialConfigurationError(
            f"Lane {lane} credential file cannot be read as UTF-8."
        ) from None

    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise CredentialConfigurationError(
                f"Lane {lane} credential file has an invalid line."
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in KEYS:
            raise CredentialConfigurationError(
                f"Lane {lane} credential file contains an unsupported key."
            )
        if key in values:
            raise CredentialConfigurationError(
                f"Lane {lane} credential file contains a duplicate key."
            )
        if value.startswith(("'", '"')) or value.endswith(("'", '"')):
            if len(value) < 2 or value[0] != value[-1] or value[0] not in ("'", '"'):
                raise CredentialConfigurationError(
                    f"Lane {lane} credential file has invalid quoting."
                )
            value = value[1:-1]
        values[key] = value

    if set(values) != set(KEYS):
        raise CredentialConfigurationError(
            f"Lane {lane} credential file must contain all three required keys."
        )
    token = values["META_ACCESS_TOKEN"]
    caller = values["META_IG_USER_ID"]
    version = values["META_GRAPH_VERSION"]
    if (not 1 <= len(token) <= 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in token)):
        raise CredentialConfigurationError(
            f"Lane {lane} access token is blank or has an invalid shape."
        )
    if not re.fullmatch(r"[0-9]{1,64}", caller):
        raise CredentialConfigurationError(
            f"Lane {lane} Instagram caller ID is blank or has an invalid shape."
        )
    if version != GRAPH_VERSION:
        raise CredentialConfigurationError(
            f"Lane {lane} Graph API version must be {GRAPH_VERSION}."
        )
    return values


def load_three_api_environment(directory, *, base_environment=None) -> dict[str, str]:
    """Read .env.api_a/b/c and map values to P6_META_* names in memory.

    No values are printed or persisted. Tokens and caller IDs must each be
    unique across A/B/C. This validates local structure only, not Meta validity,
    ownership, permission grants, or quota state.
    """
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise CredentialConfigurationError(
            "Credential directory is missing or is not a regular directory."
        )

    lane_values = {}
    for lane in LANES:
        path = root / f".env.api_{lane.lower()}"
        lane_values[lane] = _read_lane(path, lane)

    tokens = [lane_values[lane]["META_ACCESS_TOKEN"] for lane in LANES]
    callers = [lane_values[lane]["META_IG_USER_ID"] for lane in LANES]
    versions = [lane_values[lane]["META_GRAPH_VERSION"] for lane in LANES]
    if len(set(tokens)) != len(LANES):
        raise CredentialConfigurationError("A/B/C access tokens must be unique.")
    if len(set(callers)) != len(LANES):
        raise CredentialConfigurationError("A/B/C caller IDs must be unique.")
    if len(set(versions)) != 1:
        raise CredentialConfigurationError("A/B/C Graph API versions must match.")

    environment = dict(base_environment or {})
    for lane in LANES:
        environment[f"P6_META_TOKEN_{lane}"] = lane_values[lane]["META_ACCESS_TOKEN"]
        environment[f"P6_META_IG_ID_{lane}"] = lane_values[lane]["META_IG_USER_ID"]
    environment["P6_META_GRAPH_VERSION"] = versions[0]
    return environment


def print_local_caller_matches(directory, caller_id: str) -> None:
    """Offline comparison of a known Instagram caller ID to A/B/C.

    This does not identify a Meta APP ID. No credential or caller ID is printed.
    """
    if not re.fullmatch(r"[0-9]{1,64}", caller_id):
        raise CredentialConfigurationError("Expected numeric Instagram caller ID.")
    root = Path(directory)
    for lane in LANES:
        values = _read_lane(root / f".env.api_{lane.lower()}", lane)
        print(f"{lane}: caller_match={values['META_IG_USER_ID'] == caller_id}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--match-ig-caller", metavar="IG_USER_ID", help="Compare known Instagram caller ID offline; NOT a Meta app ID")
    options = parser.parse_args(argv)
    try:
        load_three_api_environment(options.directory)
        if options.match_ig_caller is not None:
            print_local_caller_matches(options.directory, options.match_ig_caller)
    except CredentialConfigurationError as exc:
        print(f"INVALID: {exc}")
        return 2
    print(
        "VALID: three unique A/B/C configurations; Graph API v26.0; "
        "Meta calls=0; values=redacted"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())