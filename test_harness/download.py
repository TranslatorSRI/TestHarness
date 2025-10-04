"""Download tests."""

import glob
import httpx
import io
import json
import logging
from pathlib import Path
import tempfile
from typing import List, Union, Dict
import zipfile

from translator_testing_model.datamodel.pydanticmodel import (
    TestCase,
    PathfinderTestCase,
    TestSuite,
)


def download_tests(
    suite: Union[str, List[str]],
    url: Path,
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

        with open(tests_paths[0]) as f:
            test_suite = TestSuite.model_validate(json.load(f))

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
    logger.info(f"Passing along {len(test_suite.test_cases)} queries")
    return test_suite.test_cases
