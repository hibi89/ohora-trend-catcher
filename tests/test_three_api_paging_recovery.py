"""Synthetic read-only repair planning for immutable paging evidence."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_paging_recovery import (
    build_paging_recovery_plan,
    cursor_failure_disposition,
    plan_cursor_recovery,
)


def evidence_row(request_id, *, chain_ok=True):
    return {
        "request_id": request_id,
        "safe_body_json": json.dumps({
            "data": [{"id": f"media-{n}"} for n in range(4)],
            "paging": {"cursors": {"after": f"opaque-cursor-{request_id}"}},
        }),
        "stored_terminal": True,
        "stored_response_cursor": None,
        "capture_present": chain_ok,
        "raw_receipt_present": chain_ok,
        "capture_raw_digest_match": chain_ok,
    }


class PagingRecoveryTests(unittest.TestCase):
    def test_49_immutable_rows_are_only_planned_and_never_applied(self):
        rows = [evidence_row(f"request-{n}") for n in range(49)]
        plan = build_paging_recovery_plan(rows)
        self.assertEqual(plan["summary"]["total"], 49)
        self.assertEqual(plan["summary"]["cursor_candidate_review_required"], 49)
        self.assertTrue(plan["read_only"])
        self.assertFalse(plan["automatic_apply"])
        self.assertTrue(all(row["mutations"] == [] and row["network_requests"] == 0
                            for row in plan["rows"]))
        self.assertTrue(all("opaque-cursor" not in str(row) for row in plan["rows"]))

    def test_incomplete_capture_or_receipt_chain_is_not_recoverable(self):
        plan = build_paging_recovery_plan([evidence_row("request-a", chain_ok=False)])
        self.assertEqual(plan["rows"][0]["status"], "evidence_chain_incomplete")

    def test_duplicate_request_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_paging_recovery_plan([evidence_row("same"), evidence_row("same")])

    def test_plan_repeat_is_idempotent(self):
        row = evidence_row("request-a")
        self.assertEqual(build_paging_recovery_plan([row]), build_paging_recovery_plan([row]))

    def test_valid_cursor_reanchors_and_resumes_with_new_cursor(self):
        result = plan_cursor_recovery(["id-4", "id-5", "id-6"], [{
            "data": [{"id": "id-1"}, {"id": "id-2"},
                     {"id": "id-3"}, {"id": "id-5"}],
            "paging": {"cursors": {"after": "new-cursor"}},
        }])
        self.assertEqual(result["status"], "resume_ready")
        self.assertEqual(result["anchor_media_id"], "id-5")
        self.assertEqual(result["resume_cursor"], "new-cursor")
        self.assertEqual(result["pages_examined"], 1)

    def test_deleted_anchor_can_reanchor_on_neighbor(self):
        result = plan_cursor_recovery(["id-4", "id-5", "id-6"], [{
            "data": [{"id": "id-1"}, {"id": "id-2"},
                     {"id": "id-3"}, {"id": "id-5"}],
            "paging": {"cursors": {"after": "new-cursor"}},
        }])
        self.assertEqual(result["anchor_media_id"], "id-5")
        self.assertEqual(result["deleted_media_ids_confirmed"], [])

    def test_cursor_failure_requires_approved_new_round_and_never_retries(self):
        result = cursor_failure_disposition(error_category="cursor_invalid",
                                            request_id="failed-request")
        self.assertEqual(result["status"], "resume_required")
        self.assertFalse(result["retry_existing_request"])
        self.assertTrue(result["recovery_round_requires_approval"])
        self.assertEqual(result["mutations"], [])

    def test_invalid_cursor_to_newest_page_overlap_to_new_cursor_is_offline_only(self):
        failure = cursor_failure_disposition(error_category="cursor_invalid",
                                             request_id="failed-request")
        self.assertEqual(failure["status"], "resume_required")
        self.assertFalse(failure["retry_existing_request"])

        recovery = plan_cursor_recovery(["old-4", "old-5", "old-6"], [{
            "data": [{"id": "new-1"}, {"id": "new-2"},
                     {"id": "new-3"}, {"id": "old-5"}],
            "paging": {"cursors": {"after": "replacement-cursor"}},
        }])
        self.assertEqual(recovery["status"], "resume_ready")
        self.assertEqual(recovery["anchor_media_id"], "old-5")
        self.assertEqual(recovery["resume_cursor"], "replacement-cursor")
        self.assertNotEqual(recovery["resume_cursor"], "invalid-cursor")
        self.assertEqual(recovery["network_requests"], 0)
        self.assertEqual(recovery["deleted_media_ids_confirmed"], [])

    def test_generic_http_400_is_not_inferred_as_cursor_failure(self):
        result = cursor_failure_disposition(error_category="http_400",
                                            request_id="failed-request")
        self.assertEqual(result["status"], "operator_review")

    def test_reordered_posts_and_repeated_observations_are_deduplicated_only_for_analysis(self):
        result = plan_cursor_recovery(["id-2", "id-3", "id-4"], [
            {"data": [{"id": "id-1"}, {"id": "id-2"},
                      {"id": "id-3"}, {"id": "id-5"}],
             "paging": {"cursors": {"after": "cursor-a"}}},
            {"data": [{"id": "id-3"}, {"id": "id-6"}],
             "paging": {}},
        ])
        self.assertEqual(result["status"], "terminal_verified")
        self.assertEqual(result["observation_ids"].count("id-3"), 2)
        self.assertEqual(result["analysis_media_ids"].count("id-3"), 1)
        self.assertEqual(result["anchor_media_id"], "id-3")

    def test_overlap_not_found_within_three_pages_marks_coverage_gap(self):
        pages = [{"data": [{"id": f"new-{page}-{index}"} for index in range(4)],
                  "paging": {"cursors": {"after": f"cursor-{page}"}}}
                 for page in range(1, 4)]
        result = plan_cursor_recovery(["old-1", "old-2"], pages)
        self.assertEqual(result["status"], "coverage_gap")
        self.assertEqual(result["pages_examined"], 3)
        self.assertIsNone(result["resume_cursor"])

    def test_anchor_on_third_page_does_not_dispatch_a_fourth_page(self):
        pages = [{"data": [{"id": f"new-{page}-{index}"} for index in range(4)],
                  "paging": {"cursors": {"after": f"cursor-{page}"}}}
                 for page in range(1, 3)]
        pages.append({"data": [{"id": "new-3-a"}, {"id": "old-a"},
                               {"id": "old-b"}, {"id": "old-c"}],
                      "paging": {"cursors": {"after": "cursor-3"}}})
        result = plan_cursor_recovery(["old-a", "old-b"], pages)
        self.assertEqual(result["status"], "page_limit_reached")
        self.assertEqual(result["pages_examined"], 3)
        self.assertEqual(result["network_requests"], 0)

    def test_fourth_recovery_page_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "three-page"):
            plan_cursor_recovery(["old"], [{"data": []}] * 4)

    def test_multiple_deleted_posts_do_not_create_false_anchor(self):
        pages = [{"data": [{"id": f"new-{n}-{i}"} for i in range(4)],
                  "paging": {"cursors": {"after": f"cursor-{n}"}}}
                 for n in range(1, 4)]
        result = plan_cursor_recovery(["deleted-a", "deleted-b"], pages)
        self.assertEqual(result["status"], "coverage_gap")
        self.assertEqual(result["deleted_media_ids_confirmed"], [])


if __name__ == "__main__":
    unittest.main()
