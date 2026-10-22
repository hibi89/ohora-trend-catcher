"""Read-only inventory of P50 candidate workspaces; never selects a canonical DB."""
import argparse
import json
import os
from pathlib import Path

DB_NAMES = ("p50_raw.sqlite", "history.sqlite", "capture.sqlite", "coordinator.sqlite", "history_capture.sqlite")


def inspect(root):
    root = Path(root).expanduser()
    result = {"path": str(root), "exists": root.is_dir(), "symlink": root.is_symlink(), "files": [], "warnings": []}
    if root.is_symlink():
        result["warnings"].append("SYMLINK_ROOT_REFUSED")
        return result
    if not root.is_dir():
        result["warnings"].append("MISSING_ROOT")
        return result
    for base, dirs, files in os.walk(root, followlinks=False):
        base = Path(base)
        dirs[:] = [d for d in dirs if not (base / d).is_symlink()]
        for name in sorted(files):
            p = base / name
            if p.is_symlink():
                continue
            if any(name == db + suffix for db in DB_NAMES for suffix in ("", "-wal", "-shm")):
                result["files"].append({"relative_path": str(p.relative_to(root)), "bytes": p.stat().st_size})
    sqlite_main = [f["relative_path"] for f in result["files"]
                   if f["relative_path"].endswith(".sqlite")]
    sidecars = {f["relative_path"] for f in result["files"]
                if f["relative_path"].endswith(("-wal", "-shm"))}
    for db in sqlite_main:
        if db + "-wal" not in sidecars:
            result["warnings"].append("WAL_NOT_PRESENT_FOR:" + db)
    if sqlite_main:
        result["warnings"].append("READ_ONLY_INVENTORY_CANNOT_PROVE_SNAPSHOT_CONSISTENCY")
    if not result["files"]:
        result["warnings"].append("NO_RECOGNIZED_SQLITE_FILES")
    result["warnings"].append("METADATA_ONLY_NOT_CANONICAL_OR_WAL_PROOF")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True)
    args = parser.parse_args()
    print(json.dumps([inspect(p) for p in args.candidate], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
