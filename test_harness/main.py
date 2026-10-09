"""Translator SRI Automated Test Harness."""

from gevent import monkey

monkey.patch_all()

import json
import os
import sys
import time
from argparse import ArgumentParser, ArgumentTypeError
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse
from uuid import uuid4

from setproctitle import setproctitle

from radiator_schema import RunCreate
from test_harness.download import download_tests, load_tests
from test_harness.logger import get_logger, setup_logger
from test_harness.performance_test_runner import PROFILES
from test_harness.radiator_client import RadiatorClient
from test_harness.reporter import LocalReporter, Reporter
from test_harness.result_collector import ResultCollector
from test_harness.run import run_tests
from test_harness.slacker import LocalSlacker, Slacker
from test_harness.trapi import (
    DEFAULT_TRAPI_VERSION,
    parse_query_parameters,
    trapi_minor_version,
    validate_query_parameters,
)
from test_harness.utils import QUERY_TYPE_PREDICATES, filter_tests_by_query_type

setproctitle("TestHarness")
setup_logger()

# The harness's exit status answers "did the run happen?", never "did the tests
# pass?". A failed acceptance test or a missed performance checkpoint is a
# *result*: it goes to Slack and the Information Radiator, and a scheduled job
# must not go red for it -- a service being slow is news for the channel, not a
# broken cron. What does earn a non-zero exit is the harness being unable to
# carry out the run at all (bad arguments, a suite that isn't there, nothing
# left to run), because that job produced no results and would otherwise sit
# green and silent indefinitely.
EXIT_OK = 0
EXIT_COULD_NOT_RUN = 1


def url_type(arg):
    url = urlparse(arg)
    if all((url.scheme, url.netloc)):
        return arg
    raise TypeError("Invalid URL")


def harness_version():
    try:
        return version("sri-test-harness")
    except PackageNotFoundError:
        return None


def save_radiator_run(radiator, output_dir, prefix, logger, force=False):
    """Save the run for `test-harness-radiator push` if it wasn't uploaded."""
    if force or not radiator.enabled or radiator.upload_failed:
        path = radiator.save(output_dir, prefix=prefix)
        logger.info(
            f"Saved the run for the Information Radiator to {path}; upload it "
            "with `test-harness-radiator push`."
        )


def radiator_headline(radiator, collector) -> str:
    """Lines for Slack: this run against the previous one in its series (the
    acceptance pass rate, each service's top concurrency), linking to what
    changed. Empty when the radiator doesn't have the run."""
    if not (collector.has_acceptance_results or collector.has_performance_results):
        return ""
    summary = radiator.summary()
    if not summary:
        return ""
    lines = []
    if collector.has_acceptance_results and summary.get("pass_rate") is not None:
        line = f"Pass rate {summary['pass_rate']:.0%}"
        if summary.get("previous_pass_rate") is not None:
            delta = (summary["pass_rate"] - summary["previous_pass_rate"]) * 100
            line += f" ({delta:+.1f} pts vs the previous run)"
            line += (
                f" · {summary['regressions']} regressions · {summary['fixed']} fixed"
                f" · <{radiator.diff_url}|What changed>"
            )
        lines.append(line)
    if collector.has_performance_results:
        lines.extend(_performance_line(p) for p in summary.get("performance") or [])
    return "".join(f"\n> {line}" for line in lines) + ("\n" if lines else "")


def _performance_line(perf: dict) -> str:
    """One service's top concurrency against its previous run."""
    name = perf.get("helmsdeep_target") or perf["host"]
    if perf.get("profile"):
        name += f" ({perf['profile']})"
    if perf.get("error"):
        return f"{name}: run failed: {perf['error']}"
    current = perf.get("max_sustainable_concurrency")
    if current is None:
        line = f"{name}: no stage met the SLO"
    else:
        line = f"{name}: max sustainable concurrency {current:.1f}"
        previous = perf.get("previous_max_sustainable_concurrency")
        if previous is not None:
            line += f" ({current - previous:+.1f} vs the previous run)"
        if perf.get("knee_unsupported"):
            line += " (unsupported, see stage warnings)"
    if perf.get("checkpoints_passed") is not None:
        line += (
            " · checkpoints passed"
            if perf["checkpoints_passed"]
            else " · checkpoints missed"
        )
    return line


def post_history_charts(radiator, slacker, collector, suite, prefix, logger):
    """Post the run's history charts, from the radiator, under the report:
    the acceptance pass rate and each service's top concurrency. Best effort:
    the report stands without them."""
    charts = []
    if collector.has_acceptance_results:
        charts.append(
            (
                radiator.history_png,
                "pass_rate_history.png",
                "Pass-rate history",
                f"Pass-rate history for {suite} up to this run",
            )
        )
    if collector.has_performance_results:
        charts.append(
            (
                radiator.performance_png,
                "concurrency_history.png",
                "Max sustainable concurrency history",
                "Max sustainable concurrency history up to this run",
            )
        )
    for fetch, filename, title, comment in charts:
        png = fetch()
        if not png:
            continue
        try:
            slacker.upload_binary_file(
                f"{prefix}{filename}",
                png,
                initial_comment=(
                    f"{comment} · <{radiator.run_url}|Open in the Information Radiator>"
                ),
                title=title,
            )
        except Exception as e:
            logger.warning(f"Failed to post the {title.lower()} chart: {e}")


def main(args):
    """Main Test Harness entrypoint."""
    qid = str(uuid4())[:8]
    logger = get_logger(qid, args["log_level"])
    if bool(args.get("target_url")) != bool(args.get("target")):
        logger.error("--target_url and --target must be provided together.")
        return EXIT_COULD_NOT_RUN
    # Check the query parameters against the TRAPI version now, so a typo
    # stops the run before any query goes out rather than failing every one.
    try:
        args["query_parameters"] = validate_query_parameters(
            args.get("query_parameters"),
            args.get("trapi_version") or DEFAULT_TRAPI_VERSION,
        )
    except ValueError as e:
        logger.error(str(e))
        return EXIT_COULD_NOT_RUN
    if args["query_parameters"]:
        logger.info(
            f"Sending these parameters with every query: "
            f"{json.dumps(args['query_parameters'])}"
        )
    tests = []
    if "tests_url" in args:
        tests = download_tests(args["suite"], args["tests_url"], logger)
    elif "tests_dir" in args:
        tests = load_tests(args["suite"], args["tests_dir"], logger)
    elif "tests" in args:
        tests = args["tests"]
    else:
        logger.error("Please run this command with `-h` to see the available options.")
        return EXIT_COULD_NOT_RUN

    if len(tests) < 1:
        logger.error("No tests to run. Exiting.")
        return EXIT_COULD_NOT_RUN

    # optionally run only one type of query out of the suite, eg to evaluate a
    # change that only affects drug-treats-disease queries
    query_type = args.get("query_type")
    if query_type is not None:
        tests = filter_tests_by_query_type(tests, query_type, logger)
        if len(tests) < 1:
            logger.error(f"No {query_type} tests to run. Exiting.")
            return EXIT_COULD_NOT_RUN

    output_dir = args.get("output_dir") or "test_results"

    # prefix saved/uploaded result filenames with the override target and the
    # query type so runs against different local services, or of different
    # slices of a suite, don't produce indistinguishable files
    target_prefix = ""
    if args.get("target"):
        target_prefix = f"{args['target'].split('infores:')[-1]}_"
    if query_type is not None:
        target_prefix += f"{query_type}_"

    # Run fully locally when asked to, or fall back to local stand-ins when the
    # respective service isn't configured, so developers can run the harness
    # without an Information Radiator or Slack workspace.
    local = args.get("local", False)

    use_local_reporter = local or not Reporter.is_configured(
        base_url=args.get("reporter_url"),
        refresh_token=args.get("reporter_access_token"),
    )
    if use_local_reporter:
        logger.info("Running without the Information Radiator (local reporter).")
        reporter = LocalReporter(logger=logger)
    else:
        # Create test run in the Information Radiator
        reporter = Reporter(
            base_url=args.get("reporter_url"),
            refresh_token=args.get("reporter_access_token"),
            logger=logger,
        )
    test_env = next(iter(tests.values())).test_env
    try:
        reporter.get_auth()
        reporter.create_test_run(test_env, args["suite"])
    except Exception as e:
        # Zebrunner being down mustn't stop the run: the tests still run and
        # still reach Slack and the new Information Radiator.
        logger.error(
            f"Couldn't open a run in the Zebrunner Information Radiator ({e}); "
            "continuing without it."
        )
        reporter = LocalReporter(logger=logger)
        reporter.create_test_run(test_env, args["suite"])

    use_local_slacker = local or not Slacker.is_configured()
    if use_local_slacker:
        logger.info(f"Running without Slack; results will be saved to '{output_dir}'.")
        slacker = LocalSlacker(output_dir=output_dir, logger=logger)
    else:
        slacker = Slacker()
    # The new Information Radiator runs alongside Zebrunner until it takes
    # over. It is additive: without RADIATOR_URL/RADIATOR_TOKEN, or with
    # --local, it only records, and the run is saved to --output_dir instead.
    radiator = RadiatorClient(
        base_url=args.get("radiator_url"),
        token=args.get("radiator_token"),
        enabled=not local,
        logger=logger,
    )
    radiator.start_run(
        RunCreate(
            run_id=uuid4(),
            suite=args["suite"],
            env=test_env,
            target=args.get("target"),
            target_url=args.get("target_url"),
            query_type=query_type,
            query_parameters=args.get("query_parameters"),
            harness_version=harness_version(),
            tests_source=args.get("tests_url") or args.get("tests_dir"),
            started_at=datetime.now().astimezone(),
        )
    )
    params_note = (
        f"\nQuery parameters: `{json.dumps(args['query_parameters'])}`"
        if args.get("query_parameters")
        else ""
    )
    # Only link to Zebrunner when the run went there: the local stand-in's
    # "URL" leads nowhere.
    zebrunner_link = (
        ""
        if reporter.is_local
        else f"\n<{reporter.base_path}/test-runs/{reporter.test_run_id}|View in the Information Radiator>"
    )
    radiator_link = (
        f"\n<{radiator.run_url}|View in the new Information Radiator>"
        if radiator.run_url
        else ""
    )

    collector = ResultCollector(
        test_env, logger, target=args.get("target"), radiator=radiator
    )
    queried_envs = set()
    for test in tests.values():
        queried_envs.add(test.test_env)
    slacker.post_notification(
        messages=[
            f"Running {args['suite']} ({sum([len(test.test_assets) for test in tests.values()])} tests, {len(tests.values())} queries)...{params_note}{zebrunner_link}{radiator_link}"
        ]
    )
    start_time = time.time()
    try:
        run_tests(tests, reporter, collector, logger, args)
    except BaseException:
        # Keep what was collected, but leave the run open: a run that died
        # halfway must not read as finished.
        radiator.flush()
        save_radiator_run(radiator, output_dir, target_prefix, logger, force=True)
        raise
    # Close out the radiator before Zebrunner and Slack, whose failures below
    # would otherwise cost it the run.
    radiator.finish_run(counts=collector.acceptance_report)
    save_radiator_run(radiator, output_dir, target_prefix, logger)

    slacker.post_notification(
        messages=[
            """Test Suite: {test_suite}\nDuration: {duration} | Environment(s): {envs}{zebrunner_link}{radiator_link}\n{result_summary}""".format(
                test_suite=args["suite"],
                duration=round(time.time() - start_time, 2),
                envs=(",").join(list(queried_envs)),
                zebrunner_link=zebrunner_link,
                radiator_link=radiator_link,
                result_summary=radiator_headline(radiator, collector)
                + collector.dump_result_summary(),
            )
        ]
    )
    post_history_charts(
        radiator, slacker, collector, args["suite"], target_prefix, logger
    )
    if collector.has_acceptance_results:
        slacker.upload_test_results_file(
            f"{target_prefix}{reporter.test_name}",
            "json",
            collector.acceptance_stats,
        )
        slacker.upload_test_results_file(
            f"{target_prefix}{reporter.test_name}",
            "csv",
            collector.acceptance_csv,
        )
    if collector.has_performance_results:
        # HelmsDeep's own summary.json is the authoritative result, so it is
        # uploaded verbatim alongside its HTML report rather than being
        # reformatted into a second, divergent JSON. Each carries the run's
        # checkpoint pass/fail as its comment.
        for filename, content, comment in collector.render_performance_artifacts():
            filename = f"{target_prefix}{filename}"
            try:
                slacker.upload_binary_file(filename, content, initial_comment=comment)
            except Exception as e:
                logger.warning(f"Failed to upload perf artifact {filename}: {e}")

    logger.info("Finishing up test run...")
    reporter.finish_test_run()

    if args["json_output"]:
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, f"{target_prefix}test_report.json")
        logger.info(f"Saving report as JSON to {report_path}...")
        with open(report_path, "w") as f:
            json.dump(collector.acceptance_report, f)

    logger.info("All tests have completed!")
    return EXIT_OK


def _query_parameters(value: str):
    """Parse --query_parameters / QUERY_PARAMETERS: JSON, or @file."""
    try:
        return parse_query_parameters(value)
    except (ValueError, OSError) as e:
        raise ArgumentTypeError(str(e))


def _trapi_version(value: str) -> str:
    """Accept a TRAPI version only if the harness can write queries in it."""
    try:
        trapi_minor_version(value)
    except ValueError as e:
        raise ArgumentTypeError(str(e))
    return value


def cli():
    """Parse args and run tests."""
    parser = ArgumentParser(description="Translator SRI Automated Test Harness")

    subparsers = parser.add_subparsers()

    download_parser = subparsers.add_parser(
        "download",
        help="Download tests to run from a URL",
    )

    download_parser.add_argument(
        "suite",
        type=str,
        help="The name/id of the suite(s) to run. Once tests have been downloaded, the test cases in this suite(s) will be run.",
    )

    download_parser.add_argument(
        "--tests_url",
        type=url_type,
        default="https://github.com/NCATSTranslator/Tests/archive/refs/heads/main.zip",
        help="URL to download in order to find the test files",
    )

    load_parser = subparsers.add_parser(
        "load",
        help="Run a test suite from a local JSON file instead of downloading one",
    )

    load_parser.add_argument(
        "suite",
        type=str,
        help=(
            "The name of the local suite to run: the JSON file's name without "
            "the extension, e.g. 'local_acceptance' for "
            "test_suites/local_acceptance.json."
        ),
    )

    load_parser.add_argument(
        "--tests_dir",
        type=str,
        default=None,
        help=(
            "Directory to read the suite from. Defaults to 'test_suites' in "
            "the working directory when it exists, and otherwise to the copy "
            "shipped with the repo, so a checkout's edited suites are found "
            "without this flag."
        ),
    )

    run_parser = subparsers.add_parser("run", help="Run a given set of tests")

    run_parser.add_argument(
        "tests",
        type=json.loads,
        help="The tests to be run, as a JSON string. This is the same structure `download_tests()` returns; to run tests from a file, use the `load` subcommand instead.",
    )

    parser.add_argument(
        "--reporter_url",
        type=url_type,
        help="URL of the Testing Dashboard",
    )

    parser.add_argument(
        "--reporter_access_token",
        type=str,
        help="Access token for authentication with the Testing Dashboard",
    )

    parser.add_argument(
        "--radiator_url",
        type=url_type,
        help="URL of the new Information Radiator (defaults to $RADIATOR_URL)",
    )

    parser.add_argument(
        "--radiator_token",
        type=str,
        help="Ingest token for the new Information Radiator (defaults to $RADIATOR_TOKEN)",
    )

    parser.add_argument(
        "--save_to_dashboard",
        action="store_true",
        help="Have the Test Harness send the test results to the Testing Dashboard",
    )

    parser.add_argument(
        "--target_url",
        type=url_type,
        help=(
            "Override the target service specified in the tests and send all "
            "queries to this URL instead, e.g. http://localhost:8080 for a "
            "locally running service. Must be used with --target."
        ),
    )

    parser.add_argument(
        "--target",
        type=str,
        help=(
            "The infores identifier of the service at --target_url, with or "
            "without the 'infores:' prefix, e.g. aragorn or infores:aragorn. "
            "Anything other than ars is queried directly as a single service "
            "(POST to <target_url>/query); ars uses the normal ARS "
            "submit/poll flow. Must be used with --target_url."
        ),
    )

    parser.add_argument(
        "--query_type",
        type=str.upper,
        choices=list(QUERY_TYPE_PREDICATES),
        help=(
            "Only run the tests of a single query type: MVP1 for the drug "
            "treats disease queries, MVP2 for the chemical affects gene "
            "queries. Every test in the suite is run if this isn't given. "
            "Mainly useful for local evaluation runs."
        ),
    )

    parser.add_argument(
        "--performance_profile",
        type=str.lower,
        choices=list(PROFILES),
        help=(
            "Which HelmsDeep query profile performance tests run: 'default' "
            "for the layer's own single-class corpus (lookup for KPs, "
            "inferred for ARAs/the ARS), 'mixed' for the 2:1 "
            "inferred/Pathfinder acceptance profile that carries pass/fail "
            "checkpoints, or 'pathfinder' for the two-pinned-endpoint path "
            "queries. Overrides a 'mixed'/'pathfinder' entry in a test case's "
            "test_runner_settings; without either, 'default' is used."
        ),
    )

    parser.add_argument(
        "--trapi_version",
        type=_trapi_version,
        default=DEFAULT_TRAPI_VERSION,
        help=(
            "TRAPI (SemVer) version to test: queries are written in it and "
            "only services registered for it are queried. 2.0.x or 1.6.x "
            f"(default {DEFAULT_TRAPI_VERSION})."
        ),
    )

    parser.add_argument(
        "--query_parameters",
        type=_query_parameters,
        default=os.getenv("QUERY_PARAMETERS"),
        help=(
            "Parameters sent with every query, as a JSON object or @file, eg "
            '\'{"timeout": 300, "bypass_cache": true}\'. TRAPI 2.0 queries carry '
            "them in their parameters object (timeout, log_level, bypass_cache, "
            "and any a service defines); TRAPI 1.6 queries take only log_level "
            "and bypass_cache. Defaults to $QUERY_PARAMETERS. Not used by "
            "performance tests, which send HelmsDeep's own queries."
        ),
    )

    parser.add_argument(
        "--json_output",
        action="store_true",
        help="Save the test results locally in json",
    )

    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Run entirely locally without an Information Radiator or Slack. "
            "Test results (CSV/JSON) and artifacts are saved to --output_dir."
        ),
    )

    parser.add_argument(
        "--output_dir",
        type=str,
        default="test_results",
        help=(
            "Directory to save local test results and artifacts when Slack is "
            "not configured or --local is used."
        ),
    )

    parser.add_argument(
        "--log_level",
        type=str,
        choices=["ERROR", "WARNING", "INFO", "DEBUG"],
        help="Level of the logs.",
        default="DEBUG",
    )

    args = parser.parse_args()
    sys.exit(main(vars(args)))


if __name__ == "__main__":
    cli()
