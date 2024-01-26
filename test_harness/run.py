"""Run tests through the Test Runners."""
from collections import defaultdict
import json
import logging
from tqdm import tqdm
import traceback
from typing import Dict, List

from ARS_Test_Runner.semantic_test import run_semantic_test as run_ars_test
from benchmarks_runner import run_benchmarks

from translator_testing_model.datamodel.pydanticmodel import TestCase

from .reporter import Reporter


async def run_tests(reporter: Reporter, tests: List[TestCase], logger: logging.Logger) -> Dict:
    """Send tests through the Test Runners."""
    # tests = [TestCase.parse_obj(test) for test in tests]
    logger.info(f"Running {len(tests)} tests...")
    full_report = {}
    # loop over all tests
    for test in tqdm(tests.values()):
        status = "PASSED"
        # check if acceptance test
        if not test.test_assets or not test.test_case_objective:
            logger.warning(f"Test has missing required fields: {test.id}")
            continue
        if test.test_case_objective == "AcceptanceTest":
            assets = test.test_assets
            test_ids = []
            err_msg = ''
            for asset in assets:
                # create test in Test Dashboard
                test_id = ""
                try:
                    test_id = await reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception:
                    logger.error(f"Failed to create test: {test.id}")
                try:
                    test_input = json.dumps({
                        # "environment": test.test_env,
                        "environment": "test",
                        "predicate": test.test_case_predicate_name,
                        "runner_settings": test.test_case_runner_settings,
                        "expected_output": asset.expected_output,
                        "input_curie": test.test_case_input_id,
                        "output_curie": asset.output_id,
                    }, indent=2)
                    await reporter.upload_log(test_id, "Calling ARS Test Runner with: {test_input}".format(
                        test_input=test_input
                    ))
                except Exception as e:
                    logger.error(str(e))
                    logger.error(f"Failed to upload logs to test: {test.id}, {test_id}")

            # group all outputs together to make one Translator query
            output_ids = [asset.output_id for asset in assets]
            expected_outputs = [asset.expected_output for asset in assets]
            test_inputs = [
                # test.test_env,
                "test",
                test.test_case_predicate_name,
                test.test_case_runner_settings,
                expected_outputs,
                test.test_case_input_id,
                output_ids,
            ]
            try:
                ars_result = await run_ars_test(*test_inputs)
            except Exception as e:
                err_msg = f"ARS Test Runner failed with {traceback.format_exc()}"
                logger.error(f"[{test.id}] {err_msg}")
                ars_result = {
                    "pks": {},
                    # this will effectively act as a list that we access by index down below
                    "results": defaultdict(lambda:{"error": err_msg}),
                }
                # full_report[test["test_case_input_id"]]["ars"] = {"error": str(e)}
            # grab individual results for each asset
            for index, (test_id, asset) in enumerate(zip(test_ids, assets)):
                test_result = {
                    "pks": ars_result["pks"],
                    "result": ars_result["results"][index],
                }
                # grab only ars result if it exists, otherwise default to failed
                status = test_result["result"].get("ars", {}).get("status", "FAILED")
                if not err_msg:
                    # only upload ara labels if the test ran successfully
                    try:
                        labels = [
                            {
                                "key": ara,
                                "value": result["status"],
                            } for ara, result in test_result["result"].items()
                        ]
                        await reporter.upload_labels(test_id, labels)
                    except Exception as e:
                        logger.warning(f"[{test.id}] failed to upload labels: {e}")
                try:
                    await reporter.upload_log(test_id, json.dumps(test_result, indent=4))
                except Exception as e:
                    logger.error(f"[{test.id}] failed to upload logs.")
                try:
                    await reporter.finish_test(test_id, status)
                except Exception as e:
                    logger.error(f"[{test.id}] failed to upload finished status.")
            # full_report[test["test_case_input_id"]]["ars"] = ars_result
        elif test["test_case_objective"] == "QuantitativeTest":
            assets = test.test_assets[0]
            try:
                test_id = await reporter.create_test(test, assets)
            except Exception:
                logger.error(f"Failed to create test: {test.id}")
                continue
            try:
                test_inputs = [
                    assets.id,
                    # TODO: update this. Assumes is going to be ARS
                    test.components[0],
                ]
                await reporter.upload_log(test_id, f"Calling Benchmark Test Runner with: {json.dumps(test_inputs, indent=4)}")
                benchmark_results, screenshots = await run_benchmarks(*test_inputs)
                await reporter.upload_log(test_id, ("\n").join(benchmark_results))
                for screenshot in screenshots.values():
                    await reporter.upload_screenshot(test_id, screenshot)
                await reporter.finish_test(test_id, "PASSED")
            except Exception as e:
                logger.error(f"Benchmarks failed with {e}: {traceback.format_exc()}")
                try:
                    await reporter.upload_log(test_id, traceback.format_exc())
                except Exception:
                    logger.error(f"Failed to upload fail logs for test {test_id}: {traceback.format_exc()}")
                await reporter.finish_test(test_id, "FAILED")
        else:
            try:
                test_id = await reporter.create_test(test, test)
                logger.error(f"Unsupported test type: {test.id}")
                await reporter.upload_log(test_id, f"Unsupported test type in test: {test.id}")
                status = "FAILED"
                await reporter.finish_test(test_id, status)
            except Exception:
                logger.error(f"Failed to report errors with: {test.id}")
    return full_report
