"""Purely synthetic opt-in carousel request -> fake HTTP -> front RAW mirror.

These tests NEVER open sockets, read real credentials, or write collection.sqlite.
The optional nested child field syntax remains unverified against real Meta.
"""
from dataclasses import replace
from datetime import datetime,timezone
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_request_spec import build_request_spec,business_discovery_field
from three_api_http_preflight import prepare_graph_request,PreparedGraphRequest
from three_api_credentials import make_references
from three_api_graph_http import GraphHTTPClient,GraphHTTPBlocked
from three_api_daily_projection import DailyGuardedSnapshot
from three_api_raw_mirror import OfflineRawMirror
from three_api_raw_projection import OfflineRawEvidenceProjector
from three_api_coordinator import Coordinator
from three_api_daily_gap import DailyGapLedger
from three_api_catchup_offline import OfflineCatchupRunner
from three_api_catchup_payload import OfflineCatchupPayloadProjector

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
DAY="2026-10-19"
CHILDREN="children{id,media_type,media_url,thumbnail_url}"

def accounts():
    return [{"ordinal":i,"key":f"KR_{i:03}","username":f"nails_{i}"}
            for i in range(1,295)]


def refs():
    return make_references({l:{"token_env_name":f"TEST_{l}_TOKEN",
                             "ig_user_id_env_name":f"TEST_{l}_IG"}
                            for l in "ABC"})


def env():
    return {f"TEST_{l}_{suffix}":value
            for l in "ABC"
            for suffix,value in (("TOKEN",f"OFFLINE_{l}_NOT_A_TOKEN"),
                                 ("IG",str(17840000000000000+"ABC".index(l))))}


def nested_carousel(pid="115"):
    return {"id":pid,"caption":"synthetic gel nail","media_type":"CAROUSEL_ALBUM",
            "like_count":0,
            "children":{"data":[
                {"id":"901","media_type":"IMAGE","media_url":"https://example.test/one",
                 "access_token":"NEVER_STORE_SECRET"},
                {"id":"902","media_type":"VIDEO","thumbnail_url":"https://example.test/two"}
            ]}}


class Reply:
    status=200
    headers={"X-App-Usage":'{"call_count":20}'}
    def __init__(self,body):
        self.stream=BytesIO(json.dumps(body).encode("utf-8"))
    def read(self,n=-1):
        return self.stream.read(n)
    def __enter__(self):
        return self
    def __exit__(self,*unused):
        self.stream.close()


class OptInRequestTests(unittest.TestCase):
    def test_default_query_mandates_reels_views_and_legacy_spec_is_readable(self):
        # Owner's new requirement supersedes the old default-query fixture.
        # Historical RequestSpec evidence must still retain its original fields.
        old="id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,thumbnail_url"
        from three_api_request_spec import RequestSpec
        legacy=RequestSpec("A","nails_1",4,old,None)
        self.assertEqual(legacy.media_fields,old)
        spec=build_request_spec("A","nails_1")
        self.assertIn("media_product_type",spec.media_fields.split(","))
        self.assertIn("view_count",spec.media_fields.split(","))
        self.assertFalse(spec.include_carousel_children)
        self.assertNotIn("children{",business_discovery_field(spec))
        self.assertFalse(spec.as_safe_dict()["carousel_children_opt_in"])

    def test_nested_fragment_only_on_explicit_opt_in(self):
        for lane in "ABC":
            with self.subTest(lane=lane):
                spec=build_request_spec(lane,"nails_1",include_carousel_children=True)
                expr=business_discovery_field(spec)
                self.assertIn(","+CHILDREN+"}",expr)
                self.assertEqual(expr.count(CHILDREN),1)
                self.assertEqual(spec.media_fields.count("children{"),1)

    def test_cursor_and_opt_in_coexist_without_token_in_query(self):
        spec=build_request_spec("C","nails_2",after="page_2=",
                                include_carousel_children=True)
        req=prepare_graph_request(spec,refs()["C"],env()["TEST_C_IG"])
        f=parse_qs(req.unsigned_parts()["query_string"])["fields"][0]
        self.assertIn("media.after(page_2=).limit(4)",f)
        self.assertIn(CHILDREN,f)
        self.assertNotIn("access_token",str(req.unsigned_parts()))
        self.assertNotIn("page_2=",repr(req))
        self.assertTrue(req.safe_summary()["carousel_children_opt_in"])

    def test_non_boolean_opt_in_rejected(self):
        for bad in (None,0,1,"true",[],{}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                build_request_spec("A","nails_1",include_carousel_children=bad)

    def test_untrusted_opt_in_dataclass_rejected_at_preflight(self):
        good=build_request_spec("A","nails_1")
        bad=replace(good,include_carousel_children=True)
        with self.assertRaises(ValueError):
            prepare_graph_request(bad,refs()["A"],env()["TEST_A_IG"])

    def test_prepared_graph_flags_checked_at_http_boundary(self):
        spec=build_request_spec("A","nails_1",include_carousel_children=True)
        prepared=prepare_graph_request(spec,refs()["A"],env()["TEST_A_IG"])
        calls=[]
        def sender(req,*,timeout):
            calls.append(1)
            return Reply({"business_discovery":{"media":{"data":[]}}})
        client=GraphHTTPClient(sender=sender,approved=True)
        with self.assertRaises(GraphHTTPBlocked):
            client.fetch(replace(prepared,include_carousel_children=False),"FAKE")
        self.assertEqual(calls,[])
        with self.assertRaises(GraphHTTPBlocked):
            client.fetch(replace(prepared,fields=prepared.fields+",evil"),"FAKE")
        self.assertEqual(calls,[])


class OptInHTTPMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.calls=[]
        self.mirror=OfflineRawMirror(self.root/"three_api_raw_mirror.sqlite")
        self.addCleanup(self.mirror.close)

    def new_snapshot(self,include_carousel_children,reply):
        def sender(request,*,timeout):
            q=parse_qs(request.full_url.split("?",1)[1])["fields"][0]
            self.calls.append(q)
            return Reply(reply)
        snap=DailyGuardedSnapshot(
            self.root/"source.sqlite",self.root/"daily.sqlite",
            accounts(),refs(),env(),DAY,
            http_client=GraphHTTPClient(sender=sender,approved=True),
            include_carousel_children=include_carousel_children,
        )
        self.addCleanup(snap.close)
        return snap

    def test_opt_in_fake_http_front_to_raw_parent_child(self):
        reply={"business_discovery":{"media":{"data":[nested_carousel()]}}}
        snapshot=self.new_snapshot(True,reply)
        with mock.patch("socket.create_connection",side_effect=AssertionError("No network")):
            result=snapshot.run_one("A",now=NOW)
        self.assertEqual(result["status"],"staged")
        self.assertIn(CHILDREN,self.calls[0])
        self.assertEqual(result["projection"]["new_pages"],1)
        out=OfflineRawEvidenceProjector(snapshot.projector,self.mirror).project_page(
            1,1,observed_at=NOW.isoformat())
        self.assertEqual(out["status"],"committed")
        self.assertEqual(self.mirror.summary()["raw_posts"],1)
        rows=self.mirror.db.execute(
            "SELECT media_id,post_id,media_role,item_order FROM raw_post_media ORDER BY item_order"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [("115","115","parent",0),("901","115","child",1),
                          ("902","115","child",2)])
        dump="\n".join(snapshot.executor._runner.coordinator.db.iterdump())
        dump+="\n".join(self.mirror.db.iterdump())
        self.assertNotIn("NEVER_STORE_SECRET",dump)
        self.assertEqual(self.mirror.summary()["raw_post_observations"],1)

    def test_opt_out_fake_http_default_without_children(self):
        reply={"business_discovery":{"media":{"data":[
            {"id":"115","media_type":"CAROUSEL_ALBUM","caption":"without children"}
        ]}}}
        snapshot=self.new_snapshot(False,reply)
        self.assertEqual(snapshot.run_one("A",now=NOW)["status"],"staged")
        self.assertNotIn("children{",self.calls[0])
        OfflineRawEvidenceProjector(snapshot.projector,self.mirror).project_page(
            1,1,observed_at=NOW.isoformat())
        self.assertEqual(self.mirror.summary()["raw_post_media"],1)
        saved=snapshot.executor._runner.coordinator.db.execute(
            "SELECT payload_json FROM staging_posts").fetchone()[0]
        self.assertNotIn("children",json.loads(saved))

    def test_partial_nested_child_page_blocks_receipt_and_lane(self):
        bad=nested_carousel()
        bad["children"]["paging"]={"next":"https://graph.facebook.com/after?secret=X"}
        snap=self.new_snapshot(True,{"business_discovery":{"media":{"data":[bad]}}})
        result=snap.run_one("A",now=NOW)
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(snap.executor._runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM staging_posts").fetchone()[0],0)
        self.assertEqual(self.mirror.summary()["raw_post_media"],0)

    def test_invalid_opt_in_prevents_sender_invocation(self):
        def sender(*args,**kwargs):
            self.calls.append(1)
            return Reply({})
        with self.assertRaises(ValueError):
            DailyGuardedSnapshot(self.root/"source.sqlite",
                self.root/"daily.sqlite",accounts(),refs(),env(),DAY,
                http_client=GraphHTTPClient(sender=sender,approved=True),
                include_carousel_children="yes")
        self.assertEqual(self.calls,[])
        self.assertFalse((self.root/"source.sqlite").exists())

    def test_no_live_sender_by_default_with_opt_in(self):
        snapshot=DailyGuardedSnapshot(
            self.root/"source.sqlite",self.root/"daily.sqlite",
            accounts(),refs(),env(),DAY,include_carousel_children=True)
        self.addCleanup(snapshot.close)
        result=snapshot.run_one("A",now=NOW)
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(self.mirror.summary()["raw_posts"],0)

    def test_front_mirror_replay_without_http(self):
        snap=self.new_snapshot(True,{"business_discovery":{"media":{"data":[nested_carousel()]}}})
        snap.run_one("A",now=NOW)
        projector=OfflineRawEvidenceProjector(snap.projector,self.mirror)
        self.assertEqual(projector.project_page(1,1,observed_at=NOW.isoformat(),
                                                stage_only=True)["status"],"staged")
        self.calls.clear()
        self.assertEqual(projector.reconcile_all({})[0]["status"],"committed")
        self.assertEqual(self.calls,[])
        self.assertEqual(self.mirror.summary()["raw_post_media"],3)

    def test_offline_catchup_fixture_and_raw_after_opt_in_front(self):
        reply={"business_discovery":{"media":{"data":[nested_carousel()],
            "paging":{"next":"offline-next-page","cursors":{"after":"p2"}}}}}
        snap=self.new_snapshot(True,reply)
        snap.ledger.seed_previous_head("KR_001","100",day="2026-10-18")
        snap.run_one("A",now=NOW)
        # Complete the other front pages through checked ledger fixture proofs.
        for cursor,ids,nextcur in (
            ("p2",["114","113","112","111"],"p3"),
            ("p3",["110","109","108","107"],"p4"),
        ):
            snap.ledger.record_page("KR_001",DAY,phase="front",
                requested_cursor=cursor,post_ids=ids,next_cursor=nextcur)
        # This remains an *offline* fixture, not an authorized page4+ sender.
        fixture={"data":[{"id":"103","media_type":"CAROUSEL_ALBUM",
                            "children":nested_carousel("103")["children"]},
                           {"id":"102"},{"id":"101"},{"id":"100"}]}
        runner=OfflineCatchupRunner(snap.ledger,snap.executor._runner.coordinator,
                 ["KR_001"],{("KR_001",DAY,"p4"):fixture})
        pipe=OfflineCatchupPayloadProjector(runner,self.mirror)
        result=pipe.run_one("A",DAY,now=NOW)
        self.assertEqual(result["status"],"applied")
        self.assertEqual(self.mirror.summary()["raw_posts"],4)
        self.assertEqual(self.mirror.summary()["raw_post_media"],6)
        self.assertEqual(len(self.calls),1)


if __name__=="__main__":
    unittest.main()
