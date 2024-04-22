"""Translator SRI Automated Test Harness."""
from typing import Optional
from argparse import ArgumentParser
import asyncio
import json
from urllib.parse import urlparse
from uuid import uuid4

from test_harness.run import run_tests
from test_harness.download import download_tests
from test_harness.logger import get_logger, setup_logger
from test_harness.reporter import Reporter
from test_harness.slacker import Slacker

setup_logger()


def url_type(arg):
    url = urlparse(arg)
    if all((url.scheme, url.netloc)):
        return arg
    raise TypeError("Invalid URL")


async def main(args):
    """Main Test Harness entrypoint."""
    #
    # The TranslatorTestingModel specifies a top level data model that
    # specifies a given test run. As of March 7, 2024, this data model
    # looks something like this:
    #
    #   TestRunSession:
    #     description: >-
    #       Single run of a TestRunner in a given environment, with a specified
    #       set of test_entities (generally, one or more instances of TestSuite).
    #     is_a: TestEntity
    #     slots:
    #       - components
    #       - test_env
    #       - test_runner_name
    #       - test_run_parameters
    #       - test_entities
    #       - test_case_results
    #       - timestamp
    #     slot_usage:
    #       test_run_parameters:
    #         description: >-
    #           Different TestRunners could expect additional global test
    #           configuration parameters, like the applicable TRAPI version
    #           ("trapi_version") or Biolink Model versions ("biolink_version").
    #       test_entities:
    #         description: >-
    #           Different TestRunners could expect specific kinds of TestEntity
    #           as an input.  These 'test_entities' are one or more instances of
    #           TestAsset, TestCase or (preferably?) TestSuite.
    #
    qid = str(uuid4())[:8]
    logger = get_logger(qid, args["log_level"])
    tests = []
    if "tests_url" in args:
        tests = download_tests(args["suite"], args["tests_url"], logger)
    elif "tests" in args:
        tests = args["tests"]
    else:
        return logger.error(
            "Please run this command with `-h` to see the available options."
        )

    if len(tests) < 1:
        return logger.warning("No tests to run. Exiting.")

    # Create test run in the Information Radiator
    reporter = Reporter(
        base_url=args.get("reporter_url"),
        refresh_token=args.get("reporter_access_token"),
        logger=logger,
    )
    await reporter.get_auth()
    await reporter.create_test_run(next(iter(tests.values())))
    slacker = Slacker()

    trapi_version: Optional[str] = None
    if("trapi_version" in args):
        trapi_version = args["trapi_version"]

    biolink_version: Optional[str] = None
    if("biolink_version" in args):
        biolink_version = args["biolink_version"]

    report = await run_tests(reporter, slacker, tests, trapi_version, biolink_version, logger)

    logger.info("Finishing up test run...")
    await reporter.finish_test_run()

    if args["json_output"]:
        # logger.info("Saving report as JSON...")
        with open("test_report.json", "w") as f:
            json.dump(report, f)

    return logger.info("All tests have completed!")


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
        # default="https://github.com/NCATSTranslator/Tests/archive/refs/heads/main.zip",
        default="https://github.com/NCATSTranslator/Tests/archive/4387dc8f69331e8ecf6a3facf86e547cb4f7824e.zip",
        help="URL to download in order to find the test files",
    )

    run_parser = subparsers.add_parser("run", help="Run a given set of tests")

    run_parser.add_argument(
        "tests",
        type=json.loads,
        help="Path to a file of tests to be run. This would be the same output from downloading the tests via `download_tests()`",
    )

    parser.add_argument(
        "--trapi_version",
        type=str,
        required=False,
        help="TRAPI (SemVer) version assumed for testing (latest release, if not given)",
    )

    parser.add_argument(
        "--biolink_version",
        type=str,
        required=False,
        help="Biolink Model (SemVer) version assumed for testing (latest release, if not given)",
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
        "--save_to_dashboard",
        action="store_true",
        help="Have the Test Harness send the test results to the Testing Dashboard",
    )

    parser.add_argument(
        "--json_output",
        action="store_true",
        help="Save the test results locally in json",
    )

    parser.add_argument(
        "--log_level",
        type=str,
        choices=["ERROR", "WARNING", "INFO", "DEBUG"],
        help="Level of the logs.",
        default="DEBUG",
    )

    args = parser.parse_args()
    asyncio.run(main(vars(args)))


if __name__ == "__main__":
    cli()
