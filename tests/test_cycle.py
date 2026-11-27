"""Tests for run cycles: acceptance, pathfinder and performance in one
environment, in that order, then the radiator's board in Slack."""

from unittest import mock

import pytest

from test_harness import cycle
from test_harness.cycle import Step, cli, cycle_message, digest_message, plan

CYCLE_ID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"


def test_a_cycle_runs_acceptance_then_pathfinder_then_performance():
    steps = plan(
        CYCLE_ID,
        acceptance="sprint_4_tests",
        pathfinder="pathfinder_tests",
        performance="performance_tests",
        performance_targets=[("ars", "https://ars.ci.transltr.io")],
        performance_profile="mixed",
        output_dir="out",
        harness_args=["--log_level", "INFO"],
        download=True,
    )
    assert [s.name for s in steps] == [
        "acceptance (sprint_4_tests)",
        "pathfinder (pathfinder_tests)",
        "performance (ars)",
    ]
    acceptance, pathfinder, performance = (s.cmd for s in steps)
    assert acceptance == [
        "test-harness",
        "--output_dir",
        "out/acceptance",
        "--cycle_id",
        CYCLE_ID,
        "--log_level",
        "INFO",
        "download",
        "sprint_4_tests",
    ]
    assert pathfinder[-2:] == ["download", "pathfinder_tests"]
    # the performance step is a sweep step: the target, and the profile
    assert performance[:5] == [
        "test-harness",
        "--target_url",
        "https://ars.ci.transltr.io",
        "--target",
        "ars",
    ]
    assert "--performance_profile" not in acceptance
    assert performance[performance.index("--performance_profile") + 1] == "mixed"
    assert performance[performance.index("--cycle_id") + 1] == CYCLE_ID
    assert performance[-2:] == ["download", "performance_tests"]


def test_a_cycle_can_leave_steps_out():
    steps = plan(CYCLE_ID, acceptance="a", tests_dir="suites")
    assert [s.name for s in steps] == ["acceptance (a)"]
    assert steps[0].cmd[-3:] == ["a", "--tests_dir", "suites"]


def test_several_acceptance_suites_run_in_order_before_pathfinder():
    steps = plan(
        CYCLE_ID,
        acceptance=["mvp_tests", "external_repurposing_tests"],
        pathfinder="pathfinder_tests",
        output_dir="out",
        download=True,
    )
    assert [s.name for s in steps] == [
        "acceptance (mvp_tests)",
        "acceptance (external_repurposing_tests)",
        "pathfinder (pathfinder_tests)",
    ]
    # each acceptance run reports to a directory of its own
    output_dirs = [s.cmd[s.cmd.index("--output_dir") + 1] for s in steps]
    assert output_dirs == [
        "out/acceptance/mvp_tests",
        "out/acceptance/external_repurposing_tests",
        "out/pathfinder",
    ]
    assert [s.cmd[-1] for s in steps] == [
        "mvp_tests",
        "external_repurposing_tests",
        "pathfinder_tests",
    ]
    for step in steps:
        assert step.cmd[step.cmd.index("--cycle_id") + 1] == CYCLE_ID


def test_one_acceptance_suite_keeps_its_directory():
    for acceptance in ("mvp_tests", ["mvp_tests"]):
        [step] = plan(CYCLE_ID, acceptance=acceptance, output_dir="out")
        assert step.cmd[step.cmd.index("--output_dir") + 1] == "out/acceptance"


@mock.patch("test_harness.cycle.subprocess.run")
def test_acceptance_can_be_repeated_on_the_command_line(run, monkeypatch):
    monkeypatch.delenv("RADIATOR_URL", raising=False)
    monkeypatch.delenv("RADIATOR_TOKEN", raising=False)
    run.return_value = mock.Mock(returncode=0)
    cli(
        [
            "--env",
            "ci",
            "--download",
            "--acceptance",
            "mvp_tests",
            "--acceptance",
            "external_repurposing_tests",
            "--pathfinder",
            "pathfinder_tests",
        ]
    )
    suites = [call.args[0][-1] for call in run.call_args_list]
    assert suites == ["mvp_tests", "external_repurposing_tests", "pathfinder_tests"]


def test_the_regressions_name_the_suite():
    second = {**REPORT["rows"][0], "suite": "external_repurposing_tests"}
    report = {"env": "ci", "rows": [REPORT["rows"][0], second]}
    _, thread = cycle_message(report, _ran(0, 0), None)
    changes = thread[1]
    assert "  Acceptance (sprint_4_tests):" in changes
    assert "  Acceptance (external_repurposing_tests):" in changes


def _ran(*codes):
    return [Step(f"step {i}", [], code, 30.0) for i, code in enumerate(codes)]


@mock.patch("test_harness.cycle._slacker", return_value=mock.Mock())
@mock.patch("test_harness.cycle.post_cycle_report")
@mock.patch("test_harness.cycle.subprocess.run")
def test_every_step_runs_even_when_one_fails(run, post, _slacker, monkeypatch):
    monkeypatch.setenv("RADIATOR_URL", "http://radiator")
    monkeypatch.setenv("RADIATOR_TOKEN", "tok")
    run.side_effect = [mock.Mock(returncode=2), mock.Mock(returncode=0)]
    code = cli(["--env", "ci", "--acceptance", "a", "--pathfinder", "p", "--download"])
    assert run.call_count == 2
    assert code == 1
    # the board is still posted, for the steps that ran
    cycle_id, steps = post.call_args.args[:2]
    assert [s.code for s in steps] == [2, 0]
    for call in run.call_args_list:
        cmd = call.args[0]
        assert cmd[cmd.index("--cycle_id") + 1] == cycle_id


@mock.patch("test_harness.cycle.subprocess.run")
def test_without_the_radiator_there_is_no_board(run, monkeypatch):
    monkeypatch.delenv("RADIATOR_URL", raising=False)
    monkeypatch.delenv("RADIATOR_TOKEN", raising=False)
    run.return_value = mock.Mock(returncode=0)
    with mock.patch("test_harness.cycle.post_cycle_report") as post:
        assert cli(["--env", "ci", "--acceptance", "a"]) == 0
    post.assert_not_called()


def test_env_is_required(capsys):
    with pytest.raises(SystemExit):
        cli(["--acceptance", "a"])
    assert "--env" in capsys.readouterr().err


@mock.patch("test_harness.cycle.subprocess.run")
def test_every_step_tests_the_cycles_env(run, monkeypatch):
    monkeypatch.delenv("RADIATOR_URL", raising=False)
    monkeypatch.delenv("RADIATOR_TOKEN", raising=False)
    run.return_value = mock.Mock(returncode=0)
    cli(["--env", "prod", "--acceptance", "a", "--pathfinder", "p"])
    for call in run.call_args_list:
        cmd = call.args[0]
        assert cmd[cmd.index("--env") + 1] == "prod"


@pytest.mark.parametrize(
    "argv, message",
    [
        ([], "Nothing to run"),
        (["--performance", "perf"], "needs a --performance_target"),
        (["--acceptance", "a", "--", "--tests_url", "x"], "belongs before the `--`"),
        (["--acceptance", "a", "--", "--cycle_id", "x"], "sets --cycle_id itself"),
        (["--acceptance", "a", "--", "--env", "prod"], "Pass --env to the cycle"),
    ],
)
def test_bad_arguments(argv, message, capsys, monkeypatch):
    monkeypatch.delenv("PERFORMANCE_TARGETS", raising=False)
    with pytest.raises(SystemExit):
        cli(["--env", "ci", *argv])
    assert message in capsys.readouterr().err


REPORT = {
    "env": "ci",
    "rows": [
        {
            "label": "Acceptance",
            "suite": "sprint_4_tests",
            "host": None,
            "value_text": "80.6%",
            "unit": "pass rate",
            "change_text": "▼2.8 pts",
            "detail_text": "3 regressions · 1 fixed",
            "verdict_label": "Regressed",
            "flags": [
                {"text": "arax: no results on 5 assets that had results last run"}
            ],
            "changes": [
                {
                    "agent": "arax",
                    "regressions": [
                        {"name": f"Asset {i}", "asset_id": f"A{i}"} for i in range(6)
                    ],
                    "fixed": [{"name": None, "asset_id": "A9"}],
                }
            ],
        },
        {
            "label": "Performance · ars",
            "suite": "performance_tests",
            "host": "https://ars.ci.transltr.io",
            "value_text": "14.5",
            "unit": "max concurrency",
            "change_text": "▲0.9 (+7%)",
            "detail_text": "checkpoints passed",
            "verdict_label": "Steady",
            "flags": [],
            "changes": [],
        },
    ],
}


def test_the_cycle_message():
    summary, thread = cycle_message(REPORT, _ran(0, 2, 0), "https://r/cycles/x")
    # at a glance: what ran, the verdicts, the flags, and the link
    assert summary == (
        "*CI run cycle finished* · 2 of 3 steps ran · 1 h 30 min\n"
        "> 1 Regressed · 1 Steady · 1 flag\n"
        "<https://r/cycles/x|Open the run cycle in the Information Radiator>"
    )
    results, changes, flags = thread
    assert results.startswith("*Results*\n• step 1: couldn't run (exit 2)")
    assert (
        "• *Acceptance (sprint_4_tests)*: 80.6% pass rate, ▼2.8 pts · "
        "3 regressions · 1 fixed · *Regressed*" in results
    )
    assert "• *Performance · ars*: 14.5 max concurrency" in results
    assert changes.startswith("*Regressions and fixes by agent*")
    assert (
        "arax: 6 regressions (Asset 0, Asset 1, Asset 2, Asset 3 and 2 more); "
        "1 fixed (A9)" in changes
    )
    assert flags == "*Flags*\n• arax: no results on 5 assets that had results last run"


def test_a_quiet_cycle_has_a_short_thread():
    report = {"env": "ci", "rows": [REPORT["rows"][1]]}
    summary, thread = cycle_message(report, _ran(0), None)
    assert summary == (
        "*CI run cycle finished* · 1 of 1 steps ran · 0 h 30 min\n> 1 Steady"
    )
    assert len(thread) == 1 and thread[0].startswith("*Results*")


def test_the_digest_message():
    digest = {
        "since": "2026-10-05T12:00:00+00:00",
        "until": "2026-10-12T12:00:00+00:00",
        "envs": [{"env": "ci", "rows": REPORT["rows"]}],
    }
    summary, thread = digest_message(digest, "https://r/weekly")
    assert summary == (
        "*Weekly test status* · 2026-10-05 to 2026-10-12\n"
        "> *CI*: 1 Regressed · 1 Steady · 1 flag\n"
        "<https://r/weekly|Open the weekly page in the Information Radiator>"
    )
    rows, flags = thread
    assert rows.startswith(
        "*CI*\n    Acceptance (sprint_4_tests): 80.6%, ▼2.8 pts · Regressed"
    )
    # a performance row's label names its service; no suite after it
    assert "    Performance · ars: 14.5," in rows
    assert "• CI: arax: no results on 5 assets" in flags
    empty, thread = digest_message({**digest, "envs": []}, None)
    assert "No finished runs this week." in empty
    assert thread == []


class _Slack:
    def __init__(self):
        self.messages, self.files, self.threads = [], [], []

    def post_thread(self, summary, image=None, details=(), files=()):
        self.messages.append(summary)
        self.threads.append(list(details))
        self.files += [f.filename for f in ([image] if image else []) + list(files)]


def test_posting_the_board():
    radiator = mock.Mock()
    radiator.cycle.return_value = REPORT
    radiator.cycle_board_png.return_value = b"\x89PNG"
    radiator.cycle_url.return_value = "https://r/cycles/x"
    slack = _Slack()
    assert cycle.post_cycle_report("x", _ran(0), radiator, slack, mock.Mock())
    [message] = slack.messages
    assert message.startswith("*CI run cycle finished*")
    assert slack.files == ["ci_run_cycle.png"]  # with the top message
    assert slack.threads[0][0].startswith("*Results*")

    radiator.cycle.return_value = None
    assert not cycle.post_cycle_report("x", _ran(0), radiator, _Slack(), mock.Mock())


def test_posting_the_digest():
    radiator = mock.Mock()
    radiator.digest.return_value = {
        "since": "2026-10-05",
        "until": "2026-10-12",
        "envs": [],
    }
    radiator.digest_png.return_value = b"\x89PNG"
    slack = _Slack()
    assert cycle.post_digest(radiator, slack, mock.Mock())
    assert slack.files == ["weekly_status.png"]
    radiator.digest.return_value = None
    assert not cycle.post_digest(radiator, _Slack(), mock.Mock())


def test_the_board_and_digest_go_to_slack_channel(monkeypatch):
    from test_harness.slacker import RETIRED_SETTINGS

    for name in RETIRED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    monkeypatch.setenv("SLACK_TOKEN", "xoxb")
    monkeypatch.setenv("SLACK_CHANNEL", "C0123")
    logger = mock.Mock()
    assert cycle._slacker("out", logger).channel == "C0123"
    logger.warning.assert_not_called()
    # the settings from before the switch are ignored, and say so
    monkeypatch.setenv("ZE_BASE_URL", "https://zebrunner.example.org")
    monkeypatch.setenv("RADIATOR_SLACK_CHANNEL", "C0OTHER")
    assert cycle._slacker("out", logger).channel == "C0123"
    [warning] = logger.warning.call_args.args
    assert "RADIATOR_SLACK_CHANNEL, ZE_BASE_URL are no longer used" in warning


def test_the_board_comes_from_the_radiator_the_steps_reported_to(monkeypatch):
    monkeypatch.delenv("RADIATOR_URL", raising=False)
    monkeypatch.delenv("RADIATOR_TOKEN", raising=False)
    with mock.patch("test_harness.cycle.subprocess.run") as run, mock.patch(
        "test_harness.cycle._slacker", return_value=mock.Mock()
    ), mock.patch("test_harness.cycle.post_cycle_report") as post:
        run.return_value = mock.Mock(returncode=0)
        cli(
            [
                "--env",
                "ci",
                "--acceptance",
                "a",
                "--",
                "--radiator_url=http://r",
                "--radiator_token",
                "t",
            ]
        )
    radiator = post.call_args.args[2]
    assert (radiator.base_url, radiator.token) == ("http://r", "t")


def test_a_slack_failure_after_the_cycle_is_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("RADIATOR_URL", "http://radiator")
    monkeypatch.setenv("RADIATOR_TOKEN", "tok")
    with mock.patch("test_harness.cycle.subprocess.run") as run, mock.patch(
        "test_harness.cycle._slacker", return_value=mock.Mock()
    ), mock.patch(
        "test_harness.cycle.post_cycle_report", side_effect=RuntimeError("slack down")
    ):
        run.return_value = mock.Mock(returncode=0)
        assert cli(["--env", "ci", "--acceptance", "a"]) == 0
    assert "Couldn't post the cycle board: RuntimeError: slack down" in caplog.text


def test_a_cycle_stopped_at_its_deadline_says_which_step_was_cut_off():
    steps = [
        Step("acceptance (mvp_tests)", [], code=0, minutes=50),
        Step("pathfinder (pathfinder_tests)", []),
        Step("performance (ars)", []),
    ]
    text = cycle.cycle_stopped_message("ci", steps, 240)
    assert "The run cycle in `ci` was stopped after 4 h 00 min" in text
    assert "*Done:* acceptance (mvp_tests): finished, 50 min" in text
    assert "*Cut off:* pathfinder (pathfinder_tests)" in text
    assert "*Never started:* performance (ars)" in text
    assert "no board" in text


def test_a_cycle_stopped_after_its_steps_was_posting_the_board():
    steps = [Step("acceptance (mvp_tests)", [], code=0, minutes=50)]
    text = cycle.cycle_stopped_message("ci", steps, 240)
    assert "Cut off" not in text
    assert "posting the cycle board" in text


def _steps():
    return [
        Step("acceptance (mvp_tests)", ["test-harness", "--env", "ci", "load", "a"]),
        Step("pathfinder (p)", ["test-harness", "--env", "ci", "load", "p"]),
        Step("performance (ars)", ["test-harness", "--env", "ci", "load", "x"]),
    ]


def test_each_step_gets_whats_left_of_the_cycles_time_limit(monkeypatch):
    calls = []
    clock = [1000.0]

    def run(cmd):
        calls.append(cmd)
        clock[0] += 60 * 60  # each step takes an hour
        return mock.Mock(returncode=0)

    monkeypatch.setattr(cycle.subprocess, "run", run)
    monkeypatch.setattr(cycle.time, "time", lambda: clock[0])
    monkeypatch.setattr("test_harness.sweep.time.time", lambda: clock[0])
    cycle.run_steps(_steps(), time_limit=240)
    limits = [float(cmd[cmd.index("--time_limit") + 1]) for cmd in calls]
    assert limits == [240, 180, 120]


def test_a_step_that_runs_out_of_time_ends_the_cycle(monkeypatch):
    codes = iter([0, cycle.EXIT_TIME_LIMIT])
    monkeypatch.setattr(
        cycle.subprocess, "run", lambda cmd: mock.Mock(returncode=next(codes))
    )
    steps = _steps()
    cycle.run_steps(steps, time_limit=240)
    assert [s.status for s in steps] == [
        "ok",
        "ran out of time",
        "not run: out of time",
    ]

    summary, thread = cycle_message({"env": "ci", "rows": []}, steps, None)
    assert "*CI run cycle ran out of time*" in summary
    assert "1 of 3 steps ran" in summary
    assert "pathfinder (p): ran out of time; its results are partial" in thread[0]
    assert "performance (ars): didn't run, the cycle was out of time" in thread[0]


@mock.patch("test_harness.cycle.subprocess.run")
def test_steps_that_never_ran_reach_slack_without_a_board(run, monkeypatch, tmp_path):
    monkeypatch.delenv("RADIATOR_URL", raising=False)
    monkeypatch.delenv("RADIATOR_TOKEN", raising=False)
    run.return_value = mock.Mock(returncode=cycle.EXIT_TIME_LIMIT)
    notes = []
    slacker = mock.Mock(post_notification=lambda messages: notes.extend(messages))
    monkeypatch.setattr(cycle, "_slacker", lambda *a: slacker)
    code = cli(
        [
            "--env",
            "ci",
            "--acceptance",
            "a",
            "--pathfinder",
            "p",
            "--output_dir",
            str(tmp_path),
        ]
    )
    assert code == 1
    (note,) = notes
    assert "The run cycle in `ci` ran out of its 240 min time limit" in note
    assert "Never started: pathfinder (p)" in note


def test_the_time_limit_belongs_to_the_cycle(capsys):
    with pytest.raises(SystemExit):
        cli(["--env", "ci", "--acceptance", "a", "--", "--time_limit=5"])
    assert "Pass --time_limit to the cycle" in capsys.readouterr().err
