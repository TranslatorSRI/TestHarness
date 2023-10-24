"""Download tests."""
import glob
import httpx
import io
import json
import logging
from pathlib import Path
from typing import List, Union
import zipfile

from .models import TestCase, TestSuite


def download_tests(suite: Union[str, List[str]], url: Path, logger: logging.Logger) -> List[TestCase]:
    """Download tests from specified location."""
    assert Path(url).suffix == ".zip"
    logger.info(f"Downloading tests from {url}...")
    # download file from internet
    with httpx.Client(follow_redirects=True) as client:
        tests_zip = client.get(url)
        tests_zip.raise_for_status()
        # we already checked if zip before download, so now unzip
        with zipfile.ZipFile(io.BytesIO(tests_zip.read())) as zip_ref:
            zip_ref.extractall("./Translator-Tests")

    # Find all json files in the downloaded zip
    tests_paths = glob.glob("./Translator-Tests/**/*.json", recursive=True)

    all_tests = []
    suites = suite if type(suite) == list else [suite]
    test_case_ids = []

    logger.info(f"Reading in {len(tests_paths)} tests...")

    # do the reading of the tests and make a tests list
    for test_path in tests_paths:
        with open(test_path, "r") as f:
            test_json = json.load(f)
            try:
                test_suite = TestSuite.parse_obj(test_json)
                if test_suite.id in suites:
                    # if suite is selected, grab all its test cases
                    test_case_ids.extend(test_suite.case_ids)
            except Exception as e:
                # not a Test Suite
                pass
            try:
                test_case = TestCase.parse_obj(test_json)
                all_tests.append(test_case)
            except Exception as e:
                # not a Test Case
                pass

    # only return the tests from the specified suites
    tests = filter(lambda x: x in test_case_ids, all_tests)
    logger.info(f"Passing along {len(tests)} tests")
    return tests
