"""Synthetic-only carousel nested-child integrity across front and catch-up."""
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_meta_normalizer import normalize_meta_page
from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore
from three_api_daily_gap import DailyGapLedger
from three_api_daily_projection import DailyEvidenceProjector
from three_api_raw_mirror import OfflineRawMirror
from three_api_raw_projection import OfflineRawEvidenceProjector
from three_api_catchup_offline import OfflineCatchupRunner, OfflineCatchupError
from three_api_catchup_payload import OfflineCatchupPayloadProjector

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
DAY="2026-10-19"

def parent(media_id="115",children=None):
    if children is None:
        children=[{"id":"201","media_type":"IMAGE","media_url":"https://example.test/a",
                   "access_token":"FAKE_CHILD_SECRET"},
                  {"id":"202","media_type":"VIDEO","thumbnail_url":"https://example.test/b"}]
    return {"id":media_id,"media_type":"CAROUSEL_ALBUM","caption":"synthetic carousel",
            "children":{"data":children},"like_count":0,"access_token":"FAKE_PARENT_SECRET"}


def accounts():
    return [{"ordinal":i,"key":f"KR_{i:03}","username":f"nails_{i}"}
            for i in range(1,295)]


class CarouselNormalizerTests(unittest.TestCase):
    def test_nested_allowlisted_children_keep_position_and_metadata(self):
        cleaned=normalize_meta_page({"business_discovery":{"media":{"data":[parent()]}}})
        nested=cleaned["data"][0]["children"]
        self.assertEqual([x["id"] for x in nested],["201","202"])
        self.assertEqual(nested[0]["media_url"],"https://example.test/a")
        self.assertEqual(nested[1]["thumbnail_url"],"https://example.test/b")
        self.assertNotIn("FAKE_CHILD_SECRET",str(cleaned))
        self.assertNotIn("FAKE_PARENT_SECRET",str(cleaned))

    def test_no_children_field_means_unknown_not_empty(self):
        cleaned=normalize_meta_page({"data":[{"id":"115","media_type":"CAROUSEL_ALBUM"}]})
        self.assertNotIn("children",cleaned["data"][0])

    def test_nested_children_paging_incomplete_keeps_received_children(self):
        p=parent()
        p["children"]["paging"]={"next":"https://graph.facebook.com/?access_token=LEAK"}
        posts=normalize_meta_page({"data":[p]}, allow_partial_children=True)["data"]
        self.assertEqual([x["id"] for x in posts[0]["children"]], ["201","202"])
        self.assertNotIn("LEAK", str(posts))

    def test_nested_children_has_next_preserves_received_children(self):
        p=parent()
        p["children"]["paging"]={"has_next":True}
        posts=normalize_meta_page({"data":[p]}, allow_partial_children=True)["data"]
        self.assertEqual(len(posts[0]["children"]),2)

    def test_20_children_allowed_21_blocked(self):
        p=parent(children=[{"id":str(i)} for i in range(200,220)])
        self.assertEqual(len(normalize_meta_page({"data":[p]})["data"][0]["children"]),20)
        p["children"]["data"].append({"id":"220"})
        with self.assertRaises(ValueError):
            normalize_meta_page({"data":[p]})

    def test_nested_children_duplicate_identity_blocks(self):
        for items in ([{"id":"201"},{"id":"201"}],[{"id":"115"}]):
            with self.subTest(items=items),self.assertRaises(ValueError):
                normalize_meta_page({"data":[parent(children=items)]})

    def test_nested_children_invalid_types_block(self):
        for children in (None,[],{"data":"wrong"},{"data":[{"id":None}]},
                         {"data":[{"id":"abc"}]},{"data":[42]},
                         {"data":[{"id":"201","media_type":"ALBUM"}]},
                         {"data":[{"id":"201","media_url":True}]},
                         {"data":[],"paging":"wrong"}):
            p=parent()
            p["children"]=children
            with self.subTest(children=children),self.assertRaises(ValueError):
                normalize_meta_page({"data":[p]})

    def test_non_carousel_cannot_claim_nested_children(self):
        p=parent()
        p["media_type"]="IMAGE"
        with self.assertRaises(ValueError):
            normalize_meta_page({"data":[p]})


class CarouselStorageTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root=Path(t.name)
        self.root=root
        self.c=Coordinator(root/"coordinator.sqlite")
        self.addCleanup(self.c.db.close)
        self.e=PageEvidenceStore(self.c)
        self.l=DailyGapLedger(root/"gap.sqlite")
        self.addCleanup(self.l.close)
        self.m=OfflineRawMirror(root/"three_api_raw_mirror.sqlite")
        self.addCleanup(self.m.close)

    def test_front_page_staging_to_raw_child_media(self):
        daily=DailyEvidenceProjector(self.c,self.l,accounts(),DAY)
        self.c.db.execute(
            "INSERT INTO jobs(account_ordinal,page,lane,phase) VALUES (1,1,'A','front')"
        )
        job=self.c.lease("A",now=NOW)
        clean=normalize_meta_page({"data":[parent()]})
        self.e.commit_staged_page(job["id"],job["lease_id"],clean,now=NOW)
        self.assertEqual(daily.project_account(1)["new_pages"],1)
        out=OfflineRawEvidenceProjector(daily,self.m).project_page(
            1,1,observed_at=NOW.isoformat()
        )
        self.assertEqual(out["status"],"committed")
        rows=self.m.db.execute(
            "SELECT media_id,post_id,media_role,item_order,media_type "
            "FROM raw_post_media ORDER BY item_order").fetchall()
        self.assertEqual([tuple(r) for r in rows],[
            ("115","115","parent",0,"CAROUSEL_ALBUM"),
            ("201","115","child",1,"IMAGE"),
            ("202","115","child",2,"VIDEO")])
        self.assertEqual(self.m.summary()["raw_posts"],1)
        self.assertEqual(self.m.summary()["raw_post_observations"],1)
        self.assertNotIn("FAKE_CHILD_SECRET","\n".join(self.m.db.iterdump()))

    def catchup(self, p=None):
        self.l.seed_previous_head("KR_001","100",day="2026-10-18")
        self.l.begin_day("KR_001",DAY)
        for cursor,ids,nxt in (
            (None,["115","114","113","112"],"p2"),
            ("p2",["111","110","109","108"],"p3"),
            ("p3",["107","106","105","104"],"p4"),
        ):
            self.l.record_page("KR_001",DAY,phase="front",
                requested_cursor=cursor,post_ids=ids,next_cursor=nxt)
        fixture={"data":[p or parent("103"),{"id":"102"},
                         {"id":"101"},{"id":"100"}]}
        pages={("KR_001",DAY,"p4"):fixture}
        runner=OfflineCatchupRunner(self.l,self.c,["KR_001"],pages)
        pipeline=OfflineCatchupPayloadProjector(runner,self.m)
        return pages,runner,pipeline

    def test_catchup_full_child_payload_and_restart_no_duplicate(self):
        pages,runner,pipeline=self.catchup()
        self.assertEqual(pipeline.run_one("A",DAY,now=NOW)["status"],"applied")
        self.assertEqual(self.m.summary()["raw_posts"],4)
        self.assertEqual(self.m.summary()["raw_post_media"],6)
        row=self.l.db.execute("SELECT payload_json FROM catchup_inbox").fetchone()
        self.assertEqual([c["id"] for c in json.loads(row[0])["posts"][0]["children"]],
                         ["201","202"])
        pages.clear()
        reopened=OfflineCatchupRunner(self.l,self.c,["KR_001"],{})
        replay=OfflineCatchupPayloadProjector(reopened,self.m).reconcile_all()
        self.assertEqual(replay[0]["status"],"already_committed")
        self.assertEqual(self.m.summary()["raw_post_media"],6)

    def test_catchup_stage_crash_restores_nested_child_media(self):
        pages,runner,pipeline=self.catchup()
        self.assertEqual(pipeline.run_one("A",DAY,now=NOW,stage_only=True)["status"],
                         "staged_unapplied")
        pages.clear()
        self.assertEqual(OfflineCatchupPayloadProjector(
            OfflineCatchupRunner(self.l,self.c,["KR_001"],{}),self.m
        ).run_one("B",DAY,now=NOW)["status"],"reconciled")
        self.assertEqual(self.m.summary()["raw_post_media"],6)

    def test_catchup_paged_children_do_not_advance_proof(self):
        p=parent("103")
        p["children"]["paging"]={"next":"https://example.test/remaining"}
        pages,runner,pipeline=self.catchup(p)
        self.assertEqual(pipeline.run_one("A",DAY,now=NOW)["status"],
                         "invalid_offline_fixture")
        self.assertEqual(self.l.status("KR_001",DAY)["catchup_pages"],0)
        self.assertEqual(self.l.db.execute(
            "SELECT COUNT(*) FROM catchup_inbox").fetchone()[0],0)
        self.assertEqual(self.m.summary()["raw_posts"],0)

    def test_catchup_nested_child_tamper_blocks_reconcile(self):
        pages,runner,pipeline=self.catchup()
        pipeline.run_one("A",DAY,now=NOW,stage_only=True)
        row=self.l.db.execute(
            "SELECT payload_json FROM catchup_inbox").fetchone()
        changed=json.loads(row[0])
        changed["posts"][0]["children"][0]["media_url"]="https://example.test/tampered"
        self.l.db.execute(
            "UPDATE catchup_inbox SET payload_json=?",
            (json.dumps(changed,ensure_ascii=False,sort_keys=True,separators=(",",":")),)
        )
        with self.assertRaises(OfflineCatchupError):
            runner.reconcile_pending()
        self.assertEqual(self.l.status("KR_001",DAY)["catchup_pages"],0)
        self.assertEqual(self.m.summary()["raw_posts"],0)

    def test_duplicate_child_across_posts_blocks_entire_mirror_page(self):
        p=parent("103")
        p2=parent("102",children=[{"id":"201","media_type":"IMAGE"}])
        self.l.seed_previous_head("KR_001","100",day="2026-10-18")
        self.l.begin_day("KR_001",DAY)
        for cursor,ids,nxt in (
            (None,["115","114","113","112"],"p2"),
            ("p2",["111","110","109","108"],"p3"),
            ("p3",["107","106","105","104"],"p4"),
        ):
            self.l.record_page("KR_001",DAY,phase="front",
                requested_cursor=cursor,post_ids=ids,next_cursor=nxt)
        fixtures={("KR_001",DAY,"p4"):{"data":[p,p2,{"id":"101"},{"id":"100"}]}}
        runner=OfflineCatchupRunner(self.l,self.c,["KR_001"],fixtures)
        pipe=OfflineCatchupPayloadProjector(runner,self.m)
        self.assertEqual(pipe.run_one("A",DAY,now=NOW)["status"],
                         "payload_projection_blocked")
        self.assertEqual(self.m.summary()["raw_posts"],0)
        self.assertEqual(self.m.summary()["pending_pages"],0)

if __name__=="__main__":
    unittest.main()
