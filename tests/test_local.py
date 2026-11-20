"""Tests for running the harness locally without a radiator or Slack.

These cover the ``--local`` switch and the fall-back to local stand-ins when
the Information Radiator / Slack aren't configured, including saving the CSV
and JSON results to disk.
"""

import json
import os

from test_harness.main import main
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
    """`--local` forces the local slacker even when Slack is configured."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://hook")
    monkeypatch.setenv("SLACK_TOKEN", "t")
    monkeypatch.setenv("SLACK_CHANNEL", "c")

    run_tests = mocker.patch("test_harness.main.run_tests", return_value={})
    slacker_cls = mocker.patch("test_harness.main.Slacker", wraps=Slacker)
    local_slacker = mocker.patch("test_harness.main.LocalSlacker", wraps=LocalSlacker)

    main(
        {
            "tests": example_test_cases,
            "suite": "testing",
            "env": "ci",
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
            "local": True,
            "output_dir": str(tmp_path),
        }
    )

    run_tests.assert_called_once()
    local_slacker.assert_called_once()
    # The real (networked) slacker is never constructed in local mode.
    slacker_cls.assert_not_called()


def test_target_override_prefixes_saved_filenames(mocker, tmp_path):
    """With a target override, every saved filename is prefixed with the target."""

    def fake_run_tests(tests, collector, logger, args):
        collector.has_acceptance_results = True

    mocker.patch("test_harness.main.run_tests", side_effect=fake_run_tests)

    main(
        {
            "tests": example_test_cases,
            "suite": "testing",
            "env": "ci",
            "save_to_dashboard": False,
            "json_output": True,
            "log_level": "ERROR",
            "local": True,
            "output_dir": str(tmp_path),
            "target_url": "http://localhost:8080",
            "target": "infores:aragorn",
        }
    )

    saved = os.listdir(tmp_path)
    # acceptance json + csv, the json report, and the run saved for the
    # Information Radiator were all saved with the prefix
    assert len(saved) == 4
    assert all(name.startswith("aragorn_") for name in saved)
    assert "aragorn_test_report.json" in saved
    assert any(name.startswith("aragorn_radiator_") for name in saved)


def test_target_override_requires_both_flags(mocker, tmp_path):
    """--target_url and --target must be given together."""
    run_tests = mocker.patch("test_harness.main.run_tests")

    args = {
        "tests": example_test_cases,
        "suite": "testing",
        "env": "ci",
        "save_to_dashboard": False,
        "json_output": False,
        "log_level": "ERROR",
        "local": True,
        "output_dir": str(tmp_path),
    }
    main({**args, "target_url": "http://localhost:8080"})
    main({**args, "target": "aragorn"})
    run_tests.assert_not_called()
