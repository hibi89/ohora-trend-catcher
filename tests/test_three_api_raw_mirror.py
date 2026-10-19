"""Synthetic, isolated RAW-like persistence and restart regression."""
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_raw_mirror import OfflineRawMirror, OfflineRawMirrorError

OBSERVED = "2026-10-19T17:33:00+09:00"


def post(pid="101", **kwargs):
    return {"id": pid, "caption": "first synthetic caption", **kwargs}


class OfflineRawMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "three_api_raw_mirror.sqlite"
        self.mirror = OfflineRawMirror(self.path)
        self.addCleanup(self.mirror.close)

    def stage(self, request="A_KR_001_p1", account="KR_001",
              posts=None, observed=OBSERVED):
        return self.mirror.stage_page(
            request, account, observed, [post()] if posts is None else posts
        )

    def test_stage_durable_before_promotion(self):
        self.assertEqual(self.stage()["status"], "staged")
        self.assertEqual(self.mirror.summary()["raw_posts"], 0)
        self.assertEqual(self.mirror.summary()["pending_pages"], 1)

    def test_atomic_parent_child_and_observation(self):
        page = [post("101", like_count=0, comments_count=4, media_type="CAROUSEL_ALBUM",
                     children=[{"id":"102","media_type":"IMAGE","media_url":"https://example.test/img"}])]
        self.stage(posts=page)
        self.assertEqual(self.mirror.promote_page("A_KR_001_p1")["status"], "committed")
        result = self.mirror.summary()
        self.assertEqual((result["raw_posts"],result["raw_post_media"],
                          result["raw_post_observations"], result["pending_pages"]),
                         (1,2,1,0))
        rows = self.mirror.db.execute(
            "SELECT media_role,item_order FROM raw_post_media ORDER BY item_order").fetchall()
        self.assertEqual([tuple(x) for x in rows], [("parent",0),("child",1)])

    def test_missing_likes_not_zero(self):
        self.stage(posts=[post(comments_count=0)])
        self.mirror.promote_page("A_KR_001_p1")
        row = self.mirror.db.execute(
            "SELECT like_count,like_present,comments_count,comments_present "
            "FROM raw_post_observations").fetchone()
        self.assertEqual(tuple(row), (None,0,0,1))

    def test_replay_identical_stage_and_promote_is_idempotent(self):
        self.stage()
        self.mirror.promote_page("A_KR_001_p1")
        self.assertEqual(self.stage()["status"],"committed")
        self.assertEqual(self.mirror.promote_page("A_KR_001_p1")["status"],
                         "already_committed")
        self.assertEqual(self.mirror.summary()["raw_post_observations"],1)

    def test_new_request_same_post_adds_observation_not_post(self):
        self.stage(posts=[post(like_count=3)])
        self.mirror.promote_page("A_KR_001_p1")
        self.stage("B_KR_001_p2", posts=[post(like_count=6,caption="new caption")],
                   observed="2026-10-20T01:00:00+09:00")
        self.mirror.promote_page("B_KR_001_p2")
        self.assertEqual(self.mirror.summary()["raw_posts"],1)
        self.assertEqual(self.mirror.summary()["raw_post_observations"],2)
        self.assertEqual(self.mirror.db.execute(
            "SELECT caption FROM raw_posts WHERE post_id='101'").fetchone()[0],
            "first synthetic caption")

    def test_conflicting_receipt_refused_without_data_loss(self):
        self.stage()
        before = self.mirror.summary().copy()
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post("102")])
        self.assertEqual(self.mirror.summary(), before)

    def test_conflicting_receipt_account_or_time_refused(self):
        self.stage()
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(account="US_001")
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(observed="2026-10-19T17:34:00+09:00")

    def test_restart_recovers_without_fixture(self):
        self.stage()
        self.mirror.close()
        self.mirror = OfflineRawMirror(self.path)
        self.assertEqual(self.mirror.reconcile_pending(),
                         [{"status":"committed","post_count":1}])
        self.assertEqual(self.mirror.summary()["pending_pages"],0)
        self.assertEqual(self.mirror.reconcile_pending(), [])

    def test_sqlite_trigger_failure_rolls_back_all_rows_and_receipt(self):
        self.stage(posts=[post("101"),post("102")])
        self.mirror.db.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON raw_posts "
            "WHEN NEW.post_id='102' BEGIN SELECT RAISE(ABORT,'fake failure'); END"
        )
        with self.assertRaises(OfflineRawMirrorError):
            self.mirror.promote_page("A_KR_001_p1")
        self.assertEqual(self.mirror.summary()["raw_posts"],0)
        self.assertEqual(self.mirror.summary()["raw_post_media"],0)
        self.assertEqual(self.mirror.summary()["raw_post_observations"],0)
        self.assertEqual(self.mirror.summary()["pending_pages"],1)
        self.mirror.db.execute("DROP TRIGGER fail_second")
        self.assertEqual(self.mirror.reconcile_pending()[0]["status"],"committed")

    def test_cross_account_post_takeover_atomic_rollback(self):
        self.stage()
        self.mirror.promote_page("A_KR_001_p1")
        self.stage("A_US_001_p1","US_001",[post("102"),post("101")])
        with self.assertRaises(OfflineRawMirrorError):
            self.mirror.promote_page("A_US_001_p1")
        self.assertEqual(self.mirror.summary()["raw_posts"],1)
        self.assertEqual(self.mirror.summary()["pending_pages"],1)

    def test_child_media_identity_collision_rolls_back(self):
        self.stage(posts=[post("101",children=[{"id":"110"}])])
        self.mirror.promote_page("A_KR_001_p1")
        self.stage("A_KR_001_p2",posts=[post("102",children=[{"id":"110"}])])
        with self.assertRaises(OfflineRawMirrorError):
            self.mirror.promote_page("A_KR_001_p2")
        self.assertEqual(self.mirror.summary()["raw_posts"],1)

    def test_extra_fields_and_secret_do_not_persist(self):
        self.stage(posts=[post(
            like_count=2, access_token="SECRET_TOKEN_XYZ",
            children=[{"id":"120","access_token":"SECRET_TOKEN_XYZ"}])])
        self.mirror.promote_page("A_KR_001_p1")
        dump = "\n".join(self.mirror.db.iterdump())
        self.assertNotIn("SECRET_TOKEN_XYZ",dump)

    def test_negative_invalid_counter_rejected(self):
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post(like_count=-1)])
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post(comments_count=True)])
        self.assertEqual(self.mirror.summary()["page_receipts"],0)

    def test_duplicate_post_or_child_id_rejected(self):
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post("101"),post("101")])
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post("101",children=[{"id":"101"}])])
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post("101",children=[{"id":"102"},{"id":"102"}])])

    def test_cross_page_media_duplicate_rejected(self):
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post("101"),post("102",children=[{"id":"101"}])])

    def test_page_size_limit(self):
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(posts=[post(str(i)) for i in range(1,6)])
        self.assertEqual(self.mirror.summary()["page_receipts"],0)

    def test_timezone_required_and_converted_to_utc(self):
        with self.assertRaises(OfflineRawMirrorError):
            self.stage(observed="2026-10-19T12:00:00")
        self.stage()
        self.assertEqual(self.mirror.db.execute(
            "SELECT observed_at FROM page_receipts").fetchone()[0],
            "2026-10-19T08:33:00+00:00")

    def test_existing_unmarked_database_refused_untouched(self):
        alien = Path(self.tmp.name)/"alien"/"three_api_raw_mirror.sqlite"
        alien.parent.mkdir()
        db = sqlite3.connect(alien)
        db.execute("CREATE TABLE unrelated (sentinel TEXT)")
        db.execute("INSERT INTO unrelated VALUES ('original')")
        db.commit()
        db.close()
        with self.assertRaises(OfflineRawMirrorError):
            OfflineRawMirror(alien)
        db = sqlite3.connect(alien)
        self.assertEqual(db.execute("SELECT sentinel FROM unrelated").fetchone()[0],
                         "original")
        self.assertEqual(db.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='page_receipts'").fetchone()[0],0)
        db.close()

    def test_refuse_collection_db_even_missing(self):
        with self.assertRaises(OfflineRawMirrorError):
            OfflineRawMirror(Path(self.tmp.name)/"collection.sqlite")
        self.assertFalse((Path(self.tmp.name)/"collection.sqlite").exists())

    def test_tampered_staged_receipt_detected_before_promotion(self):
        self.stage()
        self.mirror.db.execute(
            "UPDATE page_receipts SET payload_json=? WHERE request_id=?",
            ('[]',"A_KR_001_p1"))
        with self.assertRaises(OfflineRawMirrorError):
            self.mirror.promote_page("A_KR_001_p1")
        self.assertEqual(self.mirror.summary()["raw_posts"],0)
        self.assertEqual(self.mirror.summary()["pending_pages"],1)


if __name__ == "__main__":
    unittest.main()
