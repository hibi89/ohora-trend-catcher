"""Run the RAW suite with outbound socket connections blocked.

Optional named groups divide Windows CI by stable unittest identities, preserving
every case exactly once across independent jobs. Running without --group
continues to execute the complete suite.
"""
import argparse
import faulthandler
import os
from pathlib import Path
import sys
import unittest
import zlib
from unittest.mock import patch


# Split slow Windows SQLite orchestrator tests into stable, independent shards.
# Assignment uses the full unittest ID: deterministic across OS, enumeration and
# restart; the 480s watchdog remains in force for genuine stalls.
ORCHESTRATOR_GROUPS = ("orchestrator", "orchestrator-2", "orchestrator-3", "orchestrator-4")
ROSTER_GROUPS = ("roster", "roster-2", "roster-3")
# The five full-roster 294-account fixtures take ~100-132 seconds each on
# Windows. Explicitly distribute known tests 2/2/1 to stay under 480 seconds.
# New full-roster tests go to the first group, never silently disappear.
_ROSTER_METHOD_GROUPS = {
    "test_294_accounts_three_lane_full_front_catchup_and_restart": "roster",
    "test_restart_does_not_duplicate_294_account_front_evidence": "roster",
    "test_repeated_reconcile_does_not_inflate_full_roster_raw_counts": "roster-2",
    "test_restart_does_not_claim_coverage_when_last_account_missing": "roster-2",
    "test_missing_account_294_blocks_global_gap_without_lying_about_coverage": "roster-3",
}
GROUP_NAMES = ORCHESTRATOR_GROUPS + ROSTER_GROUPS + (
    "report", "observations", "projection", "remaining"
)
_ORCHESTRATOR_MODULES = frozenset({
    "test_three_api_daily_orchestrator",
    "test_three_api_serial_catchup",
    "test_three_api_serial_lock",
    "test_three_api_serial_contract_bridge",
    "test_three_api_serial_selection",
})
_MODULE_GROUPS = {
    "test_three_api_operator_report": "report",
    "test_three_api_p2_observations": "observations",
    "test_three_api_epoch_contract_projection": "projection",
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


def unique_discovered_cases(suite):
    """Execute each imported unittest case once; reject conflicting identities.

    Some test modules re-export a TestCase class imported from another
    module. unittest discovery legitimately finds the same class and method
    twice. Preserve one execution, but never conflate distinct test methods
    or function-test callbacks that happen to share a textual id.
    """
    seen={}
    unique=[]
    for case in iter_cases(suite):
        identity=case.id()
        prior=seen.get(identity)
        if prior is not None:
            if (prior.__class__ is not case.__class__
                    or getattr(prior, "_testMethodName", None)
                    != getattr(case, "_testMethodName", None)
                    or getattr(prior, "_testFunc", None)
                    is not getattr(case, "_testFunc", None)):
                raise RuntimeError(f"Conflicting offline test identity: {identity}")
            continue
        seen[identity]=case
        unique.append(case)
    return unique


def group_for_case(case):
    module = case.__class__.__module__.rsplit(".", 1)[-1]
    if module == "test_three_api_full_roster_batch":
        return _ROSTER_METHOD_GROUPS.get(
            getattr(case, "_testMethodName", ""), "roster"
        )
    if module in _ORCHESTRATOR_MODULES:
        # Avoid putting all 70 expensive synthetic DB cases into a single
        # 480-second Windows job. Never skip/re-execute a case across shards.
        index = zlib.crc32(case.id().encode("utf-8")) % len(ORCHESTRATOR_GROUPS)
        return ORCHESTRATOR_GROUPS[index]
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
    watchdog_raw=os.environ.get('OFFLINE_TEST_WATCHDOG_SECONDS', '0')
    try:
        watchdog_seconds=int(watchdog_raw)
    except ValueError as exc:
        raise SystemExit("OFFLINE_TEST_WATCHDOG_SECONDS must be an integer") from exc
    if watchdog_seconds < 0:
        raise SystemExit("OFFLINE_TEST_WATCHDOG_SECONDS cannot be negative")
    if watchdog_seconds > 0:
        faulthandler.dump_traceback_later(watchdog_seconds, repeat=True, exit=True)
    discovered = unittest.defaultTestLoader.discover(
        str(Path(__file__).parent), pattern='test_*.py')
    original_count=discovered.countTestCases()
    # Imported/re-exported TestCase classes otherwise produce duplicate
    # test IDs. Keep all distinct tests and execute each exact identity once.
    all_cases=unique_discovered_cases(discovered)
    suite=unittest.TestSuite(all_cases)
    total=len(all_cases)
    shard_counts = {name: sum(group_for_case(case) == name for case in all_cases)
                    for name in GROUP_NAMES}
    if (sum(shard_counts.values()) != total
            or any(count == 0 for count in shard_counts.values())
            or any(group_for_case(case) not in GROUP_NAMES for case in all_cases)):
        raise RuntimeError(f"Invalid offline shard partition: {shard_counts}, total={total}")
    # The expensive observation tests must never silently fall back into
    # 'remaining', which previously exceeded the Windows watchdog budget.
    observation_cases=[case for case in all_cases if
        case.__class__.__module__.rsplit(".",1)[-1]=="test_three_api_p2_observations"]
    if not observation_cases or any(group_for_case(case)!="observations"
                                    for case in observation_cases):
        raise RuntimeError("Observation regression shard assignment is missing")
    case_ids=[case.id() for case in all_cases]
    if sum(shard_counts.values())!=len(set(case_ids)):
        raise RuntimeError("Offline shard partition does not cover unique tests")
    if args.list_groups:
        for name in GROUP_NAMES:
            print(f"{name}: {shard_counts[name]} tests", flush=True)
        print(f"total: {total} distinct tests (discovered {original_count})", flush=True)
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
