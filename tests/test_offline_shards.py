"""Offline guarantees for module-based parallel Windows CI groups."""
import importlib.util
from pathlib import Path
import unittest

_runner_path = Path(__file__).with_name("run_offline.py")
_spec = importlib.util.spec_from_file_location("run_offline_shards", _runner_path)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _case(module_name):
    klass = type("Fixture", (unittest.TestCase,), {
        "__module__": module_name,
        "runTest": lambda self: None,
    })
    return klass()


class OfflineShardTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            _case("test_three_api_daily_orchestrator"),
            _case("test_three_api_full_roster_batch"),
            _case("test_three_api_operator_report"),
            _case("test_three_api_p2_observations"),
            _case("test_other_runtime"),
            _case("tests.test_another_module"),
        ]
        self.suite = unittest.TestSuite([
            unittest.TestSuite(self.cases[:2]),
            unittest.TestSuite(self.cases[2:]),
        ])

    def test_each_module_appears_in_one_group_only(self):
        partitions = {
            group: list(runner.iter_cases(runner.select_group(self.suite, group)))
            for group in runner.GROUP_NAMES
        }
        flattened = [case for group in runner.GROUP_NAMES for case in partitions[group]]
        self.assertEqual(len(flattened), len(self.cases))
        self.assertEqual(set(flattened), set(self.cases))

    def test_heavy_modules_are_isolated(self):
        for group, case in zip(runner.GROUP_NAMES[:4], self.cases[:4]):
            self.assertEqual(list(runner.iter_cases(runner.select_group(self.suite, group))), [case])

    def test_remaining_group_has_all_other_modules(self):
        self.assertEqual(
            list(runner.iter_cases(runner.select_group(self.suite, "remaining"))),
            self.cases[4:],
        )

    def test_empty_group_fails_closed(self):
        with self.assertRaises(ValueError):
            runner.select_group(unittest.TestSuite([self.cases[0]]), "roster")

    def test_no_group_preserves_all_cases(self):
        self.assertIs(runner.select_group(self.suite, None), self.suite)


if __name__ == "__main__":
    unittest.main()
