"""Fail-closed offline validation of the official 294-account roster.

No Meta requests, secrets, or production database writes.
"""


class RosterPreflightError(ValueError):
    pass


def validate_official_roster(accounts):
    """Require 294 unique, ordered account identities before live dispatch."""
    if not isinstance(accounts, (list, tuple)) or len(accounts) != 294:
        raise RosterPreflightError("Official 294-account roster required")
    keys = set()
    usernames = set()
    for ordinal, account in enumerate(accounts, 1):
        if not isinstance(account, dict):
            raise RosterPreflightError("Invalid account row")
        index = account.get("ordinal", ordinal)
        if type(index) is not int or index != ordinal:
            raise RosterPreflightError("Official roster ordinal mismatch")
        key = account.get("key", account.get("account_key"))
        username = account.get("username")
        if (not isinstance(key, str) or not key.strip()
                or not isinstance(username, str) or not username.strip()):
            raise RosterPreflightError("Missing official account identity")
        if key in keys or username.casefold() in usernames:
            raise RosterPreflightError("Duplicate official account identity")
        keys.add(key)
        usernames.add(username.casefold())
    return True
