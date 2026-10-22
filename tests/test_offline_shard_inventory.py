"""Offline CI shard integrity and unittest re-export regression checks."""
import unittest

from run_offline import (
    GROUP_NAMES, group_for_case, iter_cases, select_group,
    unique_discovered_cases,
)


class InventoryTests(unittest.TestCase):
    def test_identical_reexported_case_runs_once(self):
        class Shared(unittest.TestCase):
            def test_once(self):
                pass

        copies=unittest.TestSuite([Shared("test_once"), Shared("test_once")])
        unique=unique_discovered_cases(copies)
        self.assertEqual(len(unique),1)
        self.assertEqual(unique[0].id(),Shared("test_once").id())

    def test_distinct_test_methods_are_preserved(self):
        class Shared(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass

        unique=unique_discovered_cases(unittest.TestSuite([
            Shared("test_one"),Shared("test_two"),Shared("test_one")]))
        self.assertEqual([case._testMethodName for case in unique],
                         ["test_one","test_two"])

    def test_colliding_identifiers_from_different_classes_fail_closed(self):
        cls_one=type("Collision",(unittest.TestCase,),{
            "__module__":"fake_shared_module",
            "test_one":lambda self:None})
        cls_two=type("Collision",(unittest.TestCase,),{
            "__module__":"fake_shared_module",
            "test_one":lambda self:None})
        suite=unittest.TestSuite([cls_one("test_one"),cls_two("test_one")])
        with self.assertRaisesRegex(RuntimeError,
                                    "Conflicting offline test identity"):
            unique_discovered_cases(suite)

    def test_case_iteration_includes_nested_suites(self):
        class Nested(unittest.TestCase):
            def test_case(self):
                pass

        suite=unittest.TestSuite([
            unittest.TestSuite([Nested("test_case")]),
            unittest.TestSuite([Nested("test_case")])])
        self.assertEqual(len(list(iter_cases(suite))),2)
        self.assertEqual(len(unique_discovered_cases(suite)),1)

    def test_group_selection_cannot_duplicate_remaining_tests(self):
        remaining_groups = tuple(
            group for group in GROUP_NAMES if group.startswith("remaining")
        )
        self.assertTrue(remaining_groups)

        expected_by_group = {}
        for group in remaining_groups:
            for index in range(10_000):
                cls = type(
                    f"RemainingFixture{index}",
                    (unittest.TestCase,),
                    {"test_remaining": lambda self: None},
                )
                case = cls("test_remaining")
                if group_for_case(case) == group:
                    expected_by_group[group] = case
                    break
            else:
                self.fail(f"No synthetic test identity found for {group}")

        cases = list(expected_by_group.values())
        duplicated_nested_suite = unittest.TestSuite([
            unittest.TestSuite([cases[0], cases[0]]),
            unittest.TestSuite(cases[1:]),
        ])
        unique = unique_discovered_cases(duplicated_nested_suite)
        self.assertEqual(
            {case.id() for case in unique},
            {case.id() for case in cases},
        )
        self.assertEqual(len(unique), len(cases))

        original_assignments = {
            case.id(): group_for_case(case) for case in unique
        }
        rerun_assignments = {
            case.id(): group_for_case(case)
            for case in unique_discovered_cases(unittest.TestSuite(reversed(unique)))
        }
        reordered_assignments = {
            case.id(): group_for_case(case)
            for case in unique_discovered_cases(unittest.TestSuite(reversed(cases)))
        }
        self.assertEqual(original_assignments, rerun_assignments)
        self.assertEqual(original_assignments, reordered_assignments)

        for group in remaining_groups:
            self.assertEqual(
                list(iter_cases(select_group(unittest.TestSuite(unique), group))),
                [expected_by_group[group]],
            )

        with self.assertRaises(ValueError):
            select_group(unittest.TestSuite(unique),"observations")


if __name__=="__main__":
    unittest.main()
