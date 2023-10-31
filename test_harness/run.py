"""Run tests through the Test Runners."""
import logging
from tqdm import tqdm
from typing import Dict, List

from ui_test_runner import run_ui_test
from ARS_Test_Runner.semantic_test import run_semantic_test as run_ars_test

from .models import TestCase
from .models import Tests
from .reporter import Reporter


async def run_tests(reporter: Reporter, tests: List[TestCase], logger: logging.Logger) -> Dict:
    """Send tests through the Test Runners."""
    tests = [TestCase.parse_obj(test) for test in tests]
    logger.info(f"Running {len(tests)} tests...")
    full_report = {}
    # loop over all tests
    for test in tqdm(tests):
        # create test in Test Dashboard
        test_id = await reporter.create_test(test)
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
        
        await reporter.finish_test(test_id, "PASSED")

    return full_report
