"""Offline-only validation of the separately selected OHORA 50-account pilot.

The original 294-account roster and all operational stores are untouched.
This module neither loads credentials nor calls Meta.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import re

EXPECTED_COUNTS = {"US": 18, "KR": 16, "JP": 16}
_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}\Z")


class PilotRosterError(ValueError):
    pass


def load_p50_roster(path):
    """Read the exact pilot CSV and return validated collector-style identities."""
    source = Path(path)
    raw = source.read_bytes()
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"account_key", "username", "country"}
        if not required.issubset(reader.fieldnames or []):
            raise PilotRosterError("Pilot roster columns missing")
        rows = list(reader)
    if len(rows) != 50:
        raise PilotRosterError("Exactly 50 pilot accounts required")
    counts = {key: 0 for key in EXPECTED_COUNTS}
    keys, usernames, accounts = set(), set(), []
    for ordinal, row in enumerate(rows, 1):
        key = row["account_key"].strip()
        username = row["username"].strip().lstrip("@")
        country = row["country"].strip().upper()
        if (country not in counts or not key.startswith("P50_" + country + "_")
                or not _USERNAME.fullmatch(username)
                or key in keys or username.casefold() in usernames):
            raise PilotRosterError("Invalid or duplicate pilot identity")
        keys.add(key)
        usernames.add(username.casefold())
        counts[country] += 1
        accounts.append({
            "key": key,
            "account_key": key,
            "username": username,
            "input_username": row["username"],
            "country": country,
            "ordinal": ordinal,
            "input_profile_url": row.get("profile_url", ""),
        })
    if counts != EXPECTED_COUNTS:
        raise PilotRosterError("Pilot country counts mismatch")
    return accounts, hashlib.sha256(raw).hexdigest()


def validate_p50_roster(accounts):
    if not isinstance(accounts, (list, tuple)) or len(accounts) != 50:
        raise PilotRosterError("Exactly 50 pilot accounts required")
    counts = {key: 0 for key in EXPECTED_COUNTS}
    keys, usernames = set(), set()
    for ordinal, account in enumerate(accounts, 1):
        if not isinstance(account, dict):
            raise PilotRosterError("Invalid pilot account")
        key, username, country = account.get("key"), account.get("username"), account.get("country")
        if (type(account.get("ordinal")) is not int or account["ordinal"] != ordinal
                or not isinstance(key, str) or not isinstance(username, str)
                or country not in counts or not key.startswith("P50_" + country + "_")
                or not _USERNAME.fullmatch(username)
                or key in keys or username.casefold() in usernames):
            raise PilotRosterError("Invalid pilot account identity")
        keys.add(key)
        usernames.add(username.casefold())
        counts[country] += 1
    if counts != EXPECTED_COUNTS:
        raise PilotRosterError("Pilot country counts mismatch")
    return True
