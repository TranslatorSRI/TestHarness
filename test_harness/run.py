"""Run tests through the Test Runners."""
from typing import Optional
from collections import defaultdict
import json
import logging
import time
from tqdm import tqdm
import traceback
from typing import Dict, List

from ARS_Test_Runner.semantic_test import run_semantic_test as run_ars_test
from one_hop_tests import run_onehop_tests
from benchmarks_runner import run_benchmarks

from translator_testing_model.datamodel.pydanticmodel import TestCase

from .reporter import Reporter
from .slacker import Slacker


async def run_tests(
    reporter: Reporter,
    slacker: Slacker,
    tests: Dict[str, TestCase],
    trapi_version: Optional[str],
    biolink_version: Optional[str],
    logger: logging.Logger = logging.getLogger(__name__),
) -> Dict:
    """Send tests through the Test Runners."""
    start_time = time.time()
    logger.info(f"Running {len(tests)} tests...")
    full_report = {
        "PASSED": 0,
        "FAILED": 0,
        "SKIPPED": 0,
    }
    environment = "None"
    await slacker.post_notification(
        messages=[
            f"Running {len(tests)} tests...\n<{reporter.base_path}/test-runs/{reporter.test_run_id}|View in the Information Radiator>"
        ]
    )
    # loop over all tests
    for test in tqdm(tests.values()):
        status = "PASSED"
        environment = test.test_env

        components: List[str] = test.components
        assert components, "Need at least one component to test!"

        # check if acceptance test
        if not test.test_assets or not test.test_case_objective:
            logger.warning(f"Test has missing required fields: {test.id}")
            continue
        if test.test_case_objective == "AcceptanceTest":
            assets = test.test_assets
            test_ids = []
            err_msg = ""
            for asset in assets:
                # create test in Test Dashboard
                test_id = ""
                try:
                    test_id = await reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception:
                    logger.error(f"Failed to create test: {test.id}")
                try:
                    test_input = json.dumps(
                        {
                            "environment": environment,
                            "predicate": test.test_case_predicate_name,
                            "runner_settings": test.test_runner_settings,
                            "expected_output": asset.expected_output,
                            "input_curie": test.test_case_input_id,
                            "output_curie": asset.output_id,
                        },
                        indent=2,
                    )
                    await reporter.upload_log(
                        test_id,
                        "Calling ARS Test Runner with: {test_input}".format(
                            test_input=test_input
                        ),
                    )
                except Exception as e:
                    logger.error(str(e))
                    logger.error(f"Failed to upload logs to test: {test.id}, {test_id}")

            # group all outputs together to make one Translator query
            output_ids = [asset.output_id for asset in assets]
            expected_outputs = [asset.expected_output for asset in assets]
            test_inputs = [
                environment,
                test.test_case_predicate_name,
                test.test_runner_settings,
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
                    "results": defaultdict(lambda: {"error": err_msg}),
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
                full_report[status] += 1
                if not err_msg:
                    # only upload ara labels if the test ran successfully
                    try:
                        labels = [
                            {
                                "key": ara,
                                "value": result["status"],
                            }
                            for ara, result in test_result["result"].items()
                        ]
                        await reporter.upload_labels(test_id, labels)
                    except Exception as e:
                        logger.warning(f"[{test.id}] failed to upload labels: {e}")
                try:
                    await reporter.upload_log(
                        test_id, json.dumps(test_result, indent=4)
                    )
                except Exception as e:
                    logger.error(f"[{test.id}] failed to upload logs.")
                try:
                    await reporter.finish_test(test_id, status)
                except Exception as e:
                    logger.error(f"[{test.id}] failed to upload finished status.")
            # full_report[test["test_case_input_id"]]["ars"] = ars_result

        elif test.test_case_objective in ("StandardsValidationTest", "OneHopTest"):
            # One Hop Tests and TRAPI/Biolink ("standards") validation test have comparable test inputs and
            # configuration, except for the TestRunner that is executed, hence, are aggregated in this section.

            # 1. Standards Validation TestRunner uses the "reasoner-validator" package to validate
            # TRAPI and Biolink Model compliance of inputs and outputs templated TRAPI queries.

            # 2. One Hop Tests seem a bit different from other types of tests. Generally, a single OneHopTest TestAsset
            # is single S-P-O triplet with categories, used internally to generate a half dozen distinct TestCases and
            # a single KP or ARA TRAPI service is called several times, once for each generated TestCase.
            # There is no external sense of "ExpectedOutput" rather, test pass, fail or skip status is an intrinsic
            # outcome pertaining to the recovery of the input test asset values in the output of the various TestCases.
            # A list of such TestAssets run against a given KP or ARA target service, could be deemed a "TestSuite".
            # But a set of such TestSuites could be run in batch within a given TestSession. It is somewhat hard
            # to align with this framework to the new Translator Test Harness, or at least, not as efficient to run.
            #
            # To make this work, we will do some violence to the testing model and wrap each input S-P-O triple as a
            # single TestCase, extract a single associated TestAsset, which we'll feed in with the value of the
            # TestCase 'components' field value, which will be taken as the 'infores' of the ARA or KP to be tested.
            # Internally, we'll generate and run TRAPI queries of the actual TestCase instances against the 'infores'
            # specified resources, then return the results, suitably indexed.  Alternately, if the specified target
            # is the 'ars', then the returned results will be indexed by 'pks'(?)

            # As indicated above, we only expect a single TestAsset
            asset = test.test_assets[0]

            # Remapping fields semantically onto StandardsValidationTest/OneHopTest inputs
            test_inputs = {
                "environment": environment,
                "components": components,
                "trapi_version": trapi_version,
                "biolink_version": biolink_version,
                "runner_settings": asset.test_runner_settings,

                "subject_id": asset.input_id,
                "subject_category": asset.input_category,
                "predicate_id": asset.predicate_id,
                "object_id": asset.output_id,
                "object_category": asset.output_category

                # TODO: not sure if or how to set any log_level here
                # log_level: "?"
            }
            err_msg = ""

            # create test in Test Dashboard
            test_id = ""
            try:
                test_id = await reporter.create_test(test, asset)
            except Exception:
                logger.error(
                    f"Failed to create {test.test_case_objective}: {test.id}"
                )

            try:
                test_input_json = json.dumps(test_inputs, indent=2)
                await reporter.upload_log(
                    test_id,
                    f"Calling {test.test_case_objective} with: {test_input_json}"
                )
            except Exception as e:
                logger.error(str(e))
                logger.error(f"Failed to upload logs to {test.test_case_objective}: {test.id}")

            try:
                if test.test_case_objective == "StandardsValidationTest":
                    raise NotImplementedError("'StandardsValidationTest' not implemented yet")
                elif test.test_case_objective in ("StandardsValidationTest", "OneHopTest"):
                    # we pass the test arguments as named parameters,
                    # instead of a simple argument sequence.
                    test_result = await run_onehop_tests(**test_inputs)
                else:
                    raise NotImplementedError(f"Unknown test case_objective: {test.test_case_objective}")

            except Exception as e:
                err_msg = f"{test.test_case_objective} Test Runner failed with {traceback.format_exc()}"
                logger.error(f"[{test.id}] {err_msg}")
                test_result = {
                    "pks": {},
                    # this will effectively act as a list that we access by index down below
                    "results": defaultdict(lambda: {"error": err_msg}),
                }

            test_result = {
                "pks": test_result["pks"],
                "result": test_result["results"],
            }
            full_report[status] += 1
            if not err_msg:
                # only upload ara labels if the test ran successfully
                try:
                    labels = [
                        {
                            "key": ara,
                            "value": result["status"],
                        }
                        for ara, result in test_result["result"].items()
                    ]
                    await reporter.upload_labels(test_id, labels)
                except Exception as e:
                    logger.warning(f"[{test.id}] failed to upload labels: {e}")
            try:
                await reporter.upload_log(
                    test_id, json.dumps(test_result, indent=4)
                )
            except Exception as e:
                logger.error(f"[{test.id}] failed to upload logs.")
            try:
                await reporter.finish_test(test_id, status)
            except Exception as e:
                logger.error(f"[{test.id}] failed to upload finished status.")

        elif test.test_case_objective == "QuantitativeTest":
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
                    components[0],
                ]
                await reporter.upload_log(
                    test_id,
                    f"Calling Benchmark Test Runner with: {json.dumps(test_inputs, indent=4)}",
                )
                benchmark_results, screenshots = await run_benchmarks(*test_inputs)
                await reporter.upload_log(test_id, ("\n").join(benchmark_results))
                # ex:
                # {
                #   "aragorn": {
                #     "precision": screenshot
                #   }
                # }
                for target_screenshots in screenshots.values():
                    for screenshot in target_screenshots.values():
                        await reporter.upload_screenshot(test_id, screenshot)
                await reporter.finish_test(test_id, "PASSED")
                full_report["PASSED"] += 1
            except Exception as e:
                logger.error(f"Benchmarks failed with {e}: {traceback.format_exc()}")
                full_report["FAILED"] += 1
                try:
                    await reporter.upload_log(test_id, traceback.format_exc())
                except Exception:
                    logger.error(
                        f"Failed to upload fail logs for test {test_id}: {traceback.format_exc()}"
                    )
                await reporter.finish_test(test_id, "FAILED")
        else:
            try:
                test_id = await reporter.create_test(test, test)
                logger.error(f"Unsupported test type: {test.id}")
                await reporter.upload_log(
                    test_id, f"Unsupported test type in test: {test.id}"
                )
                status = "FAILED"
                await reporter.finish_test(test_id, status)
            except Exception:
                logger.error(f"Failed to report errors with: {test.id}")

    await slacker.post_notification(
        messages=[
            """Test Suite: {test_suite_id}\nDuration: {duration} | Environment: {env}\n<{ir_url}|View in the Information Radiator>\n> Test Results:\n> Passed: {num_passed}, Failed: {num_failed}, Skipped: {num_skipped}""".format(
                test_suite_id=1,
                duration=round(time.time() - start_time, 2),
                env=environment,
                ir_url=f"{reporter.base_path}/test-runs/{reporter.test_run_id}",
                num_passed=full_report["PASSED"],
                num_failed=full_report["FAILED"],
                num_skipped=full_report["SKIPPED"],
            )
        ]
    )
    return full_report
