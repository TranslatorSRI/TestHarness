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


def _ran(*codes):
    return [Step(f"step {i}", [], code, 30.0) for i, code in enumerate(codes)]


@mock.patch("test_harness.cycle.post_cycle_report")
@mock.patch("test_harness.cycle.subprocess.run")
def test_every_step_runs_even_when_one_fails(run, post, monkeypatch):
    monkeypatch.setenv("RADIATOR_URL", "http://radiator")
    monkeypatch.setenv("RADIATOR_TOKEN", "tok")
    run.side_effect = [mock.Mock(returncode=2), mock.Mock(returncode=0)]
    code = cli(["--acceptance", "a", "--pathfinder", "p", "--download"])
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
        assert cli(["--acceptance", "a"]) == 0
    post.assert_not_called()


@pytest.mark.parametrize(
    "argv, message",
    [
        ([], "Nothing to run"),
        (["--performance", "perf"], "needs a --performance_target"),
        (["--acceptance", "a", "--", "--tests_url", "x"], "belongs before the `--`"),
        (["--acceptance", "a", "--", "--cycle_id", "x"], "sets --cycle_id itself"),
    ],
)
def test_bad_arguments(argv, message, capsys, monkeypatch):
    monkeypatch.delenv("PERFORMANCE_TARGETS", raising=False)
    with pytest.raises(SystemExit):
        cli(argv)
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
    message = cycle_message(REPORT, _ran(0, 2, 0), "https://r/cycles/x")
    assert message.startswith("*CI run cycle finished* · 2 of 3 steps ran · 1 h 30 min")
    assert "• step 1: couldn't run (exit 2)" in message
    assert (
        "• *Acceptance (sprint_4_tests)*: 80.6% pass rate, ▼2.8 pts · "
        "3 regressions · 1 fixed · *Regressed*" in message
    )
    assert "• *Performance · ars*: 14.5 max concurrency" in message
    assert (
        "arax: 6 regressions (Asset 0, Asset 1, Asset 2, Asset 3 and 2 more); "
        "1 fixed (A9)" in message
    )
    assert "*⚑ Flags*\n• arax: no results on 5 assets" in message
    assert message.endswith(
        "<https://r/cycles/x|Open the run cycle in the Information Radiator>"
    )


def test_the_digest_message():
    digest = {
        "since": "2026-10-05T12:00:00+00:00",
        "until": "2026-10-12T12:00:00+00:00",
        "envs": [{"env": "ci", "rows": REPORT["rows"]}],
    }
    message = digest_message(digest, "https://r/weekly")
    assert message.startswith("*Weekly test status* · 2026-10-05 to 2026-10-12")
    assert "*CI* · ⚑ 1\n    Acceptance: 80.6%, ▼2.8 pts · Regressed" in message
    assert "• CI: arax: no results on 5 assets" in message
    empty = digest_message({**digest, "envs": []}, None)
    assert "No finished runs this week." in empty


class _Slack:
    def __init__(self):
        self.messages, self.files = [], []

    def post_notification(self, messages=[]):
        self.messages.extend(messages)

    def upload_binary_file(self, filename, content, initial_comment=None, title=None):
        self.files.append(filename)


def test_posting_the_board():
    radiator = mock.Mock()
    radiator.cycle.return_value = REPORT
    radiator.cycle_board_png.return_value = b"\x89PNG"
    radiator.cycle_url.return_value = "https://r/cycles/x"
    slack = _Slack()
    assert cycle.post_cycle_report("x", _ran(0), radiator, slack, mock.Mock())
    assert "*CI run cycle finished*" in slack.messages[0]
    assert slack.files == ["ci_run_cycle.png"]

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
