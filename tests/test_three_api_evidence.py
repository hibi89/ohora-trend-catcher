"""Offline transactional page evidence tests."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore


def response(after=None):
    if after:
        return {"data": [{"id": "post"}],
                "paging": {"next": "https://graph.facebook.com/next",
                           "cursors": {"after": after}}}
    return {"data": [{"id": "post"}]}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = Coordinator(Path(self.tmp.name) / "state.sqlite")
        self.c.seed()
        self.e = PageEvidenceStore(self.c)

    def test_commit_and_predecessor_cursor(self):
        job = self.c.lease("A")
        self.assertEqual((job["account_ordinal"], job["page"]), (1, 1))
        edge = self.e.commit_page(job["id"], job["lease_id"], "stored-1", response("edge1"))
        self.assertEqual(edge["next_cursor"], "edge1")
        self.assertEqual(self.e.required_cursor(1, 2), "edge1")

    def test_uncommitted_predecessor_rejected(self):
        with self.assertRaises(ValueError):
            self.e.required_cursor(1, 2)

    def test_stale_lease_rejected(self):
        job = self.c.lease("A")
        with self.assertRaises(ValueError):
            self.e.commit_page(job["id"], "wrong", "stored-1", response("edge1"))
        self.assertEqual(self.c.db.execute("SELECT state FROM jobs WHERE id=?",
                         (job["id"],)).fetchone()[0], "leased")

    def test_bad_page_response_rolls_back(self):
        job = self.c.lease("A")
        with self.assertRaises(ValueError):
            self.e.commit_page(job["id"], job["lease_id"], "stored-1",
                               {"data": [], "paging": {"next": "url"}})
        self.assertEqual(self.c.db.execute("SELECT COUNT(*) FROM page_evidence")
                         .fetchone()[0], 0)

    def test_duplicate_receipt_commit_rejected(self):
        job = self.c.lease("A")
        self.e.commit_page(job["id"], job["lease_id"], "stored-1", response("edge1"))
        with self.assertRaises(ValueError):
            self.e.commit_page(job["id"], job["lease_id"], "stored-1", response("edge1"))


if __name__ == "__main__":
    unittest.main()
