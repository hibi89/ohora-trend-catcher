"""Offline three-API ordered staging -> daily proof -> RAW mirror regression."""
from datetime import datetime,timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore
from three_api_daily_gap import DailyGapLedger
from three_api_daily_projection import DailyEvidenceProjector
from three_api_raw_mirror import OfflineRawMirror
from three_api_raw_projection import OfflineRawEvidenceProjector, OfflineRawProjectionError

NOW=datetime(2026,10,19,9,0,tzinfo=timezone.utc)
OBS="2026-10-19T18:02:00+09:00"
DAY="2026-10-19"

def accounts():
    return [{"ordinal":i,"key":f"KR_{i:03}","username":f"nails_{i}"}
            for i in range(1,295)]


class RawProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.c=Coordinator(root/"coordinator.sqlite")
        self.addCleanup(self.c.db.close)
        self.e=PageEvidenceStore(self.c)
        self.l=DailyGapLedger(root/"gap.sqlite")
        self.addCleanup(self.l.close)
        self.daily=DailyEvidenceProjector(self.c,self.l,accounts(),DAY)
        self.m=OfflineRawMirror(root/"three_api_raw_mirror.sqlite")
        self.addCleanup(self.m.close)
        self.projector=OfflineRawEvidenceProjector(self.daily,self.m)
        self.c.db.execute(
            "INSERT INTO jobs(account_ordinal,page,lane,phase) VALUES (1,1,'A','front')"
        )

    def save_page(self,posts=None, next_cursor="page_2", page=1):
        if posts is None:
            posts=[{"id":"101","caption":"synthetic", "like_count":3,
                    "comments_count":0, "media_type":"IMAGE"}]
        if page==2:
            self.c.db.execute(
                "INSERT INTO jobs(account_ordinal,page,lane,phase) "
                "VALUES (1,2,'C','history')"
            )
        lane="A" if page==1 else "C"
        job=self.c.lease(lane,now=NOW)
        request=None if page==1 else "page_2"
        payload={"data":posts}
        if next_cursor:
            payload["paging"]={"next":"offline-next-page",
                               "cursors":{"after":next_cursor}}
        self.e.commit_staged_page(job["id"],job["lease_id"],payload,
                                  requested_cursor=request,now=NOW)
        self.daily.project_account(1)
        return job["id"]

    def test_front_payload_into_mirror(self):
        self.save_page()
        result=self.projector.project_page(1,1,observed_at=OBS)
        self.assertEqual((result["status"],result["records"]),("committed",1))
        self.assertEqual(self.m.summary()["raw_posts"],1)
        row=self.m.db.execute("SELECT like_count,comments_count FROM raw_post_observations").fetchone()
        self.assertEqual(tuple(row),(3,0))

    def test_missing_capture_time_is_blocked(self):
        self.save_page()
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1)
        self.assertEqual(self.m.summary()["page_receipts"],0)

    def test_crash_after_stage_restarts_without_new_timestamp(self):
        self.save_page()
        self.assertEqual(self.projector.project_page(1,1,observed_at=OBS,stage_only=True)["status"],"staged")
        self.m.close()
        self.m=OfflineRawMirror(self.m.path)
        self.projector=OfflineRawEvidenceProjector(self.daily,self.m)
        self.assertEqual(self.projector.project_page(1,1)["status"],"committed")
        self.assertEqual(self.m.summary()["raw_post_observations"],1)

    def test_full_restart_reconcile_without_new_http(self):
        self.save_page()
        self.projector.project_page(1,1,observed_at=OBS,stage_only=True)
        self.m.close()
        self.m=OfflineRawMirror(self.m.path)
        self.projector=OfflineRawEvidenceProjector(self.daily,self.m)
        self.assertEqual(self.projector.reconcile_all()[0]["status"],"committed")
        self.assertEqual(self.projector.reconcile_all()[0]["status"],"already_committed")
        self.assertEqual(self.m.summary()["raw_posts"],1)

    def test_missing_daily_id_proof_fails_closed(self):
        self.c.db.execute("DELETE FROM jobs")
        self.save_page()
        self.l.db.execute("DELETE FROM page_proof")
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at=OBS)
        self.assertEqual(self.m.summary()["page_receipts"],0)

    def test_uncommitted_source_or_invalid_page_rejected(self):
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at=OBS)
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,4,observed_at=OBS)
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(0,1,observed_at=OBS)

    def test_modified_original_staging_payload_detected(self):
        jobid=self.save_page()
        self.c.db.execute(
            "UPDATE staging_posts SET payload_json=? WHERE job_id=?",
            ('{"id":"101","caption":"tampered"}',jobid)
        )
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at=OBS)
        self.assertEqual(self.m.summary()["raw_posts"],0)

    def test_missing_or_changed_position_proof_detected(self):
        self.save_page([{"id":"102"},{"id":"101"}])
        self.c.db.execute("UPDATE staging_post_order SET position=3 WHERE position=1")
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at=OBS)

    def test_ledger_cursor_drift_detected(self):
        self.save_page()
        self.l.db.execute("UPDATE page_proof SET next_cursor='wrong'")
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at=OBS)

    def test_ledger_order_drift_detected(self):
        self.save_page([{"id":"102"},{"id":"101"}])
        self.l.db.execute("UPDATE page_proof SET media_ids_json=?",('["101","102"]',))
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at=OBS)

    def test_replay_does_not_duplicate_observation(self):
        self.save_page()
        self.assertEqual(self.projector.project_page(1,1,observed_at=OBS)["status"],"committed")
        self.assertEqual(self.projector.project_page(1,1)["status"],"already_committed")
        self.assertEqual(self.m.summary()["raw_post_observations"],1)

    def test_replay_with_different_observation_time_rejected(self):
        self.save_page()
        self.projector.project_page(1,1,observed_at=OBS)
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,1,observed_at="2026-10-20T01:00:00+09:00")

    def test_mirror_filters_extraneous_secret(self):
        self.save_page([{"id":"101","access_token":"SHOULD_NOT_STORE",
                         "caption":"safe", "like_count":0}])
        self.projector.project_page(1,1,observed_at=OBS)
        self.assertNotIn("SHOULD_NOT_STORE","\n".join(self.m.db.iterdump()))

    def test_two_front_pages_reconciled_and_order_preserved(self):
        self.save_page([{"id":"110"},{"id":"109"}],next_cursor="page_2")
        self.save_page([{"id":"108"},{"id":"107"}],next_cursor=None,page=2)
        self.assertEqual(len(self.projector.reconcile_all({(1,1):OBS,(1,2):OBS})),2)
        self.assertEqual(self.m.summary()["raw_posts"],4)
        self.assertEqual(self.m.summary()["raw_post_observations"],4)
        self.assertEqual(len(self.projector.reconcile_all()),2)

    def test_catchup_fourth_page_cannot_be_fabricated(self):
        self.save_page()
        with self.assertRaises(OfflineRawProjectionError):
            self.projector.project_page(1,4,observed_at=OBS)

    def test_separate_dbs_enforced(self):
        # No local production DB file can be treated as a synthetic mirror.
        with self.assertRaises(Exception):
            OfflineRawMirror(Path(self.tmp.name)/"collection.sqlite")

    def test_snapshot_binding_mismatch_is_blocked(self):
        self.save_page()
        self.c.db.execute("UPDATE daily_snapshot_binding SET day_kst='2026-10-20'")
        with self.assertRaises(Exception):
            self.projector.project_page(1,1,observed_at=OBS)

if __name__=="__main__":
    unittest.main()
