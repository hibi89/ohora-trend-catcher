"""Purely synthetic tests for crash-safe KST staging projection."""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_coordinator import Coordinator
from three_api_credentials import make_references
from three_api_daily_gap import DailyGapLedger
from three_api_daily_projection import (
    DailyEvidenceProjector, DailyGuardedSnapshot, DailyProjectionError,
)
from three_api_evidence import PageEvidenceStore
from three_api_graph_http import GraphHTTPClient

NOW=datetime(2026,10,19,tzinfo=timezone.utc)


def accounts():
    return [
        {"ordinal":i,"key":f"KR_{i:03}","username":f"nails_{i}"}
        for i in range(1,295)
    ]


def refs():
    return make_references({l:{"token_env_name":f"TEST_{l}_TOKEN",
                               "ig_user_id_env_name":f"TEST_{l}_IG"}
                            for l in "ABC"})


def env():
    return {f"TEST_{l}_{suffix}":value
            for l in "ABC"
            for suffix,value in (("TOKEN",f"fake{l}token"),
                                 ("IG",str(17840000000000000+"ABC".index(l))))}


def response(post_ids, next_cursor=None):
    media={"data":[{"id":p,"caption":"fixture"} for p in post_ids]}
    if next_cursor:
        media["paging"]={"next":"offline-next-page",
                         "cursors":{"after":next_cursor}}
    return {"business_discovery":{"media":media}}


class Reply:
    status=200
    headers={"X-App-Usage":'{"call_count":25}'}
    def __init__(self,data):
        self.buffer=BytesIO(json.dumps(data).encode())
    def read(self,size=-1):
        return self.buffer.read(size)
    def __enter__(self):
        return self
    def __exit__(self,*args):
        self.buffer.close()


class DailyProjectionTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.source=Path(self.t.name)/"source.sqlite"
        self.target=Path(self.t.name)/"gap.sqlite"

    def ledger_with_old_head(self):
        l=DailyGapLedger(self.target)
        l.seed_previous_head("KR_001","100")
        l.close()

    def wrapper(self, sender, day="2026-10-19",source=None):
        obj=DailyGuardedSnapshot(
            self.source if source is None else source,
            self.target,accounts(),refs(),env(),day,
            http_client=GraphHTTPClient(sender=sender,approved=True))
        self.addCleanup(obj.close)
        return obj

    def test_staged_page_projects_to_daily_ledger_and_can_reconcile(self):
        self.ledger_with_old_head()
        calls=[]
        def sender(*_args, **_kwargs):
            calls.append(1)
            return Reply(response(["115","114","113","112"],"page_2"))
        obj=self.wrapper(sender)
        outcome=obj.run_one("A",now=NOW)
        self.assertEqual(outcome["status"],"staged")
        self.assertEqual(outcome["projection"]["new_pages"],1)
        self.assertEqual(obj.ledger.status("KR_001","2026-10-19")["front_pages"],1)
        self.assertEqual(obj.reconcile_all()[0]["new_pages"],0)
        self.assertEqual(len(calls),1)
        rows=obj.ledger.db.execute(
            "SELECT media_ids_json FROM page_proof WHERE account_key='KR_001'"
        ).fetchall()
        self.assertEqual(json.loads(rows[0][0]),["115","114","113","112"])

    def test_crash_after_source_commit_reconcile_no_refetch(self):
        self.ledger_with_old_head()
        calls=[]
        def sender(*_args,**_kwargs):
            calls.append(1)
            return Reply(response(["115","114","113","112"],"page_2"))
        obj=self.wrapper(sender)
        result=obj.executor.run_one("A",now=NOW)
        self.assertEqual(result["status"],"staged")
        self.assertIsNone(obj.ledger.status("KR_001","2026-10-19"))
        self.assertEqual(obj.reconcile_all()[0]["new_pages"],1)
        self.assertEqual(obj.reconcile_all()[0]["new_pages"],0)
        self.assertEqual(len(calls),1)

    def test_restart_same_snapshot_can_reconcile(self):
        self.ledger_with_old_head()
        obj=self.wrapper(lambda *_a,**_k:Reply(response(["115"],"page_2")))
        obj.executor.run_one("A",now=NOW)
        obj.close()
        second=self.wrapper(lambda *_a,**_k:Reply(response(["101"])))
        self.assertEqual(second.reconcile_all()[0]["new_pages"],1)

    def test_unbound_completed_snapshot_cannot_be_attributed_to_today(self):
        source=Coordinator(self.source)
        try:
            evidence=PageEvidenceStore(source)
            source.seed()
            job=source.lease("A",now=NOW)
            evidence.commit_staged_page(job["id"],job["lease_id"],
                {"data":[{"id":"115"}],"paging":{}},
                now=NOW)
        finally:
            source.db.close()
        with self.assertRaises(DailyProjectionError):
            self.wrapper(lambda *_a,**_k:Reply(response(["115"])))

    def test_binding_different_day_is_rejected(self):
        self.wrapper(lambda *_a,**_k:Reply(response(["115"])))
        with self.assertRaises(DailyProjectionError):
            DailyGuardedSnapshot(
                self.source,self.target,accounts(),refs(),
                env(),"2026-10-20")

    def test_order_proof_missing_does_not_claim_success(self):
        self.ledger_with_old_head()
        obj=self.wrapper(lambda *_a,**_k:Reply(response(["115","114"],"page_2")))
        obj.executor.run_one("A",now=NOW)
        db=obj.executor._runner.coordinator.db
        db.execute("DELETE FROM staging_post_order WHERE position=1")
        with self.assertRaises(DailyProjectionError):
            obj.reconcile_all()
        # Starting the daily window is safe, but invalid ordered evidence
        # must never advance its page counter or add page proof.
        status=obj.ledger.status("KR_001","2026-10-19")
        self.assertEqual(status["front_pages"],0)
        self.assertEqual(obj.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof").fetchone()[0],0)

    def test_projection_failure_halts_lane_without_refetch(self):
        self.ledger_with_old_head()
        calls=[]
        def sender(*_a,**_k):
            calls.append(1)
            return Reply(response(["not_numeric"]))
        obj=self.wrapper(sender)
        status=obj.run_one("A",now=NOW)
        self.assertEqual(status["status"],"projection_blocked")
        self.assertEqual(len(calls),1)
        self.assertEqual(obj.run_one("A",now=NOW)["status"],"no_eligible_job")

    def test_three_pages_front_leads_to_pending_catchup(self):
        self.ledger_with_old_head()
        c=Coordinator(self.source)
        self.addCleanup(c.db.close)
        ev=PageEvidenceStore(c)
        l=DailyGapLedger(self.target)
        self.addCleanup(l.close)
        projector=DailyEvidenceProjector(c,l,accounts(),"2026-10-19")
        for page,lane in enumerate("ACB",1):
            c.db.execute(
                "INSERT INTO jobs(account_ordinal,page,lane,phase) VALUES (?,?,?,?)",
                (1,page,lane,"front" if page==1 else "catchup"))
        for page,(lane,post_ids,next_cursor) in enumerate([
            ("A",["115","114","113","112"],"page_2"),
            ("C",["111","110","109","108"],"page_3"),
            ("B",["107","106","105","104"],"page_4"),
        ],1):
            job=c.lease(lane,now=NOW)
            self.assertEqual(job["account_ordinal"],1)
            requested=None if page==1 else "page_"+str(page)
            page_data={"data":[{"id":pid} for pid in post_ids],
                       "paging":{"next":"offline-next-page",
                                 "cursors":{"after":next_cursor}}}
            ev.commit_staged_page(job["id"],job["lease_id"],page_data,
                                  requested_cursor=requested,now=NOW)
            self.assertEqual(projector.project_account(1)["new_pages"],1)
        s=l.status("KR_001","2026-10-19")
        self.assertEqual(s["front_pages"],3)
        self.assertEqual(s["status"],"catchup_pending")
        self.assertEqual(projector.project_account(1)["new_pages"],0)

    def test_production_source_db_refused(self):
        with self.assertRaises(ValueError):
            self.wrapper(lambda *_a,**_k:Reply(response(["115"])),
                         source=Path(self.t.name)/"collection.sqlite")

    def test_source_and_target_must_be_distinct(self):
        with self.assertRaises(DailyProjectionError):
            DailyGuardedSnapshot(self.target,self.target,accounts(),refs(),env(),"2026-10-19")

    def test_invalid_roster_cannot_bind(self):
        with self.assertRaises(DailyProjectionError):
            DailyGuardedSnapshot(self.source,self.target,accounts()[:293],
                                 refs(),env(),"2026-10-19")

    def test_projection_does_not_log_credentials(self):
        self.ledger_with_old_head()
        obj=self.wrapper(lambda *_a,**_k:Reply(response(["115"])))
        obj.run_one("A",now=NOW)
        dumps="\n".join(obj.ledger.db.iterdump())+"\n".join(
            obj.executor._runner.coordinator.db.iterdump())
        for token in ("fakeAtoken","fakeBtoken","fakeCtoken"):
            self.assertNotIn(token,dumps)


if __name__=="__main__":
    unittest.main()
