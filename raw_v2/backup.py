#!/usr/bin/env python3
"""Create a consistent SQLite backup without changing the live OHORA RAW database."""
import argparse
from datetime import datetime,timezone
import hashlib
from pathlib import Path
import sqlite3


def backup_store(output_dir,backup_dir):
    source=Path(output_dir).resolve()/'collection.sqlite'
    if not source.is_file():raise ValueError('Existing collection.sqlite not found')
    destination=Path(backup_dir).resolve()
    if destination==source.parent or destination.is_relative_to(source.parent):
        raise ValueError('Save backups OUTSIDE the live output directory')
    destination.mkdir(parents=True,exist_ok=True)
    timestamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=destination/('collection_'+timestamp+'.sqlite')
    if target.exists():raise ValueError('Backup already exists; no overwrite')
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    try:
        with sqlite3.connect(target) as out:
            src.backup(out,pages=128)
            if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Backup failed integrity_check')
            original_count=src.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
            copied_count=out.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
            if original_count!=copied_count:raise ValueError('Backup post count mismatch')
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    print('BACKUP_CREATED',target)
    print('SHA256',digest)
    print('POSTS',copied_count)
    print('Important: retain original runs/raw JSON separately; the SQLite snapshot does not include it.')
    return target


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--backup-dir',type=Path,required=True)
    a=p.parse_args()
    try:backup_store(a.output_dir,a.backup_dir);return 0
    except (OSError,ValueError,sqlite3.Error) as e:
        print('BACKUP_FAILED',str(e));return 1


if __name__=='__main__':
    raise SystemExit(main())
