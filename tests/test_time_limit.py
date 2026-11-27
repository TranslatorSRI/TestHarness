"""A run with a --time_limit stops when it runs out, and still reports what it
got: to Slack, headed as having run out of time, and to the Information
Radiator, closed as stopped at its time limit rather than left open."""

import json
import logging
import os
import subprocess
import sys
import time

import gevent
import pytest

from test_harness.main import EXIT_OK, EXIT_TIME_LIMIT, main
from test_harness.performance_test_runner import _stream

from .helpers.example_tests import example_test_cases
from .helpers.mocks import MockSlacker

BASE_ARGS = {
    "suite": "testing",
    "env": "ci",
    "save_to_dashboard": False,
    "json_output": False,
    "log_level": "ERROR",
    "tests": example_test_cases,
}


class RecordingSlacker(MockSlacker):
    def __init__(self):
        self.summaries = []

    def post_thread(self, summary, image=None, details=(), files=()):
        self.summaries.append(summary)


def _saved_run(tmp_path):
    (path,) = tmp_path.glob("radiator_*.json")
    return json.loads(path.read_text())


def test_a_run_that_runs_out_of_time_stops_and_says_so(mocker, tmp_path):
    slacker = RecordingSlacker()
    mocker.patch("test_harness.main.Slacker", return_value=slacker)

    def slow_run(tests, collector, logger, args, **kwargs):
        time.sleep(30)  # a query that never comes back

    mocker.patch("test_harness.main.run_tests", side_effect=slow_run)
    started = time.time()
    code = main({**BASE_ARGS, "time_limit": 0.01, "output_dir": str(tmp_path)})

    assert code == EXIT_TIME_LIMIT
    assert time.time() - started < 10
    (summary,) = slacker.summaries
    assert "*testing* ran out of time on *CI*" in summary
    assert "Stopped at its 0.01 min time limit" in summary
    finish = _saved_run(tmp_path)["finish"]
    assert finish["stopped"] == "time_limit"
    assert finish["ended_at"]


def test_a_run_within_its_time_limit_finishes_as_usual(mocker, tmp_path):
    slacker = RecordingSlacker()
    mocker.patch("test_harness.main.Slacker", return_value=slacker)
    mocker.patch("test_harness.main.run_tests", return_value={})
    code = main({**BASE_ARGS, "time_limit": 10, "output_dir": str(tmp_path)})

    assert code == EXIT_OK
    assert "finished on *CI*" in slacker.summaries[0]
    assert _saved_run(tmp_path)["finish"]["stopped"] is None


def test_stopping_a_load_test_stops_it_at_once(tmp_path):
    """HelmsDeep runs for an hour; at the time limit it's killed with the
    locust it started, rather than waited out."""
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    started = time.time()
    with pytest.raises(KeyboardInterrupt):
        with gevent.Timeout(2, KeyboardInterrupt()):
            _stream([sys.executable, "-c", script], logging.getLogger("t"))
    assert time.time() - started < 10
    grandchild = int(pid_file.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        # reaped by init once its parent is gone; a zombie still answers kill 0
        if _is_zombie(grandchild):
            break
        time.sleep(0.05)
    else:
        pytest.fail("the load test's own child was left running")


def _is_zombie(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] == "Z"
    except OSError:
        return True


def test_the_time_limit_must_be_positive():
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "test_harness.main",
            "--env",
            "ci",
            "--time_limit",
            "0",
            "load",
            "x",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2
    assert "--time_limit must be more than 0 minutes" in out.stderr
