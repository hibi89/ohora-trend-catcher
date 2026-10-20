"""Run the RAW suite with outbound socket connections blocked.

Optional named groups divide Windows CI by test *module*, preserving every
case exactly once across the five independent jobs. Running without --group
continues to execute the complete suite.
"""
import argparse
import faulthandler
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


GROUP_NAMES = ("orchestrator", "roster", "report", "observations", "remaining")
_MODULE_GROUPS = {
    "test_three_api_daily_orchestrator": "orchestrator",
    "test_three_api_full_roster_batch": "roster",
    "test_three_api_operator_report": "report",
    "test_three_api_p2_observations": "observations",
}


def no_network(*args, **kwargs):
    raise AssertionError('Network access is forbidden in offline tests')


def iter_cases(suite):
    """Walk arbitrary nested unittest suites without dropping individual cases."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def group_for_case(case):
    module = case.__class__.__module__.rsplit(".", 1)[-1]
    return _MODULE_GROUPS.get(module, "remaining")


def select_group(suite, group):
    """Return an independently runnable suite; reject empty shards."""
    if group is None:
        return suite
    selected = [case for case in iter_cases(suite) if group_for_case(case) == group]
    if not selected:
        raise ValueError(f"Offline test group {group!r} has no tests")
    return unittest.TestSuite(selected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=GROUP_NAMES,
                        help='Run exactly one module-based CI shard (default: all)')
    parser.add_argument("--list-groups", action="store_true",
                        help="Print discovered per-group test counts without running tests")
    args = parser.parse_args()

    # Opt-in stack dumps help diagnose CI stalls without touching production paths.
    watchdog_seconds = int(os.environ.get('OFFLINE_TEST_WATCHDOG_SECONDS', '0'))
    if watchdog_seconds > 0:
        faulthandler.dump_traceback_later(watchdog_seconds, repeat=True)
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
    total = suite.countTestCases()
    # Fail closed if a discovered test is not assigned to exactly one CI shard.
    all_cases = list(iter_cases(suite))
    shard_counts = {name: sum(group_for_case(case) == name for case in all_cases)
                    for name in GROUP_NAMES}
    if sum(shard_counts.values()) != total or any(count == 0 for count in shard_counts.values()):
        raise RuntimeError(f"Invalid offline shard partition: {shard_counts}, total={total}")
    if args.list_groups:
        for name in GROUP_NAMES:
            print(f"{name}: {shard_counts[name]} tests", flush=True)
        print(f"total: {total} tests", flush=True)
        sys.exit(0)
    suite = select_group(suite, args.group)
    print(f"Offline suite: group={args.group or 'all'} selected={suite.countTestCases()} discovered={total}",
          flush=True)
    with patch('socket.socket.connect', no_network), \
         patch('socket.socket.connect_ex', no_network), \
         patch('socket.create_connection', no_network):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if watchdog_seconds > 0:
        faulthandler.cancel_dump_traceback_later()
    sys.exit(not result.wasSuccessful())
