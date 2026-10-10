"""Run a cycle: acceptance, then pathfinder, then performance, in one environment.

A run cycle is a weekly look at one environment: the acceptance suite (about
50 minutes), the pathfinder suite, and a HelmsDeep performance run against the
ARS (about 70 minutes), one after another so they never load the services at
the same time. Each step is an ordinary ``test-harness`` run, with its own Slack
report as usual; the runs share a cycle id, and when the last one finishes the
cycle posts the Information Radiator's board for the whole cycle: each test
against its previous run in this environment, the regressions and fixes by
agent, and flags for anything odd.

    test-harness-cycle --download \\
        --acceptance sprint_4_tests --pathfinder pathfinder_tests \\
        --performance performance_tests \\
        --performance_target ars=https://ars.ci.transltr.io \\
        --performance_profile mixed \\
        -- --log_level INFO

Every step runs even when an earlier one fails. Like the sweep, the exit status
answers "did the runs happen?": non-zero only when a step couldn't be carried
out at all, never because a test failed.

The weekly digest (``test-harness-radiator digest``) is the same board for
every environment; its Slack message is built here too.
"""

import logging
import os
import subprocess
import sys
import time
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass
from typing import List, Optional, Sequence
from uuid import uuid4

from test_harness.radiator_client import RadiatorClient
from test_harness.slacker import LocalSlacker, Slacker
from test_harness.sweep import (
    DEFAULT_OUTPUT_DIR,
    HARNESS,
    SUBCOMMAND_FLAGS,
    TARGETS_ENV,
    _banner,
    build_command,
    parse_targets,
    split_passthrough,
)

FLAG = "⚑"


@dataclass
class Step:
    name: str  # what the step is, for the log and the report
    cmd: List[str]
    code: Optional[int] = None
    minutes: float = 0.0


def _suite_command(
    suite: str,
    output_dir: str,
    harness_args: Sequence[str],
    download: bool,
    tests_url: Optional[str],
    tests_dir: Optional[str],
) -> List[str]:
    cmd = [HARNESS, "--output_dir", output_dir, *harness_args]
    if download or tests_url:
        cmd += ["download", suite]
        if tests_url:
            cmd += ["--tests_url", tests_url]
    else:
        cmd += ["load", suite]
        if tests_dir:
            cmd += ["--tests_dir", tests_dir]
    return cmd


def plan(
    cycle_id: str,
    acceptance: Optional[str] = None,
    pathfinder: Optional[str] = None,
    performance: Optional[str] = None,
    performance_targets: Sequence = (),
    performance_profile: Optional[str] = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    harness_args: Sequence[str] = (),
    download: bool = False,
    tests_url: Optional[str] = None,
    tests_dir: Optional[str] = None,
) -> List[Step]:
    """The cycle's harness invocations, in order."""
    shared = ["--cycle_id", cycle_id, *harness_args]
    steps = []
    for kind, suite in (("acceptance", acceptance), ("pathfinder", pathfinder)):
        if suite:
            steps.append(
                Step(
                    f"{kind} ({suite})",
                    _suite_command(
                        suite,
                        os.path.join(output_dir, kind),
                        shared,
                        download,
                        tests_url,
                        tests_dir,
                    ),
                )
            )
    if performance:
        profile = (
            ["--performance_profile", performance_profile]
            if performance_profile
            else []
        )
        for target, url in performance_targets:
            steps.append(
                Step(
                    f"performance ({target})",
                    build_command(
                        target,
                        url,
                        performance,
                        os.path.join(output_dir, "performance"),
                        [*shared, *profile],
                        download,
                        tests_url,
                        tests_dir,
                    ),
                )
            )
    return steps


def run_steps(steps: List[Step]) -> None:
    for position, step in enumerate(steps, start=1):
        _banner(f"[{position}/{len(steps)}] {step.name}")
        print(f"$ {' '.join(step.cmd)}", flush=True)
        started = time.time()
        try:
            step.code = subprocess.run(step.cmd).returncode
        except OSError as e:
            print(f"Failed to launch the harness for {step.name}: {e}", flush=True)
            step.code = 127
        step.minutes = (time.time() - started) / 60
        print(
            f"\n{step.name} finished with exit code {step.code} "
            f"after {step.minutes:.1f} min",
            flush=True,
        )


# --- the Slack messages ------------------------------------------------------------


def _row_line(row: dict) -> str:
    what = row["label"]
    if row.get("host") is None:
        what += f" ({row['suite']})"
    return (
        f"• *{what}*: {row['value_text']} {row['unit']}, {row['change_text']} · "
        f"{row['detail_text']} · *{row['verdict_label']}*"
    )


def _changes_lines(row: dict, limit: int = 4) -> List[str]:
    lines = []
    for change in row.get("changes", []):
        parts = []
        if change["regressions"]:
            parts.append(_count_names(change["regressions"], "regression", limit))
        if change["fixed"]:
            parts.append(_count_names(change["fixed"], "fixed", limit))
        lines.append(f"    {change['agent']}: " + "; ".join(parts))
    return lines


def _count_names(assets: list, word: str, limit: int) -> str:
    names = ", ".join(a["name"] or a["asset_id"] for a in assets[:limit])
    more = len(assets) - limit
    if more > 0:
        names += f" and {more} more"
    n = len(assets)
    label = word if word == "fixed" or n == 1 else f"{word}s"
    return f"{n} {label} ({names})"


def cycle_message(report: dict, steps: List[Step], url: Optional[str]) -> str:
    """The Slack message for a finished cycle, from the radiator's report."""
    env = (report.get("env") or "mixed environments").upper()
    total = sum(step.minutes for step in steps)
    ran = [step for step in steps if step.code == 0]
    lines = [
        f"*{env} run cycle finished* · {len(ran)} of {len(steps)} steps ran · "
        f"{int(total // 60)} h {int(total % 60):02d} min"
    ]
    for step in steps:
        if step.code != 0:
            lines.append(f"• {step.name}: couldn't run (exit {step.code})")
    rows = report.get("rows", [])
    lines += [_row_line(row) for row in rows]
    changed = [row for row in rows if row.get("changes")]
    if changed:
        lines.append("")
        lines.append("*Regressions and fixes by agent*")
        for row in changed:
            lines.append(f"  {row['label']}:")
            lines += _changes_lines(row)
    flags = [flag for row in rows for flag in row.get("flags", [])]
    if flags:
        lines.append("")
        lines.append(f"*{FLAG} Flags*")
        lines += [f"• {flag['text']}" for flag in flags]
    if url:
        lines.append(f"\n<{url}|Open the run cycle in the Information Radiator>")
    return "\n".join(lines)


def digest_message(digest: dict, url: Optional[str]) -> str:
    """The Slack message for the weekly digest."""
    since, until = digest["since"][:10], digest["until"][:10]
    lines = [f"*Weekly test status* · {since} to {until}"]
    if not digest.get("envs"):
        lines.append("No finished runs this week.")
    for env in digest.get("envs", []):
        name = (env.get("env") or "no environment").upper()
        flags = sum(len(row["flags"]) for row in env["rows"])
        lines.append(f"*{name}*" + (f" · {FLAG} {flags}" if flags else ""))
        for row in env["rows"]:
            lines.append(
                f"    {row['label']}: {row['value_text']}, {row['change_text']} · "
                f"{row['verdict_label']}"
            )
    flagged = [
        env for env in digest.get("envs", []) if any(r["flags"] for r in env["rows"])
    ]
    if flagged:
        lines.append("")
        lines.append(f"*{FLAG} Flags*")
        for env in flagged:
            name = (env.get("env") or "no environment").upper()
            for row in env["rows"]:
                for flag in row["flags"]:
                    lines.append(f"• {name}: {flag['text']}")
    if url:
        lines.append(f"\n<{url}|Open the weekly page in the Information Radiator>")
    return "\n".join(lines)


def _slacker(output_dir: str, logger):
    if Slacker.is_configured():
        return Slacker()
    logger.info(f"Slack isn't configured; saving the reports to '{output_dir}'.")
    return LocalSlacker(output_dir=output_dir, logger=logger)


def post_cycle_report(
    cycle_id: str,
    steps: List[Step],
    radiator: RadiatorClient,
    slacker,
    logger: logging.Logger,
) -> bool:
    """Post the cycle's board to Slack. Returns False if the radiator didn't
    have it (then the per-run reports are all there is)."""
    report = radiator.cycle(cycle_id)
    if report is None or not report.get("rows"):
        logger.warning(
            "The Information Radiator has no board for this cycle; "
            "only the per-run reports were posted."
        )
        return False
    message = cycle_message(report, steps, radiator.cycle_url(cycle_id))
    slacker.post_notification(messages=[message])
    board = radiator.cycle_board_png(cycle_id)
    if board:
        env = report.get("env") or "cycle"
        slacker.upload_binary_file(
            f"{env}_run_cycle.png",
            board,
            initial_comment=f"{env.upper()} run cycle: each test against its previous run",
            title="Run cycle board",
        )
    return True


def post_digest(
    radiator: RadiatorClient, slacker, logger: logging.Logger, days: int = 7
) -> bool:
    digest = radiator.digest(days)
    if digest is None:
        logger.error("Couldn't get the weekly digest from the Information Radiator.")
        return False
    slacker.post_notification(messages=[digest_message(digest, radiator.weekly_url)])
    image = radiator.digest_png(days)
    if image:
        slacker.upload_binary_file(
            "weekly_status.png",
            image,
            initial_comment="Each environment's latest runs this week",
            title="Weekly test status",
        )
    return True


# --- the command ----------------------------------------------------------------------


def cli(argv: Sequence[str] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    cycle_argv, harness_args = split_passthrough(argv)
    parser = ArgumentParser(
        prog="test-harness-cycle",
        formatter_class=RawDescriptionHelpFormatter,
        description=(
            "Run acceptance, then pathfinder, then performance in one "
            "environment, one after another, and post the Information "
            "Radiator's board for the whole cycle."
        ),
        epilog=(
            "Anything after a `--` separator is passed to every harness "
            "invocation, e.g.:\n\n"
            "  test-harness-cycle --download --acceptance sprint_4_tests \\\n"
            "      --pathfinder pathfinder_tests --performance performance_tests \\\n"
            "      --performance_target ars=https://ars.ci.transltr.io \\\n"
            "      --performance_profile mixed -- --log_level INFO\n"
        ),
    )
    parser.add_argument("--acceptance", help="The acceptance suite to run first.")
    parser.add_argument("--pathfinder", help="The pathfinder suite to run second.")
    parser.add_argument("--performance", help="The performance suite to run last.")
    parser.add_argument(
        "--performance_target",
        action="append",
        default=[],
        metavar="NAME=URL",
        help=(
            "The service to load in the performance step, e.g. "
            "ars=https://ars.ci.transltr.io. Repeat to run several, in order. "
            f"Defaults to the {TARGETS_ENV} environment variable."
        ),
    )
    parser.add_argument(
        "--performance_profile",
        help="HelmsDeep's query profile for the performance step, e.g. mixed.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the suites instead of reading them from local files.",
    )
    parser.add_argument("--tests_url", help="URL to download the suites from.")
    parser.add_argument("--tests_dir", help="Directory to read local suites from.")
    parser.add_argument(
        "--output_dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Base directory for results; each step gets its own subdirectory.",
    )
    args = parser.parse_args(cycle_argv)

    misplaced = [
        arg
        for arg in harness_args
        for flag in SUBCOMMAND_FLAGS
        if arg == flag or arg.startswith(f"{flag}=")
    ]
    if misplaced:
        parser.error(
            f"{', '.join(misplaced)} belongs before the `--`: it's an argument "
            "of the cycle, which passes it to each step's subcommand."
        )
    if "--cycle_id" in harness_args:
        parser.error("The cycle sets --cycle_id itself.")
    if not (args.acceptance or args.pathfinder or args.performance):
        parser.error(
            "Nothing to run: pass --acceptance, --pathfinder or --performance."
        )
    targets = []
    if args.performance:
        try:
            targets = parse_targets(args.performance_target)
        except ValueError as e:
            parser.error(str(e))
        if not targets:
            parser.error(
                "--performance needs a --performance_target (or "
                f"{TARGETS_ENV}), e.g. ars=https://ars.ci.transltr.io"
            )

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("test-harness-cycle")
    cycle_id = str(uuid4())
    steps = plan(
        cycle_id,
        acceptance=args.acceptance,
        pathfinder=args.pathfinder,
        performance=args.performance,
        performance_targets=targets,
        performance_profile=args.performance_profile,
        output_dir=args.output_dir,
        harness_args=harness_args,
        download=args.download,
        tests_url=args.tests_url,
        tests_dir=args.tests_dir,
    )
    print(f"Run cycle {cycle_id}: {len(steps)} steps", flush=True)
    run_steps(steps)

    _banner("CYCLE SUMMARY")
    for step in steps:
        status = "ok" if step.code == 0 else f"FAILED (exit {step.code})"
        print(f"  {step.name:<40} {status:<20} {step.minutes:>6.1f} min", flush=True)

    radiator = RadiatorClient(logger=logger)
    if radiator.enabled:
        post_cycle_report(
            cycle_id, steps, radiator, _slacker(args.output_dir, logger), logger
        )
    else:
        logger.info("No Information Radiator configured, so no cycle board.")
    return 1 if any(step.code != 0 for step in steps) else 0


def digest_cli(radiator: RadiatorClient, days: int, output_dir: str, logger) -> int:
    """``test-harness-radiator digest``: post the weekly digest to Slack."""
    return 0 if post_digest(radiator, _slacker(output_dir, logger), logger, days) else 1


if __name__ == "__main__":
    sys.exit(cli())
