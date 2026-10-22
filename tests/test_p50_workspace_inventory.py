import tempfile
import unittest
from pathlib import Path
from p50_workspace_inventory import inspect


class InventoryTests(unittest.TestCase):
    def test_missing_is_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p50"
            self.assertFalse(inspect(p)["exists"])
            self.assertFalse(p.exists())

    def test_nested_sidecars_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p50"
            (p / "2026-10-22").mkdir(parents=True)
            db = p / "2026-10-22" / "coordinator.sqlite"
            wal = p / "2026-10-22" / "coordinator.sqlite-wal"
            db.write_bytes(b"original")
            wal.write_bytes(b"wal")
            before = (db.read_bytes(), wal.read_bytes())
            self.assertEqual(len(inspect(p)["files"]), 2)
            self.assertEqual(before, (db.read_bytes(), wal.read_bytes()))

    def test_nested_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p50"
            root.mkdir()
            target = Path(d) / "external"
            target.mkdir()
            (target / "history.sqlite").write_bytes(b"secret")
            try:
                (root / "nested").symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink unsupported")
            self.assertEqual(inspect(root)["files"], [])

    def test_secret_file_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text("SECRET")
            (root / "p50_raw.sqlite").write_bytes(b"x")
            files = inspect(root)["files"]
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["relative_path"], "p50_raw.sqlite")

    def test_symlink_root_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink unsupported")
            self.assertEqual(inspect(link)["files"], [])
            self.assertIn("SYMLINK_ROOT_REFUSED", inspect(link)["warnings"])


if __name__ == "__main__":
    unittest.main()
