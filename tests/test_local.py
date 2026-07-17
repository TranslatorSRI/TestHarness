"""Tests for running the harness locally without a Reporter or Slacker.

These cover the ``--local`` switch and the fall-back to local stand-ins when
the Information Radiator / Slack aren't configured, including saving the CSV
and JSON results to disk.
"""

import json
import os

from test_harness.main import main
from test_harness.reporter import LocalReporter, Reporter
from test_harness.slacker import LocalSlacker, Slacker

from .helpers.example_tests import example_test_cases


def test_slacker_is_configured(monkeypatch):
    """Slack counts as configured only with a webhook plus token and channel."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL", raising=False)
    assert not Slacker.is_configured()
    assert not Slacker.is_configured(url="http://hook")  # missing token/channel
    assert Slacker.is_configured(url="http://hook", token="t", slack_channel="c")

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://hook")
    monkeypatch.setenv("SLACK_TOKEN", "t")
    monkeypatch.setenv("SLACK_CHANNEL", "c")
    assert Slacker.is_configured()


def test_reporter_is_configured(monkeypatch):
    """The reporter needs both a base URL and a refresh token."""
    monkeypatch.delenv("ZE_BASE_URL", raising=False)
    monkeypatch.delenv("ZE_REFRESH_TOKEN", raising=False)
    assert not Reporter.is_configured()
    assert not Reporter.is_configured(base_url="http://ir")  # missing token
    assert Reporter.is_configured(base_url="http://ir", refresh_token="tok")


def test_local_reporter_makes_no_network_calls():
    """The LocalReporter hands out sequential ids and never authenticates."""
    reporter = LocalReporter()
    reporter.get_auth()  # no-op, must not raise
    assert reporter.authenticated_client is None
    reporter.create_test_run("ci", "my-suite")
    assert reporter.test_run_id == "local"
    assert reporter.test_name.startswith("my-suite:")
    assert reporter.create_test(None, None) == 1
    assert reporter.create_test(None, None) == 2
    assert reporter.finish_test("2", "PASSED") == "PASSED"
    assert reporter.finish_test_run() is None


def test_local_slacker_saves_results_to_disk(tmp_path):
    """CSV/JSON results and binary artifacts are written under output_dir."""
    slacker = LocalSlacker(output_dir=str(tmp_path))
    slacker.post_notification(["hello"])  # logged, not posted

    csv_path = slacker.upload_test_results_file("my-suite: 2026", "csv", "a,b\n1,2\n")
    json_path = slacker.upload_test_results_file(
        "my-suite: 2026", "json", {"passed": 3}
    )
    bin_path = slacker.upload_binary_file("chart.png", b"\x89PNG")

    assert os.path.exists(csv_path)
    assert os.path.exists(json_path)
    assert os.path.exists(bin_path)
    # filenames are sanitized (no spaces/colons)
    assert " " not in os.path.basename(csv_path)
    assert ":" not in os.path.basename(csv_path)
    with open(csv_path) as f:
        assert f.read() == "a,b\n1,2\n"
    with open(json_path) as f:
        assert json.load(f) == {"passed": 3}


def test_local_slacker_does_not_clobber_same_name(tmp_path):
    """Two results sharing a base name both survive (eg acceptance + perf json)."""
    slacker = LocalSlacker(output_dir=str(tmp_path))
    first = slacker.upload_test_results_file("run", "json", {"a": 1})
    second = slacker.upload_test_results_file("run", "json", {"b": 2})
    assert first != second
    assert os.path.exists(first) and os.path.exists(second)


def test_main_local_flag_uses_local_stand_ins(mocker, monkeypatch, tmp_path):
    """`--local` forces local reporter/slacker even when services are configured."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://hook")
    monkeypatch.setenv("SLACK_TOKEN", "t")
    monkeypatch.setenv("SLACK_CHANNEL", "c")
    monkeypatch.setenv("ZE_BASE_URL", "http://ir")
    monkeypatch.setenv("ZE_REFRESH_TOKEN", "tok")

    run_tests = mocker.patch("test_harness.main.run_tests", return_value={})
    reporter_cls = mocker.patch("test_harness.main.Reporter", wraps=Reporter)
    slacker_cls = mocker.patch("test_harness.main.Slacker", wraps=Slacker)
    local_reporter = mocker.patch(
        "test_harness.main.LocalReporter", wraps=LocalReporter
    )
    local_slacker = mocker.patch("test_harness.main.LocalSlacker", wraps=LocalSlacker)

    main(
        {
            "tests": example_test_cases,
            "suite": "testing",
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
            "local": True,
            "output_dir": str(tmp_path),
        }
    )

    run_tests.assert_called_once()
    local_reporter.assert_called_once()
    local_slacker.assert_called_once()
    # The real (networked) reporter/slacker are never constructed in local mode.
    reporter_cls.assert_not_called()
    slacker_cls.assert_not_called()
