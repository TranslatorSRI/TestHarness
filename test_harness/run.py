"""Run tests through the Test Runners."""
import logging
from pydantic import validate_arguments
from typing import List, Dict

from ui_test_runner import run_ui_test
from ARS_Test_Runner.semantic_test import run_semantic_test as run_ars_test

from .models import TestCase

logger = logging.getLogger(__name__)


@validate_arguments
def run_tests(tests: List[TestCase]) -> Dict:
    """Send tests through the Test Runners."""
    full_report = {}
    # loop over all tests
    for test in tests:
        # check if acceptance test
        if test.type == "acceptance":
            full_report[test.input_curie] = {}
            try:
                ui_result = run_ui_test(
                    test.env,
                    test.query_type,
                    test.expected_output,
                    test.input_curie,
                    test.output_curie,
                )
                full_report[test.input_curie]["ui"] = ui_result
            except Exception as e:
                logger.error(f"UI test failed with {e}")
                full_report[test.input_curie]["ui"] = {"error": str(e)}
            try:
                ars_result = run_ars_test(
                    test.env,
                    test.query_type,
                    test.expected_output,
                    test.input_curie,
                    test.output_curie,
                )
                full_report[test.input_curie]["ars"] = ars_result
            except Exception as e:
                logger.error(f"ARS test failed with {e}")
                full_report[test.input_curie]["ars"] = {"error": str(e)}
        elif test.type == "quantitative":
            # implement the Benchmark Runner
            logger.warning("Quantitative tests are not supported yet.")
        else:
            logger.warning(f"Unsupported test type: {test.type}")

    return full_report
