import logging
import os
from logging.handlers import RotatingFileHandler

# ---------------------------------------------
# One log file per worker
# ---------------------------------------------
# With uvicorn --workers 2 there are two Python processes. If both
# write to the same app.log, RotatingFileHandler breaks: when one
# worker renames app.log -> app.log.1 at 10 MB, the other keeps
# writing to the renamed file, lines get lost, and on Windows the
# rename fails because the file is open in the other process.
#
# So each worker writes its own file: logs/app-worker1.log,
# logs/app-worker2.log, ... A worker claims a number by LOCKING
# logs/worker<N>.lock (kept open while the process runs). The OS frees
# the lock when the process stops, so a restarted worker gets a free
# number again and the file names stay the same across restarts
# (a PID-based name would create a new file on every restart).

if os.name == "nt":
    import msvcrt

    def _try_lock(handle) -> bool:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
else:
    import fcntl

    def _try_lock(handle) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


class Applogger:
    """Sets up logging once for the whole application."""

    # LOG_DIR can be set in .env (an absolute path on the server is best,
    # so logs do not depend on the folder uvicorn is started from).
    # Read in setup(), after main.py has loaded .env.
    LOG_DIR = "logs"

    # %(process)d: the worker's PID on every line, so lines in the
    # console (where both workers write) can still be told apart.
    LOG_FORMAT = "%(asctime)s %(levelname)s [pid=%(process)d] [%(name)s] %(message)s"

    MAX_BYTES = 10 * 1024 * 1024
    BACKUP_COUNT = 5

    # How many worker numbers to try (spare ones cover a restarting
    # worker whose old process has not fully exited yet).
    MAX_WORKER_SLOTS = 16

    # Kept open for the life of the process: closing it frees the number.
    _slot_lock_handle = None
    log_file: str | None = None

    @classmethod
    def _claim_worker_slot(cls) -> int | None:
        """Lock the first free logs/worker<N>.lock and return N."""
        for slot in range(1, cls.MAX_WORKER_SLOTS + 1):
            path = os.path.join(cls.LOG_DIR, f"worker{slot}.lock")
            handle = open(path, "a+")
            if _try_lock(handle):
                cls._slot_lock_handle = handle
                return slot
            handle.close()
        return None

    @classmethod
    def setup(cls):
        """Call this one time per process, when the app starts."""
        if cls.log_file is not None:
            return   # already set up in this process

        cls.LOG_DIR = os.getenv("LOG_DIR", cls.LOG_DIR)
        os.makedirs(cls.LOG_DIR, exist_ok=True)

        slot = cls._claim_worker_slot()
        if slot is not None:
            name = f"app-worker{slot}.log"
        else:
            # All numbers taken (should not happen): fall back to the PID.
            name = f"app-pid{os.getpid()}.log"
        cls.log_file = os.path.join(cls.LOG_DIR, name)

        console_handler = logging.StreamHandler()
        file_handler = RotatingFileHandler(
            cls.log_file,
            maxBytes=cls.MAX_BYTES,
            backupCount=cls.BACKUP_COUNT,
            encoding="utf-8",
        )
        logging.basicConfig(
            level=logging.INFO,
            format=cls.LOG_FORMAT,
            handlers=[console_handler, file_handler],
        )
        logging.getLogger(__name__).info(
            "logging_started pid=%s file=%s", os.getpid(), cls.log_file,
        )

    @staticmethod
    def get_loggerdata(name):

        """Get a logger for one file. Pass __name__ as the name."""
        return logging.getLogger(name)
