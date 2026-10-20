"""Run the RAW suite with outbound socket connections blocked."""
import faulthandler
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


def no_network(*args, **kwargs):
    raise AssertionError('Network access is forbidden in offline tests')


if __name__ == '__main__':
    # Opt-in stack dumps help diagnose CI stalls without touching production paths.
    watchdog_seconds = int(os.environ.get('OFFLINE_TEST_WATCHDOG_SECONDS', '0'))
    if watchdog_seconds > 0:
        faulthandler.dump_traceback_later(watchdog_seconds, repeat=True)
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
    with patch('socket.socket.connect', no_network), \
         patch('socket.socket.connect_ex', no_network), \
         patch('socket.create_connection', no_network):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if watchdog_seconds > 0:
        faulthandler.cancel_dump_traceback_later()
    sys.exit(not result.wasSuccessful())
