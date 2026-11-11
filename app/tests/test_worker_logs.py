"""
One log file per worker: each process claims a free worker number by
locking logs/worker<N>.lock, and writes logs/app-worker<N>.log.

Separate processes are started like real uvicorn workers; logs go to a
temporary folder, never to the real logs/ folder.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A "worker": sets up logging, writes one line, then stays alive
# (holding its number) until its stdin is closed.
WORKER = (
    "import sys, logging;"
    "from app.utilities.log import Applogger;"
    "Applogger.setup();"
    "logging.getLogger('test').info('hello from worker');"
    "print(Applogger.log_file, flush=True);"
    "sys.stdin.read()"
)


def start_worker(log_dir):
    env = {**os.environ, "LOG_DIR": str(log_dir)}
    process = subprocess.Popen(
        [sys.executable, "-c", WORKER], cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    log_file = process.stdout.readline().strip()
    return process, os.path.basename(log_file)


def stop(process):
    process.stdin.close()
    process.wait(timeout=30)


def test_two_workers_get_two_files(tmp_path):
    first, first_file = start_worker(tmp_path)
    second, second_file = start_worker(tmp_path)
    try:
        assert {first_file, second_file} == {"app-worker1.log", "app-worker2.log"}
    finally:
        stop(first)
        stop(second)

    for name in (first_file, second_file):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "hello from worker" in text
        assert "[pid=" in text                      # every line shows its worker


def test_each_file_has_lines_from_one_process_only(tmp_path):
    first, first_file = start_worker(tmp_path)
    second, second_file = start_worker(tmp_path)
    stop(first)
    stop(second)

    for name in (first_file, second_file):
        lines = (tmp_path / name).read_text(encoding="utf-8").splitlines()
        pids = {line.split("[pid=")[1].split("]")[0] for line in lines}
        assert len(pids) == 1


def test_restarted_worker_reuses_the_free_number(tmp_path):
    first, _ = start_worker(tmp_path)
    second, second_file = start_worker(tmp_path)
    assert second_file == "app-worker2.log"

    stop(second)                                    # worker 2 stops ...
    time.sleep(0.5)
    restarted, restarted_file = start_worker(tmp_path)
    try:
        assert restarted_file == "app-worker2.log"  # ... and its number is reused
    finally:
        stop(first)
        stop(restarted)


def test_killed_worker_releases_its_number(tmp_path):
    crashed, crashed_file = start_worker(tmp_path)
    crashed.kill()                                  # no clean shutdown
    crashed.wait(timeout=30)
    time.sleep(0.5)

    replacement, replacement_file = start_worker(tmp_path)
    try:
        assert replacement_file == crashed_file == "app-worker1.log"
    finally:
        stop(replacement)
