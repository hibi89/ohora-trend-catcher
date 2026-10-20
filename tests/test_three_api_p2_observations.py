"""P2 no-network end-to-end tests for temporal RAW observations and state history.

All tests use an isolated SQLite :memory: RAW clone and a disposable CaptureJournal.
Do not use a live Meta sender or operating collection.sqlite.
"""
from dataclasses import replace
from datetime import timedelta
import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_contract_executor import (
    FakeTransport, OBSERVED_AT, SCHEMA, make_request, make_response,
)
from three_api_capture import CaptureJournal
from three_api_contract_executor import ThreeApiContractExecutor
from three_api_raw_writer import ContractRawWriter


class P2ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = CaptureJournal(str(Path(self.temp.name) / "capture.sqlite"))
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.writer = ContractRawWriter(self.db)
        self.base = make_request()

    def tearDown(self):
        self.db.close()
        self.journal.close()
        self.temp.cleanup()

    def count(self, table):
        return self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def apply(self, number, *, day=0, edits=None, post_id=None):
        request = replace(self.base, request_id="req-p2-" + str(number))
        response = make_response(request)
        body = copy.deepcopy(response.payload)
        posts = body["business_discovery"]["media"]["data"]
        if post_id is not None:
            posts[0]["id"] = str(post_id)
        if edits is not None:
            edits(body, posts)
        instant = OBSERVED_AT + timedelta(days=day)
        response = replace(response, payload=body, received_at=instant)
        transport = FakeTransport(response)
        receipt = ThreeApiContractExecutor(
            transport, self.journal, self.writer).execute(request)
        self.assertEqual(transport.calls, 1)
        self.assertIn(receipt.status, ("committed", "already_committed"))
        return receipt, request

    def test_unchanged_recheck_records_new_time_but_reuses_all_states(self):
        self.apply(1, day=0)
        self.apply(2, day=1)
        self.assertEqual(self.count("query_observations"), 2)
        self.assertEqual(self.count("post_checks"), 2)
        self.assertEqual(self.count("post_observations"), 2)
        self.assertEqual(self.count("post_states"), 4)
        self.assertEqual(self.count("account_states"), 1)
        self.assertEqual(self.count("account_checks"), 2)
        self.assertEqual(self.count("change_events"), 0)
        self.assertEqual(self.count("active_change_batches"), 4)
        self.assertEqual(
            self.db.execute("SELECT change_count FROM active_change_batches").fetchall(),
            [(0,)] * 4)
        self.assertNotEqual(*[r[0] for r in self.db.execute(
            "SELECT checked_at FROM query_observations ORDER BY checked_at")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)

    def test_a_to_b_to_a_never_erases_prior_state_or_events(self):
        self.apply(1, day=0)
        def b(_body, posts):
            posts[0]["caption"] = "Changed caption"
            posts[0]["like_count"] = 88
            posts[0]["comments_count"] = 50
            posts[0]["permalink"] = "https://www.instagram.com/p/SYNTHETIC_B/"
        self.apply(2, day=1, edits=b)
        self.apply(3, day=2)
        self.assertEqual(self.count("post_checks"), 3)
        self.assertEqual(self.count("post_states"), 7)  # initial 4 + 3 changed components
        self.assertEqual(self.count("current_change_events"), 8)
        self.assertEqual(self.count("change_events"), 8)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(DISTINCT metrics_state_id) FROM post_checks").fetchone()[0], 2)
        paths = [r[0] for r in self.db.execute(
            "SELECT field_path FROM current_change_events ORDER BY field_path")]
        self.assertEqual(paths.count("content.caption"), 2)
        self.assertEqual(paths.count("metrics.like_count"), 2)
        self.assertEqual(paths.count("metrics.comments_count"), 2)
        self.assertEqual(paths.count("locator.permalink"), 2)
        rows = self.db.execute(
            "SELECT metrics_state_id FROM post_checks ORDER BY observed_at").fetchall()
        self.assertEqual(rows[0], rows[2])
        self.assertNotEqual(rows[0], rows[1])

    def test_null_zero_absent_and_unrequested_stay_distinct(self):
        self.apply(1)
        def absent(_body, posts):
            del posts[0]["caption"]
            del posts[0]["like_count"]
            posts[0]["comments_count"] = None
        self.apply(2, day=1, edits=absent)
        events = dict((r[0], r[1]) for r in self.db.execute(
            "SELECT field_path,kind FROM current_change_events"))
        self.assertEqual(events["content.caption"], "availability_change")
        self.assertEqual(events["metrics.like_count"], "availability_change")
        self.assertEqual(events["metrics.comments_count"], "metric_change")
        def zero(_body, posts):
            posts[0]["like_count"] = 0
            posts[0]["comments_count"] = 0
        self.apply(3, day=2, edits=zero)
        metrics_row = self.db.execute("""SELECT s.full_values_json,s.field_states_json
            FROM post_checks c JOIN post_states s ON c.metrics_state_id=s.state_id
            ORDER BY c.observed_at DESC LIMIT 1""").fetchone()
        import json
        self.assertEqual(json.loads(metrics_row[0])["like_count"], 0)
        self.assertEqual(json.loads(metrics_row[1])["like_count"], "returned")
        self.assertEqual(self.count("query_observations"), 3)
        # A previous value must not be overwritten by absent data.
        self.assertEqual(self.db.execute("SELECT caption FROM posts").fetchone()[0],
                         "synthetic nail art")

    def test_media_reorder_changes_structure_not_media_identity(self):
        def carousel(_body, posts):
            posts[:] = [{"id":"1234567890", "media_type":"CAROUSEL_ALBUM",
                         "children":{"data":[
                           {"id":"101","media_type":"IMAGE",
                            "media_url":"https://cdn.test/image1.jpg"},
                           {"id":"102","media_type":"VIDEO",
                            "media_url":"https://cdn.test/film.mp4"}]}}]
        self.apply(1, edits=carousel)
        def reordered(body, posts):
            carousel(body, posts)
            posts[0]["children"]["data"].reverse()
        self.apply(2, day=1, edits=reordered)
        self.assertEqual(self.count("post_media"), 2)
        self.assertEqual(self.count("post_checks"), 2)
        self.assertEqual(self.count("media_structure_members"), 4)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM current_change_events "
            "WHERE field_path='structure.children'").fetchone()[0], 1)
        states = [r[0] for r in self.db.execute(
            "SELECT structure_state_id FROM post_checks ORDER BY observed_at")]
        self.assertNotEqual(states[0],states[1])
        self.assertEqual([r[0] for r in self.db.execute(
            "SELECT media_id FROM media_structure_members "
            "WHERE structure_state_id=? ORDER BY position", (states[1],))], ["102","101"])

    def test_child_media_field_null_vs_absent_has_independent_revision(self):
        def first(_body, posts):
            posts[:] = [{"id":"1234567890", "media_type":"CAROUSEL_ALBUM",
                         "children":{"data":[{"id":"301", "media_type":"VIDEO",
                           "media_url":None, "thumbnail_url":"https://cdn.test/thumb.jpg"}]}}]
        self.apply(1,edits=first)
        def missing(body, posts):
            first(body,posts)
            posts[0]["children"]["data"][0].pop("media_url")
        self.apply(2,day=1,edits=missing)
        current = self.db.execute("""
            SELECT field_path,previous_field_status,new_field_status,kind
            FROM current_change_events
            WHERE field_path='locator.child.301.media_url'
        """).fetchall()
        self.assertEqual(current,
            [("locator.child.301.media_url","null","absent","availability_change")])
        rows = self.db.execute("""SELECT s.full_values_json FROM post_states s
          JOIN post_checks c ON c.locator_state_id=s.state_id
          ORDER BY c.observed_at""").fetchall()
        import json
        a,b = [json.loads(row[0])["child_locations"][0] for row in rows]
        self.assertEqual(a["media_url_status"],"null")
        self.assertIsNone(a["media_url"])
        self.assertEqual(b["media_url_status"],"absent")
        self.assertNotIn("media_url",b)

    def test_url_only_change_is_locator_not_new_nail_content(self):
        def link(_body, posts):
            posts[0]["media_url"] = "https://cdn.test/image-2026-10-21.jpg"
        self.apply(1)
        self.apply(2,day=1,edits=link)
        kinds=self.db.execute("SELECT field_path,kind FROM current_change_events").fetchall()
        self.assertIn(("locator.media_url","locator_change"),kinds)
        self.assertFalse(any(kind=="content_change" for _,kind in kinds))
        self.assertEqual(self.count("posts"),1)
        # Historical first-seen locator remains; current locator is a new state.
        self.assertIsNone(self.db.execute("SELECT media_url FROM posts").fetchone()[0])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(DISTINCT locator_state_id) FROM post_checks").fetchone()[0],2)

    def test_account_profile_opt_in_tracks_followers_and_unchanged_check(self):
        from three_api_request_spec import build_request_spec
        from three_api_runtime_contract import digest_sha256
        spec=build_request_spec("A","nails_1",include_account_metrics=True)
        self.base=replace(self.base,request_spec=spec,
            request_spec_digest=digest_sha256({
                "api_version":"v26.0",
                "target_ig_user_id":self.base.target_ig_user_id,
                "spec":spec}))
        def a(body,_posts):
            body["business_discovery"]["followers_count"]=1500
            body["business_discovery"]["media_count"]=100
        def b(body,posts):
            a(body,posts)
            body["business_discovery"]["followers_count"]=1600
        self.apply(1,edits=a)
        self.apply(2,day=1,edits=b)
        self.apply(3,day=2,edits=b)
        self.assertEqual(self.count("account_checks"),3)
        self.assertEqual(self.count("account_states"),2)
        rows=self.db.execute("""SELECT e.field_path,e.kind,e.previous_value_json,
              e.new_value_json FROM current_change_events e
              WHERE e.entity_kind='account'""").fetchall()
        self.assertEqual(rows,[("profile.followers_count","profile_change","1500","1600")])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM active_change_batches WHERE entity_kind='account' "
            "AND change_count=0").fetchone()[0],2)

    def test_late_replay_updates_only_derived_comparison_pointer(self):
        self.apply(1,day=0)
        def two(_body, posts):
            posts[0]["like_count"] = 2
        self.apply(3,day=2,edits=two)
        old_event = self.db.execute(
            "SELECT event_id,check_id,previous_check_id FROM change_events "
            "WHERE field_path='metrics.like_count'").fetchone()
        self.assertIsNotNone(old_event)
        self.apply(2,day=1,edits=two)
        self.assertEqual(self.count("post_checks"),3)
        self.assertEqual(self.count("query_observations"),3)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM current_change_events "
            "WHERE field_path='metrics.like_count'").fetchone()[0],1)
        self.assertGreater(self.db.execute(
            "SELECT COUNT(*) FROM change_events WHERE field_path='metrics.like_count'"
        ).fetchone()[0], 1)  # old derived event retained, but no longer active
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM current_change_events WHERE event_id=?",
            (old_event[0],)).fetchone()[0], 0)
        latest = self.db.execute(
            "SELECT request_id FROM post_checks ORDER BY observed_at DESC LIMIT 1").fetchone()[0]
        self.assertEqual(latest, "req-p2-3")  # no rollback of current projection time

    def test_successful_empty_page_has_query_and_account_check(self):
        def empty(_body, posts):
            posts[:] = []
        self.apply(1,edits=empty)
        self.assertEqual(self.count("query_observations"),1)
        self.assertEqual(self.count("post_checks"),0)
        self.assertEqual(self.count("account_checks"),1)
        self.assertEqual(self.db.execute(
            "SELECT returned_count FROM query_observations").fetchone()[0],0)

    def test_receipt_counts_are_per_request_even_in_one_run(self):
        first,_ = self.apply(1)
        second,_ = self.apply(2,day=1,post_id="777777")
        self.assertEqual(first.row_counts["media_inserted"],1)
        self.assertEqual(second.row_counts["media_inserted"],1)
        self.assertEqual(second.row_counts["query_observations"],1)
        self.assertEqual(second.row_counts["post_checks"],1)
        self.assertEqual(self.count("post_media"),2)

    def test_replay_does_not_double_insert_or_change_original(self):
        result,req = self.apply(1)
        counts = tuple(self.count(t) for t in
            ("query_observations","post_checks","post_states","account_checks",
             "change_events","three_api_contract_receipts"))
        denied = FakeTransport(error=AssertionError("HTTP must not repeat"))
        replayed = ThreeApiContractExecutor(
            denied,self.journal,self.writer).execute(req)
        self.assertEqual(replayed.status,"already_committed")
        self.assertEqual(denied.calls,0)
        self.assertEqual(tuple(self.count(t) for t in
            ("query_observations","post_checks","post_states","account_checks",
             "change_events","three_api_contract_receipts")),counts)

    def test_preexisting_legacy_rows_remain_unmodified(self):
        self.db.execute("""INSERT INTO posts(post_id,account_key,caption)
                           VALUES('999','KR_001','legacy first-seen')""")
        self.db.execute("""INSERT INTO post_media(media_id,post_id,account_key,item_order)
                           VALUES('999','999','KR_001',0)""")
        self.apply(1)
        self.assertEqual(self.db.execute(
            "SELECT caption FROM posts WHERE post_id='999'").fetchone()[0],
            "legacy first-seen")
        self.assertEqual(self.db.execute(
            "SELECT item_order FROM post_media WHERE media_id='999'").fetchone()[0],0)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])


if __name__ == "__main__":
    unittest.main()
