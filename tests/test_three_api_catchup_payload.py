"""Synthetic page4+ sealed payload, RAW mirror promotion, and restart replay."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_coordinator import Coordinator
from three_api_daily_gap import DailyGapLedger
from three_api_catchup_offline import OfflineCatchupRunner, OfflineCatchupError
from three_api_raw_mirror import OfflineRawMirror
from three_api_catchup_payload import OfflineCatchupPayloadProjector, OfflineCatchupPayloadError

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
TODAY="2026-10-19"

def page(ids, next_cursor=None, **fields):
    posts=[{"id":str(i),"caption":"fixture nail",**fields} for i in ids]
    result={"data":posts}
    if next_cursor:
        result["paging"]={"next":"https://graph.facebook.com/fixture",
                          "cursors":{"after":next_cursor}}
    return result


class CatchupPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.ledger=DailyGapLedger(root/"gap.sqlite")
        self.addCleanup(self.ledger.close)
        self.coordinator=Coordinator(root/"coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.ledger.seed_previous_head("KR_001","100",day="2026-10-18")
        self.ledger.begin_day("KR_001",TODAY)
        self.fixtures={("KR_001",TODAY,"p4"):page([103,102,101,100],like_count=0,
                                                       comments_count=3,media_type="IMAGE")}
        self.mirror=OfflineRawMirror(root/"three_api_raw_mirror.sqlite")
        self.addCleanup(lambda: self.mirror.close())
        self.front()
        self.runner=OfflineCatchupRunner(self.ledger,self.coordinator,
                                          ["KR_001"],self.fixtures)
        self.pipeline=OfflineCatchupPayloadProjector(self.runner,self.mirror)

    def front(self):
        for cursor,ids,nxt in (
            (None,[115,114,113,112],"p2"),
            ("p2",[111,110,109,108],"p3"),
            ("p3",[107,106,105,104],"p4"),
        ):
            self.ledger.record_page("KR_001",TODAY,phase="front",
                requested_cursor=cursor,post_ids=[str(i) for i in ids],
                next_cursor=nxt)

    def call(self,lane="A",stage_only=False):
        return self.pipeline.run_one(lane,TODAY,now=NOW,stage_only=stage_only)

    def test_full_allowlisted_payload_and_cursor_proof(self):
        result=self.call()
        self.assertEqual(result["status"],"applied")
        self.assertEqual(result["payload_projection"]["status"],"committed")
        self.assertEqual(self.mirror.summary()["raw_posts"],4)
        row=self.ledger.db.execute("SELECT * FROM catchup_inbox").fetchone()
        self.assertEqual(row["payload_version"],1)
        self.assertEqual(len(json.loads(row["payload_json"])["posts"]),4)
        self.assertEqual(row["observed_at"],NOW.isoformat())
        self.assertEqual(self.ledger.status("KR_001",TODAY)["status"],"verified")

    def test_likes_zero_and_missing_values_preserved(self):
        self.fixtures[("KR_001",TODAY,"p4")]=page([103,102,101,100])
        self.call()
        absent=self.mirror.db.execute(
            "SELECT like_count,like_present FROM raw_post_observations LIMIT 1"
        ).fetchone()
        self.assertEqual(tuple(absent),(None,0))

    def test_zero_is_distinct_from_missing(self):
        self.call()
        row=self.mirror.db.execute(
            "SELECT like_count,like_present,comments_count,comments_present "
            "FROM raw_post_observations LIMIT 1").fetchone()
        self.assertEqual(tuple(row),(0,1,3,1))

    def test_secrets_never_persist(self):
        self.fixtures[("KR_001",TODAY,"p4")]=page([103,102,101,100],
                                                    access_token="DO_NOT_PERSIST")
        self.call()
        dump="\n".join(self.ledger.db.iterdump())+"\n".join(self.mirror.db.iterdump())
        self.assertNotIn("DO_NOT_PERSIST",dump)

    def test_stage_interrupt_then_restart_without_fixture(self):
        result=self.call(stage_only=True)
        self.assertEqual(result["status"],"staged_unapplied")
        self.assertEqual(self.mirror.summary()["raw_posts"],0)
        self.fixtures.clear()
        reopened=OfflineCatchupRunner(self.ledger,self.coordinator,["KR_001"],{})
        self.pipeline=OfflineCatchupPayloadProjector(reopened,self.mirror)
        done=self.call("B")
        self.assertEqual(done["status"],"reconciled")
        self.assertEqual(done["payload_projection"]["status"],"committed")
        self.assertEqual(self.mirror.summary()["raw_posts"],4)

    def test_after_ledger_proof_before_ack_replay(self):
        self.call(stage_only=True)
        row=self.runner._pending()
        self.ledger.record_page("KR_001",TODAY,phase="catchup",
            requested_cursor=row["requested_cursor"],
            post_ids=json.loads(row["ids_json"]),next_cursor=row["next_cursor"])
        self.fixtures.clear()
        self.assertEqual(self.call("B")["status"],"reconciled")
        self.assertEqual(self.mirror.summary()["raw_posts"],4)

    def test_payload_tamper_blocks_ledger_advance(self):
        self.call(stage_only=True)
        self.ledger.db.execute("UPDATE catchup_inbox SET payload_json='{}'")
        with self.assertRaises(OfflineCatchupError):
            self.runner.reconcile_pending()
        self.assertEqual(self.ledger.status("KR_001",TODAY)["catchup_pages"],0)
        self.assertEqual(self.mirror.summary()["page_receipts"],0)

    def test_observation_time_tamper_blocks_replay(self):
        self.call(stage_only=True)
        self.ledger.db.execute(
            "UPDATE catchup_inbox SET observed_at='2026-10-20T10:00:00+00:00'"
        )
        with self.assertRaises(OfflineCatchupError):
            self.runner.reconcile_pending()
        self.assertEqual(self.ledger.status("KR_001",TODAY)["catchup_pages"],0)

    def test_id_swap_without_resealing_blocks_replay(self):
        self.call(stage_only=True)
        self.ledger.db.execute("UPDATE catchup_inbox SET ids_json='[\"999\"]'")
        with self.assertRaises(OfflineCatchupError):
            self.runner.reconcile_pending()

    def test_proof_cursor_conflict_blocks_mirror(self):
        self.runner.run_one("A",TODAY,now=NOW)
        self.ledger.db.execute(
            "UPDATE page_proof SET requested_cursor='other' WHERE phase='catchup'"
        )
        with self.assertRaises(OfflineCatchupPayloadError):
            self.pipeline.project_page("KR_001",TODAY,1)
        self.assertEqual(self.mirror.summary()["raw_posts"],0)

    def test_missing_committed_proof_blocks_mirror(self):
        self.runner.run_one("A",TODAY,now=NOW)
        self.ledger.db.execute("DELETE FROM page_proof WHERE phase='catchup'")
        with self.assertRaises(OfflineCatchupPayloadError):
            self.pipeline.project_page("KR_001",TODAY,1)

    def test_legacy_id_only_refuses_to_fabricate_payload(self):
        self.runner.run_one("A",TODAY,now=NOW)
        self.ledger.db.execute(
            "UPDATE catchup_inbox SET payload_version=0,payload_json=NULL,"
            "payload_sha256=NULL,observed_at=NULL"
        )
        with self.assertRaises(OfflineCatchupPayloadError):
            self.pipeline.project_page("KR_001",TODAY,1)
        self.assertEqual(self.pipeline.run_one("A",TODAY,now=NOW)["status"],
                         "payload_projection_blocked")
        self.assertEqual(self.mirror.summary()["raw_posts"],0)

    def test_pending_id_only_is_not_silently_upgraded(self):
        self.call(stage_only=True)
        self.ledger.db.execute(
            "UPDATE catchup_inbox SET payload_version=0,payload_json=NULL,"
            "payload_sha256=NULL,observed_at=NULL"
        )
        self.runner.reconcile_pending()
        with self.assertRaises(OfflineCatchupPayloadError):
            self.pipeline.project_page("KR_001",TODAY,1)

    def test_mirror_stage_crash_and_replay_no_duplicates(self):
        self.runner.run_one("A",TODAY,now=NOW)
        self.assertEqual(self.pipeline.project_page(
            "KR_001",TODAY,1,stage_only=True)["status"],"staged")
        self.mirror.close()
        self.mirror=OfflineRawMirror(self.mirror.path)
        self.pipeline=OfflineCatchupPayloadProjector(self.runner,self.mirror)
        self.assertEqual(self.pipeline.project_page("KR_001",TODAY,1)["status"],"committed")
        self.assertEqual(self.pipeline.project_page("KR_001",TODAY,1)["status"],
                         "already_committed")
        self.assertEqual(self.mirror.summary()["raw_post_observations"],4)

    def test_mirror_failure_blocks_new_capture_and_recovers(self):
        self.mirror.db.execute(
            "CREATE TRIGGER prevent_insert BEFORE INSERT ON raw_posts BEGIN "
            "SELECT RAISE(ABORT,'fixture failure'); END"
        )
        self.assertEqual(self.call()["status"],"payload_projection_blocked")
        self.assertEqual(self.ledger.status("KR_001",TODAY)["status"],"verified")
        self.assertEqual(self.mirror.summary()["raw_posts"],0)
        self.fixtures.clear()
        self.assertEqual(self.call("C")["status"],"payload_projection_blocked")
        self.mirror.db.execute("DROP TRIGGER prevent_insert")
        self.assertEqual(self.pipeline.reconcile_all()[0]["status"],"committed")
        self.assertEqual(self.mirror.summary()["raw_posts"],4)

    def test_multi_page_catchup_mirror_projection(self):
        self.fixtures[("KR_001",TODAY,"p4")]=page([103,102,101,99],"p5",
                                                   like_count=10)
        self.fixtures[("KR_001",TODAY,"p5")]=page([98,97,100],None,
                                                   like_count=12)
        self.assertEqual(self.call("A")["gap_status"],"catchup_pending")
        self.assertEqual(self.call("B")["gap_status"],"verified")
        self.assertEqual(self.mirror.summary()["raw_posts"],7)
        self.assertEqual(self.mirror.summary()["raw_post_observations"],7)
        self.assertEqual(len(self.pipeline.reconcile_all()),2)
        self.assertEqual(self.mirror.summary()["raw_post_observations"],7)

    def test_invalid_fourth_page_prevents_payload_stage(self):
        self.fixtures[("KR_001",TODAY,"p4")]=page([103,103,100])
        self.assertEqual(self.call()["status"],"invalid_offline_fixture")
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM catchup_inbox").fetchone()[0],0)

    def test_page_identity_replay_is_deterministic(self):
        self.call()
        receipt=self.mirror.db.execute(
            "SELECT request_id FROM page_receipts").fetchone()[0]
        self.assertTrue(receipt.startswith("catchup."))
        self.assertEqual(receipt,self.pipeline._receipt("KR_001",TODAY,1))
        self.assertEqual(len(receipt),72)

    def test_constructor_refuses_shared_mirror_database(self):
        with self.assertRaises(Exception):
            OfflineRawMirror(self.ledger.path)

    def test_projection_requires_applied_proof(self):
        self.call(stage_only=True)
        with self.assertRaises(OfflineCatchupPayloadError):
            self.pipeline.project_page("KR_001",TODAY,1)
        self.assertEqual(self.mirror.summary()["page_receipts"],0)


if __name__=="__main__":
    unittest.main()
