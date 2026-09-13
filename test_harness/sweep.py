"""Run one test suite against several services, one after another.

A performance sweep is several independent harness runs: HelmsDeep loads exactly
one layer per run (the Translator stack cascades ARS -> ARAs -> KPs, so loading
two at once double-loads whatever sits underneath and corrupts both
measurements), and the harness resolves one target per invocation. This
entrypoint exists so a single Kubernetes Job can do the whole sweep rather than
four Jobs racing each other: it shells out to ``test-harness`` once per target,
in the order given, and waits for each to finish before starting the next.

Every target runs even when an earlier one fails -- a broken ARS shouldn't cost
you the ARA numbers -- and the sweep exits non-zero if any of them failed, so
the Job still reports failure.

    test-harness-sweep --suite performance_tests --download \\
        ars=https://ars.ci.transltr.io \\
        aragorn=https://aragorn.ci.transltr.io \\
        arax=https://arax.ci.transltr.io \\
        bte=https://bte.ci.transltr.io \\
        -- --performance_profile mixed

Targets can also come from the PERFORMANCE_TARGETS environment variable as a
comma-separated list of the same ``name=url`` pairs, which is usually the easier
knob in a CronJob spec: the target list changes without rebuilding the image.
"""

import os
import subprocess
import sys
import time
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from typing import Dict, List, Sequence, Tuple

# Comma-separated `name=url` pairs, an alternative to passing them as arguments.
TARGETS_ENV = "PERFORMANCE_TARGETS"

# Each target gets its own output directory. HelmsDeep names its raw files after
# the run type and the test case id, neither of which mentions the host, so
# every ARA in a sweep would otherwise write the same
# `helmsdeep_aras_case_<id>_*` files and clobber the previous target's.
DEFAULT_OUTPUT_DIR = "test_results"

HARNESS = "test-harness"


def parse_targets(
    specs: Sequence[str],
    env: Dict[str, str] = os.environ,
) -> List[Tuple[str, str]]:
    """Parse ``name=url`` pairs, preserving the order they were given in.

    Order is the sweep's contract -- it is the order the services get loaded in
    -- so this keeps a list rather than a dict.
    """
    raw = list(specs)
    if not raw:
        raw = [part for part in env.get(TARGETS_ENV, "").split(",") if part.strip()]

    targets = []
    seen = set()
    for spec in raw:
        name, sep, url = spec.strip().partition("=")
        name, url = name.strip(), url.strip()
        if not sep or not name or not url:
            raise ValueError(
                f"Bad target {spec!r}; expected name=url, e.g. "
                "ars=https://ars.ci.transltr.io"
            )
        if name in seen:
            raise ValueError(f"Target {name!r} given more than once")
        seen.add(name)
        targets.append((name, url))
    return targets


def build_command(
    target: str,
    url: str,
    suite: str,
    output_dir: str,
    harness_args: Sequence[str],
    download: bool,
    tests_url: str = None,
) -> List[str]:
    """Build one ``test-harness`` invocation for a single target.

    The harness takes its global flags *before* the subcommand, so everything
    except the suite goes in front of ``load``/``download``.
    """
    cmd = [
        HARNESS,
        "--target_url",
        url,
        "--target",
        target,
        # Per-target directory; see DEFAULT_OUTPUT_DIR on why.
        "--output_dir",
        os.path.join(output_dir, target),
        *harness_args,
    ]
    if download or tests_url:
        cmd.append("download")
        cmd.append(suite)
        if tests_url:
            cmd += ["--tests_url", tests_url]
    else:
        cmd += ["load", suite]
    return cmd


def _banner(text: str) -> None:
    """Print a delimiter that's findable in a multi-hour Job log."""
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}", flush=True)


def run_sweep(
    targets: Sequence[Tuple[str, str]],
    suite: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    harness_args: Sequence[str] = (),
    download: bool = False,
    tests_url: str = None,
) -> int:
    """Run the suite against each target in turn. Returns a process exit code.

    A target that fails is recorded and the sweep continues to the next one, so
    one unreachable service can't cost you the rest of the sweep's results.
    """
    results = []
    for position, (target, url) in enumerate(targets, start=1):
        cmd = build_command(
            target, url, suite, output_dir, harness_args, download, tests_url
        )
        _banner(f"[{position}/{len(targets)}] {target} -> {url}")
        print(f"$ {' '.join(cmd)}", flush=True)
        started = time.time()
        try:
            code = subprocess.run(cmd).returncode
        except OSError as e:
            # eg the harness isn't on PATH; a sweep-wide problem, but report it
            # per target and keep going so the log says which ones never ran.
            print(f"Failed to launch the harness for {target}: {e}", flush=True)
            code = 127
        elapsed = time.time() - started
        results.append((target, code, elapsed))
        print(
            f"\n{target} finished with exit code {code} "
            f"after {elapsed / 60:.1f} min",
            flush=True,
        )

    _banner("SWEEP SUMMARY")
    for target, code, elapsed in results:
        status = "ok" if code == 0 else f"FAILED (exit {code})"
        print(f"  {target:<12} {status:<20} {elapsed / 60:>6.1f} min", flush=True)
    failed = [target for target, code, _ in results if code != 0]
    total = sum(elapsed for _, _, elapsed in results)
    print(
        f"\n{len(results) - len(failed)}/{len(results)} succeeded "
        f"in {total / 60:.1f} min total",
        flush=True,
    )
    if failed:
        print(f"Failed targets: {', '.join(failed)}", flush=True)
        return 1
    return 0


def split_passthrough(argv: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Split argv at the first ``--``: sweep arguments, then harness arguments.

    Done by hand rather than with an ``argparse.REMAINDER`` positional: with a
    preceding ``nargs="*"`` positional (the target list) argparse swallows the
    ``--`` and everything after it into the targets instead of splitting there.
    """
    argv = list(argv)
    if "--" not in argv:
        return argv, []
    separator = argv.index("--")
    return argv[:separator], argv[separator + 1 :]


def cli(argv: Sequence[str] = None) -> int:
    """Parse args and run the sweep."""
    if argv is None:
        argv = sys.argv[1:]
    sweep_argv, harness_args = split_passthrough(argv)

    parser = ArgumentParser(
        prog="test-harness-sweep",
        formatter_class=RawDescriptionHelpFormatter,
        description=(
            "Run one test suite against several services sequentially, so a "
            "single scheduled Job can sweep the ARS and each ARA in turn."
        ),
        epilog=(
            "Anything after a `--` separator is passed through to every "
            "harness invocation, e.g.:\n\n"
            "  test-harness-sweep --suite performance_tests --download \\\n"
            "      ars=https://ars.ci.transltr.io \\\n"
            "      aragorn=https://aragorn.ci.transltr.io \\\n"
            "      -- --performance_profile mixed --log_level INFO\n"
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="NAME=URL",
        help=(
            "The services to run against, in order, e.g. "
            "ars=https://ars.ci.transltr.io. NAME is the infores identifier "
            f"passed to --target. Defaults to the {TARGETS_ENV} environment "
            "variable, as a comma-separated list of the same pairs."
        ),
    )
    parser.add_argument(
        "--suite",
        required=True,
        help="The test suite to run against every target.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=(
            "Download the suite instead of reading it from a local file "
            "(the harness's `download` subcommand rather than `load`)."
        ),
    )
    parser.add_argument(
        "--tests_url",
        default=None,
        help="URL to download the suite from. Implies --download.",
    )
    parser.add_argument(
        "--output_dir",
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Base directory for results. Each target gets its own "
            "subdirectory, so their artifacts don't overwrite each other "
            f"(default: {DEFAULT_OUTPUT_DIR})."
        ),
    )
    args = parser.parse_args(sweep_argv)

    try:
        targets = parse_targets(args.targets)
    except ValueError as e:
        parser.error(str(e))
    if not targets:
        parser.error(
            "No targets given. Pass them as NAME=URL arguments or set "
            f"{TARGETS_ENV}."
        )

    return run_sweep(
        targets,
        args.suite,
        output_dir=args.output_dir,
        harness_args=harness_args,
        download=args.download,
        tests_url=args.tests_url,
    )


if __name__ == "__main__":
    sys.exit(cli())
