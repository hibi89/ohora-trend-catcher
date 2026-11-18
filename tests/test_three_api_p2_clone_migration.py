"""P2 clone-only schema migration safety checks. No operating DB touched."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_raw_writer import SCHEMA
from three_api_p2_clone_migration import migrate_explicit_clone


class P2CloneMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO accounts VALUES('KR_001')")
        self.db.execute("""INSERT INTO posts(post_id,account_key,caption)
                         VALUES('991','KR_001','must remain unchanged')""")

    def tearDown(self):
        self.db.close()

    def test_requires_explicit_clone_confirmation(self):
        with self.assertRaises(ValueError):
            migrate_explicit_clone(self.db)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='post_checks'").fetchone()[0],0)

    def test_existing_legacy_data_and_schema_are_preserved_idempotently(self):
        original_schema = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()[0]
        first = migrate_explicit_clone(self.db, clone_confirmed=True)
        self.assertEqual(first["status"], "applied")
        self.assertEqual(first["preserved_legacy_rows"]["posts"],1)
        self.assertEqual(self.db.execute(
            "SELECT caption FROM posts").fetchone()[0],"must remain unchanged")
        self.assertEqual(self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()[0],original_schema)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='post_checks'").fetchone()[0],1)
        again = migrate_explicit_clone(self.db, clone_confirmed=True)
        self.assertEqual(again["status"], "already_present")
        self.assertEqual(first["verified_legacy_sha256"],
                         again["verified_legacy_sha256"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM p2_schema_migrations").fetchone()[0],1)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_rejects_operating_filename_even_with_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            operating = sqlite3.connect(
                str(Path(temp)/"collection.sqlite"), isolation_level=None)
            try:
                operating.executescript(SCHEMA)
                with self.assertRaises(ValueError):
                    migrate_explicit_clone(operating,clone_confirmed=True)
            finally:
                operating.close()

    def test_rejects_generic_file_without_clone_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            other = sqlite3.connect(
                str(Path(temp)/"unreviewed.sqlite"), isolation_level=None)
            try:
                other.executescript(SCHEMA)
                with self.assertRaises(ValueError):
                    migrate_explicit_clone(other,clone_confirmed=True)
            finally:
                other.close()

    def test_explicit_named_disk_clone_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"ohora-p2-clone.sqlite"
            clone=sqlite3.connect(str(path),isolation_level=None)
            try:
                clone.executescript(SCHEMA)
                clone.execute("INSERT INTO accounts VALUES('KR_001')")
                clone.execute("""INSERT INTO posts(post_id,account_key,caption)
                            VALUES('887','KR_001','source retained')""")
                result=migrate_explicit_clone(clone,clone_confirmed=True)
                self.assertEqual(result["status"],"applied")
                self.assertEqual(clone.execute(
                    "SELECT caption FROM posts").fetchone()[0],"source retained")
                self.assertEqual(clone.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            finally:
                clone.close()


if __name__ == "__main__":
    unittest.main()
