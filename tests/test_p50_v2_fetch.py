"""Offline HTTP-shape checks for P50 Business Discovery pagination."""
import json
import sys
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from p50_v2_collector import fetch


class FakeResponse:
    status = 200
    headers = {}
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self, size):
        return json.dumps(self.body).encode("utf-8")


def payload(ids, after=None, next_page=False):
    media={"data":[{"id":x,"timestamp":"2025-01-01T00:00:00+0000",
                    "like_count":10,"comments_count":2,"view_count":100} for x in ids]}
    if after is not None:
        media["paging"]={"cursors":{"after":after}}
        if next_page:
            media["paging"]["next"]="https://graph.facebook.com/next"
    return {"business_discovery":{"id":"1","media":media}}


class FetchTests(unittest.TestCase):
    def check(self, body, cursor=None, limit=8):
        calls=[]
        def sender(req,timeout):
            calls.append(req.full_url)
            return FakeResponse(body)
        result=fetch("A","sampleuser",cursor,"123","testtoken","v26.0",
                     sender,limit=limit)
        fields=parse_qs(urlsplit(calls[0]).query)["fields"][0]
        return result, fields

    def test_continuation_is_exactly_five(self):
        data,fields=self.check(payload(["4","5","6","7","8"],"newcursor",True),
                               cursor="oldcursor",limit=5)
        self.assertEqual(len(data[0]),5)
        self.assertEqual(data[1],"newcursor")
        self.assertIn("media.after(oldcursor).limit(5)",fields)
        self.assertIn("view_count",fields)

    def test_cursor_only_page_remains_continuable(self):
        data,_=self.check(payload(["last"],"terminalcursor",False))
        self.assertEqual(data[1],"terminalcursor")

    def test_empty_terminal_page_exhausts_even_with_after_cursor(self):
        data,_=self.check(payload([],"terminalcursor",False),
                          cursor="previouscursor")
        self.assertIsNone(data[1])

    def test_page_without_cursor_is_terminal(self):
        data,_=self.check(payload(["last"]))
        self.assertIsNone(data[1])

    def test_same_cursor_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"did not advance"):
            self.check(payload(["1"],"same",True),cursor="same")

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"Duplicate post IDs"):
            self.check(payload(["a","a"],"fresh",True))

    def test_missing_next_cursor_fails_closed(self):
        body=payload(["a"])
        body["business_discovery"]["media"]["paging"]={"next":"https://graph.facebook.com/next"}
        with self.assertRaisesRegex(RuntimeError,"without after cursor"):
            self.check(body)


if __name__=="__main__":
    unittest.main()
