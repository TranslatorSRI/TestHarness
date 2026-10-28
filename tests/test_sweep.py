"""Tests for the sequential multi-target sweep entrypoint.

A sweep is what a single scheduled Job runs: several harness invocations, one
per service, strictly in order. The properties that matter are that the order is
the order given, that one failing target doesn't cost you the rest, and that the
Job still fails when any target failed.
"""

import os
from unittest import mock

import pytest

from test_harness.sweep import (
    TARGETS_ENV,
    build_command,
    cli,
    parse_targets,
    run_sweep,
)

TARGETS = [
    ("ars", "https://ars.ci.transltr.io"),
    ("aragorn", "https://aragorn.ci.transltr.io"),
    ("arax", "https://arax.ci.transltr.io"),
    ("bte", "https://bte.ci.transltr.io"),
]


def test_targets_keep_the_order_they_were_given():
    """Order is the sweep's contract: it's the order services get loaded."""
    specs = [f"{name}={url}" for name, url in TARGETS]
    assert parse_targets(specs, env={}) == TARGETS
    # reversing the input reverses the sweep
    assert parse_targets(list(reversed(specs)), env={}) == list(reversed(TARGETS))


def test_targets_fall_back_to_the_environment():
    """A CronJob changes the sweep by editing an env var, not the image."""
    env = {TARGETS_ENV: "ars=http://a, aragorn=http://b"}
    assert parse_targets([], env=env) == [("ars", "http://a"), ("aragorn", "http://b")]
    # arguments win over the environment
    assert parse_targets(["bte=http://c"], env=env) == [("bte", "http://c")]


@pytest.mark.parametrize("spec", ["ars", "=http://a", "ars=", ""])
def test_malformed_targets_are_rejected(spec):
    with pytest.raises(ValueError):
        parse_targets([spec], env={})


def test_duplicate_targets_are_rejected():
    """Two runs against one service would just clobber each other's output."""
    with pytest.raises(ValueError):
        parse_targets(["ars=http://a", "ars=http://b"], env={})


def test_each_target_gets_its_own_output_dir():
    """HelmsDeep names its raw files after the run type and test case, not the
    host, so every ARA in a sweep writes the same names."""
    dirs = [
        build_command(name, url, "perf", "out", [], download=False)[
            build_command(name, url, "perf", "out", [], download=False).index(
                "--output_dir"
            )
            + 1
        ]
        for name, url in TARGETS
    ]
    assert dirs == [os.path.join("out", name) for name, _ in TARGETS]
    assert len(set(dirs)) == len(TARGETS)


def test_globals_precede_the_subcommand():
    """The harness parses its global flags before the subcommand, so a
    passed-through flag after `load` would be rejected."""
    cmd = build_command(
        "ars",
        "http://a",
        "local_performance",
        "out",
        ["--performance_profile", "mixed"],
        download=False,
    )
    assert cmd[-2:] == ["load", "local_performance"]
    assert cmd.index("--performance_profile") < cmd.index("load")


def test_download_mode_uses_the_download_subcommand():
    cmd = build_command(
        "ars", "http://a", "performance_tests", "out", [], download=True
    )
    assert cmd[-2:] == ["download", "performance_tests"]

    cmd = build_command(
        "ars", "http://a", "s", "out", [], download=False, tests_url="http://t.zip"
    )
    # a tests_url implies downloading, and is passed to the subcommand
    assert "download" in cmd
    assert cmd[-2:] == ["--tests_url", "http://t.zip"]


def test_every_target_runs_even_when_one_fails():
    """A broken ARS must not cost you the ARA numbers."""
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd[cmd.index("--target") + 1])
        return mock.Mock(returncode=1 if calls[-1] == "ars" else 0)

    with mock.patch("test_harness.sweep.subprocess.run", side_effect=fake_run):
        code = run_sweep(TARGETS, "perf")

    assert calls == [name for name, _ in TARGETS], "a failure stopped the sweep"
    assert code == 1, "the Job has to fail when a target failed"


def test_runs_are_sequential():
    """Overlapping runs would double-load whatever sits under both services."""
    running = []
    overlaps = []

    def fake_run(cmd, *a, **kw):
        running.append(cmd)
        if len(running) > 1:
            overlaps.append(list(running))
        running.pop()
        return mock.Mock(returncode=0)

    with mock.patch("test_harness.sweep.subprocess.run", side_effect=fake_run):
        assert run_sweep(TARGETS, "perf") == 0
    assert not overlaps


def test_a_clean_sweep_exits_zero():
    with mock.patch(
        "test_harness.sweep.subprocess.run", return_value=mock.Mock(returncode=0)
    ):
        assert run_sweep(TARGETS, "perf") == 0


def test_an_unlaunchable_harness_is_reported_per_target():
    """If the binary isn't on PATH the log should still name every target."""
    with mock.patch(
        "test_harness.sweep.subprocess.run", side_effect=OSError("no such file")
    ):
        assert run_sweep(TARGETS[:2], "perf") == 1


def test_cli_requires_targets(capsys):
    with pytest.raises(SystemExit):
        cli(["--suite", "perf"])
    assert TARGETS_ENV in capsys.readouterr().err


def test_cli_passes_through_args_after_the_separator():
    with mock.patch("test_harness.sweep.run_sweep", return_value=0) as sweep:
        cli(
            [
                "--suite",
                "local_performance",
                "ars=http://a",
                "--",
                "--performance_profile",
                "mixed",
            ]
        )
    kwargs = sweep.call_args.kwargs
    # the `--` separator itself must not reach the harness
    assert kwargs["harness_args"] == ["--performance_profile", "mixed"]


def test_passthrough_splits_at_the_separator():
    """`--` belongs to the sweep; it must not reach the harness, and the target
    list must not swallow what follows it."""
    from test_harness.sweep import split_passthrough

    sweep, harness = split_passthrough(
        ["--suite", "s", "ars=http://a", "--", "--log_level", "INFO"]
    )
    assert sweep == ["--suite", "s", "ars=http://a"]
    assert harness == ["--log_level", "INFO"]

    # no separator at all
    assert split_passthrough(["--suite", "s", "ars=http://a"]) == (
        ["--suite", "s", "ars=http://a"],
        [],
    )


def test_a_subcommand_flag_after_the_separator_is_rejected_with_guidance(capsys):
    """`--tests_url` belongs to the harness's `download` subcommand, so the
    sweep's passthrough (which goes in front of the subcommand) can't carry it.
    Argparse's own "unrecognized arguments" doesn't say the position is the
    problem, and this is edited by hand in a YAML manifest."""
    for arg in ("--tests_url=https://x/y.zip", "--tests_url", "--tests_dir=/suites"):
        with pytest.raises(SystemExit):
            cli(["--suite", "s", "ars=http://a", "--", arg])
        err = capsys.readouterr().err
        assert "cannot be passed through after `--`" in err
        assert "before the `--`" in err


def test_tests_url_goes_after_the_download_subcommand():
    """The flag is only valid there; in front of it argparse rejects the run."""
    cmd = build_command(
        "ars",
        "http://a",
        "perf",
        "out",
        [],
        download=False,
        tests_url="https://x/y.zip",
    )
    assert cmd.index("--tests_url") > cmd.index("download")
    assert cmd[cmd.index("--tests_url") + 1] == "https://x/y.zip"


def test_tests_dir_goes_after_the_load_subcommand():
    cmd = build_command(
        "ars", "http://a", "perf", "out", [], download=False, tests_dir="/suites"
    )
    assert cmd.index("--tests_dir") > cmd.index("load")
    assert cmd[cmd.index("--tests_dir") + 1] == "/suites"
