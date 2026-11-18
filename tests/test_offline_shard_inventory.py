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
        class Remaining(unittest.TestCase):
            def test_remaining(self):
                pass

        cases=unique_discovered_cases(unittest.TestSuite([
            Remaining("test_remaining"),Remaining("test_remaining")]))
        self.assertIn("remaining",GROUP_NAMES)
        self.assertEqual(group_for_case(cases[0]),"remaining")
        self.assertEqual(select_group(unittest.TestSuite(cases),
                                      "remaining").countTestCases(),1)
        with self.assertRaises(ValueError):
            select_group(unittest.TestSuite(cases),"observations")


if __name__=="__main__":
    unittest.main()
