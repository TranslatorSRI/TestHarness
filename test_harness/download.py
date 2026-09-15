"""Download tests, or read them from local files."""

import glob
import io
import json
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Union

import httpx
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestCase,
    TestCase,
    TestSuite,
)

# The editable test suites shipped with the repo, used when --tests_dir isn't
# given and there's no `test_suites/` in the working directory.
BUNDLED_TESTS_DIR = Path(__file__).resolve().parent / "test_suites"
DEFAULT_TESTS_DIR = "test_suites"


def _read_test_suite(
    path: Union[str, Path],
) -> Dict[str, Union[TestCase, PathfinderTestCase]]:
    """Parse one test suite JSON file into its test cases."""
    with open(path) as f:
        return TestSuite.model_validate(json.load(f)).test_cases


def resolve_tests_dir(tests_dir: Union[str, None]) -> Path:
    """Pick the directory to read local test suites out of.

    An explicit ``--tests_dir`` is used as given. Otherwise `test_suites/` in
    the working directory wins when it exists -- so a suite you are editing in
    your own checkout is found without a flag -- and the copy shipped next to
    the package is the fallback, which is what makes this work from anywhere in
    an editable install.
    """
    if tests_dir:
        return Path(tests_dir)
    cwd_dir = Path(DEFAULT_TESTS_DIR)
    if cwd_dir.is_dir():
        return cwd_dir
    return BUNDLED_TESTS_DIR


def load_tests(
    suite: str,
    tests_dir: Union[str, None],
    logger: logging.Logger,
) -> Dict[str, Union[TestCase, PathfinderTestCase]]:
    """Read a test suite from a local JSON file instead of downloading a zip.

    ``suite`` is the file's basename without ``.json``, matching how the
    downloaded suites are named, so the same suite name works either way.
    """
    directory = resolve_tests_dir(tests_dir)
    path = directory / f"{suite}.json"
    if not path.is_file():
        if directory.is_dir():
            available = sorted(p.stem for p in directory.glob("*.json"))
            hint = (
                f"available suites: {', '.join(available)}"
                if available
                else "that directory has no .json suites in it"
            )
        else:
            hint = "that directory doesn't exist"
        logger.error(f"No test suite '{suite}' in {directory.resolve()}; {hint}.")
        return {}

    logger.info(f"Reading tests from {path.resolve()}...")
    try:
        test_cases = _read_test_suite(path)
    except Exception as e:
        logger.error(f"Failed to parse {path.resolve()}: {e}")
        return {}
    logger.info(f"Passing along {len(test_cases.keys())} queries")
    return test_cases


def download_tests(
    suite: Union[str, List[str]],
    url: str,
    logger: logging.Logger,
) -> Dict[str, Union[TestCase, PathfinderTestCase]]:
    """Download tests from specified location."""
    assert Path(url).suffix == ".zip"
    logger.info(f"Downloading tests from {url}...")
    # download file from internet
    with httpx.Client(follow_redirects=True) as client:
        tests_zip = client.get(url)
        tests_zip.raise_for_status()
        # we already checked if zip before download, so now unzip
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(io.BytesIO(tests_zip.read())) as zip_ref:
            zip_ref.extractall(tmpdir)

        # Find all json files in the downloaded zip
        # tests_paths = glob.glob(f"{tmpdir}/**/*.json", recursive=True)

        tests_paths = glob.glob(f"{tmpdir}/*/test_suites/{suite}.json")

        test_cases = _read_test_suite(tests_paths[0])

        # all_tests = []
        # suites = suite if type(suite) == list else [suite]
        # test_case_ids = []

        # logger.info(f"Reading in {len(test_suite.test_cases)} tests...")

        # do the reading of the tests and make a tests list
        # for test_case in test_suite.test_cases:
        #     try:
        # test_suite = TestSuite.parse_obj(test_json)
        # if test_suite.id in suites:
        # if test_json["test_case_type"] == "acceptance":
        #     # if suite is selected, grab all its test cases
        #     # test_case_ids.extend(test_suite.case_ids)
        #     all_tests.append(test_json)
        #     continue
        #     if test_json.get("test_env"):
        #         # only grab Test Cases and not Test Assets
        #         all_tests.append(test_json)
        # except Exception as e:
        #     # not a Test Suite
        #     pass
        # try:
        #     # test_case = TestCase.parse_obj(test_json)
        #     if test_json["test_case_type"] == "quantitative":
        #         all_tests.append(test_json)
        #         continue
        #     # all_tests.append(test_json)
        # except Exception as e:
        #     # not a Test Case
        #     print(e)
        #     pass

    # only return the tests from the specified suites
    # tests = list(filter(lambda x: x in test_case_ids, all_tests))
    # tests = [
    #     test
    #     for test in all_tests
    #     for asset in test.test_assets
    #     if asset.output_id
    # ]
    # for test in tests:
    #     test.test_case_type = "acceptance"
    # tests = all_tests
    # tests = list(filter((lambda x: x for x in all_tests for asset in x.test_assets if asset.output_id), all_tests))
    logger.info(f"Passing along {len(test_cases.keys())} queries")
    return test_cases


if __name__ == "__main__":
    tests = download_tests(
        "performance_tests",
        "https://github.com/NCATSTranslator/Tests/archive/refs/heads/performance_tests.zip",
        logging.Logger("tester"),
    )
    for test_case_id, test in tests.items():
        print(type(test))
