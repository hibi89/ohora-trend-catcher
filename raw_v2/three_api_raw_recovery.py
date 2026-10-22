"""Replay only previously persisted, independently validated capture envelopes.

Source acknowledgements must happen after write_capture returns. On an
interruption after RAW commit, identical replay returns already_committed.
"""
from three_api_raw_writer import write_capture, RawWriteError

def replay_saved(db, saved_capture, *, source_verified=False):
    if source_verified is not True:
        raise RawWriteError("Immutable source capture verification required")
    return write_capture(db, saved_capture)
