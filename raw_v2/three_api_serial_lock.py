"""Cross-process serial tick lock for the isolated p6 offline coordinator.

No Meta transport, operational database or background scheduling.
The OS releases the lock on process death; the lock file can safely remain.
"""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path


class SerialTickBusy(RuntimeError):
    """Another runner owns the serial dispatch gate."""


@contextmanager
def serial_tick_lock(state_db):
    """Nonblocking OS-owned exclusive lock shared by all runners for one DB.

    Both the serial orchestrator and legacy explicit single-lane steps use
    this gate. It intentionally locks *outside* SQLite transactions so a
    process crash releases the gate without changing captured RAW evidence.
    """
    lock_path = Path(str(Path(state_db).resolve()) + ".serial.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SerialTickBusy("Serial tick already active") from None
        elif os.name == "posix":
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                raise SerialTickBusy("Serial tick already active") from None
        else:
            raise RuntimeError("Unsupported serial lock platform")
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def single_dispatch_gate(method):
    """Guard both manual step() and step_serial() against other processes."""

    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            with serial_tick_lock(self.coordinator.path):
                return method(self, *args, **kwargs)
        except SerialTickBusy:
            if method.__name__ == "step_serial":
                return {"status": "serial_busy", "selected_lane": None}
            lane = args[0] if args else kwargs.get("lane")
            return {"status": "serial_busy", "lane": lane}

    return guarded
