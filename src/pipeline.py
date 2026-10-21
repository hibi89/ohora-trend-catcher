#!/usr/bin/env python3
"""OHORA P6 n8n tick entrypoint: strict, read-only integration preflight.

Do not interpret this bridge as a live Meta collector. The production three-lane
runner is not wired here; --live is deliberately rejected. No network, DB
creation, credentials reading, or writes occur in any invocation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = 1
COUNTRIES = ('KR',) * 100 + ('US',) * 101 + ('JP',) * 93
EXPECTED_ACCOUNTS = 294
MAX_FRONT_PAGE_JOBS = 882


class ConfigurationRequired(Exception):
    """Configuration is not ready. Details must not escape to n8n execution logs."""


def report(state: str, *, ok: bool = False) -> dict:
    return {
        'schema_version': SCHEMA_VERSION,
        'ok': ok,
        'state': state,
        'needs_human': not ok,
        'ready_at': None,
        'pages_committed': 0,
        'http_requests': 0,
    }


def _path(value: object) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ConfigurationRequired()
    path = Path(value)
    if '..' in path.parts:
        raise ConfigurationRequired()
    return path


def _official_roster(path: Path, checksum: object) -> None:
    if not path.is_file() or not isinstance(checksum, str) or len(checksum) != 64:
        raise ConfigurationRequired()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest().lower() != checksum.lower():
        raise ConfigurationRequired()
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'account_key', 'username', 'country'}.issubset(reader.fieldnames or []):
            raise ConfigurationRequired()
        rows = list(reader)
    if len(rows) != EXPECTED_ACCOUNTS or tuple(r['country'].strip() for r in rows) != COUNTRIES:
        raise ConfigurationRequired()
    keys = [r['account_key'].strip() for r in rows]
    usernames = [r['username'].strip().lstrip('@').casefold() for r in rows]
    if any(not x for x in keys + usernames) or len(set(keys)) != EXPECTED_ACCOUNTS or len(set(usernames)) != EXPECTED_ACCOUNTS:
        raise ConfigurationRequired()


def preflight(config_path: Path) -> None:
    if not config_path.is_file() or config_path.stat().st_size > 65536:
        raise ConfigurationRequired()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ConfigurationRequired()
    roster = _path(config.get('roster_csv'))
    data_dir = _path(config.get('data_dir'))
    credentials = config.get('credential_files')
    if not isinstance(credentials, dict) or set(credentials) != {'A', 'B', 'C'}:
        raise ConfigurationRequired()
    # Paths only: never read credentials or expose their values through n8n.
    for lane in ('A', 'B', 'C'):
        file = _path(credentials[lane])
        if not file.is_file():
            raise ConfigurationRequired()
    if not data_dir.is_dir():
        raise ConfigurationRequired()
    _official_roster(roster, config.get('roster_sha256'))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='P6 read-only n8n bridge preflight')
    parser.add_argument('--config', required=True)
    parser.add_argument('--tick', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--max-pages', type=int, required=True)
    parser.add_argument('--max-seconds', type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    outcome = report('CONFIG_REQUIRED')
    try:
        if not args.tick or not args.json or not 1 <= args.max_pages <= MAX_FRONT_PAGE_JOBS or not 1 <= args.max_seconds <= 240:
            raise ConfigurationRequired()
        if args.live:
            # Fail closed even with all three credentials and explicit switches.
            # The audited production transport/commit executor is NOT yet connected.
            raise ConfigurationRequired()
        preflight(_path(args.config))
        outcome = report('DRY_RUN', ok=True)
    except Exception:
        # Intentionally no exception message: no path, cursor, or credentials leak.
        pass
    print(json.dumps(outcome, separators=(',', ':'), ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
