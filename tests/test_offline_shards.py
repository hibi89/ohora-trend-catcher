"""Offline guarantees for unique, deterministic parallel Windows CI groups."""
import importlib.util
from pathlib import Path
import unittest

_runner_path = Path(__file__).with_name("run_offline.py")
_spec = importlib.util.spec_from_file_location("run_offline_shards", _runner_path)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _case(module_name, class_name="Fixture"):
    klass = type(class_name, (unittest.TestCase,), {
        "__module__": module_name,
        "runTest": lambda self: None,
    })
    return klass()


def _roster_case(method):
    cls = type("FullRosterBatchTests", (unittest.TestCase,), {
        "__module__": "test_three_api_full_roster_batch",
        method: lambda self: None,
    })
    return cls(method)


def _orchestrator_case_for(group):
    # Independent synthetic test IDs for all subshards: do not rely on an
    # arbitrary CPython hash seed or module discovery order.
    for index in range(200):
        case = _case("test_three_api_daily_orchestrator", f"ShardFixture{index}")
        if runner.group_for_case(case) == group:
            return case
    raise AssertionError(f"No synthetic case found for {group}")


class OfflineShardTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator_cases = [
            _orchestrator_case_for(group) for group in runner.ORCHESTRATOR_GROUPS
        ]
        self.roster_cases = [
            _roster_case(method) for method in runner._ROSTER_METHOD_GROUPS
        ]
        self.heavy_cases = [
            _case("test_three_api_operator_report"),
            _case("test_three_api_p2_observations"),
            _case("test_three_api_epoch_contract_projection"),
        ]
        self.remaining_cases = [
            _case("test_other_runtime"),
            _case("tests.test_another_module"),
        ]
        self.cases = self.orchestrator_cases + self.roster_cases + self.heavy_cases + self.remaining_cases
        self.suite = unittest.TestSuite([
            unittest.TestSuite(self.cases[:5]),
            unittest.TestSuite(self.cases[5:]),
        ])

    def test_each_case_appears_in_exactly_one_group(self):
        partitions = {
            group: list(runner.iter_cases(runner.select_group(self.suite, group)))
            for group in runner.GROUP_NAMES
        }
        flattened = [case for group in runner.GROUP_NAMES for case in partitions[group]]
        self.assertEqual(len(flattened), len(self.cases))
        self.assertEqual(set(flattened), set(self.cases))
        self.assertEqual(len({case.id() for case in flattened}), len(self.cases))

    def test_all_four_orchestrator_shards_are_reachable(self):
        for group, case in zip(runner.ORCHESTRATOR_GROUPS, self.orchestrator_cases):
            self.assertEqual(runner.group_for_case(case), group)
            self.assertEqual(
                list(runner.iter_cases(runner.select_group(self.suite, group))), [case]
            )

    def test_roster_groups_cover_all_five_methods_once(self):
        parts = [
            list(runner.iter_cases(runner.select_group(self.suite, group)))
            for group in runner.ROSTER_GROUPS
        ]
        self.assertEqual([len(part) for part in parts], [2, 2, 1])
        self.assertEqual(
            {case.id() for part in parts for case in part},
            {case.id() for case in self.roster_cases},
        )

    def test_other_heavy_modules_are_isolated(self):
        for group, case in zip(("report", "observations", "projection"),
                               self.heavy_cases):
            self.assertEqual(
                list(runner.iter_cases(runner.select_group(self.suite, group))), [case]
            )

    def test_remaining_group_has_all_other_modules(self):
        self.assertEqual(
            list(runner.iter_cases(runner.select_group(self.suite, "remaining"))),
            self.remaining_cases,
        )

    def test_orchestrator_partition_is_stable_under_discovery_reordering(self):
        for case in self.orchestrator_cases:
            before = runner.group_for_case(case)
            after = runner.group_for_case(case)
            self.assertEqual(before, after)
        reverse_suite = unittest.TestSuite(reversed(self.cases))
        for group in runner.GROUP_NAMES:
            expected = set(runner.iter_cases(runner.select_group(self.suite, group)))
            observed = set(runner.iter_cases(runner.select_group(reverse_suite, group)))
            self.assertEqual(expected, observed)

    def test_empty_group_fails_closed(self):
        with self.assertRaises(ValueError):
            runner.select_group(unittest.TestSuite([self.cases[0]]), "roster")

    def test_no_group_preserves_all_cases(self):
        self.assertIs(runner.select_group(self.suite, None), self.suite)


    def test_serial_selection_new_cases_never_fall_back_to_remaining(self):
        import zlib
        for name in (
            "test_selects_a_then_b_round_robin_after_durable_success",
            "test_usage_85_halts_only_fixture_lane_and_b_is_available",
            "test_pre_send_pending_usage_is_durable_and_released_by_safe_evidence",
            "test_bound_capture_recovers_after_lease_expiry_without_resend",
        ):
            cls = type("SerialFixtureSelectionTests", (unittest.TestCase,), {
                "__module__": "test_three_api_serial_selection",
                name: lambda self: None,
            })
            case = cls(name)
            expected = runner.ORCHESTRATOR_GROUPS[
                zlib.crc32(case.id().encode("utf-8"))
                % len(runner.ORCHESTRATOR_GROUPS)]
            self.assertEqual(runner.group_for_case(case), expected)
            self.assertNotEqual(runner.group_for_case(case), "remaining")


if __name__ == "__main__":
    unittest.main()
