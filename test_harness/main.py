"""Translator SRI Automated Test Harness."""
from argparse import ArgumentParser
import asyncio
import json
from urllib.parse import urlparse
from uuid import uuid4

from .run import run_tests
from .download import download_tests
from .logger import get_logger, setup_logger
from .reporter import Reporter

setup_logger()


def url_type(arg):
    url = urlparse(arg)
    if all((url.scheme, url.netloc)):
        return arg
    raise TypeError("Invalid URL")


async def main(args):
    """Main Test Harness entrypoint."""
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
    await reporter.create_test_run()
    report = await run_tests(reporter, tests, logger)

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
        default="https://github.com/NCATSTranslator/Tests/archive/refs/heads/main.zip",
        help="URL to download in order to find the test files",
    )

    run_parser = subparsers.add_parser("run", help="Run a given set of tests")

    run_parser.add_argument(
        "tests",
        type=json.loads,
        help="Path to a file of tests to be run. This would be the same output from downloading the tests via `download_tests()`",
    )

    parser.add_argument(
        "--reporter_url",
        type=url_type,
        default="http://informationradiator.apps.renci.org",
        help="URL of the Testing Dashboard",
    )

    parser.add_argument(
        "--reporter_access_token",
        type=str,
        default="fillthisin",
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
