"""Run tests through the Test Runners."""

from collections import defaultdict
import httpx
import json
import logging
import time
from tqdm import tqdm
import traceback
from typing import Dict

from ARS_Test_Runner.semantic_test import run_semantic_test as run_ars_test

# from benchmarks_runner import run_benchmarks

from translator_testing_model.datamodel.pydanticmodel import TestCase

from .reporter import Reporter
from .slacker import Slacker
from .result_collector import ResultCollector
from .utils import normalize_curies, get_tag


async def run_tests(
    reporter: Reporter,
    slacker: Slacker,
    tests: Dict[str, TestCase],
    logger: logging.Logger = logging.getLogger(__name__),
    args: Dict[str, any] = {},
) -> Dict:
    """Send tests through the Test Runners."""
    start_time = time.time()
    logger.info(f"Running {len(tests)} tests...")
    full_report = {
        "PASSED": 0,
        "FAILED": 0,
        "SKIPPED": 0,
    }
    env = "None"
    await slacker.post_notification(
        messages=[
            f"Running {args['suite']} ({sum([len(test.test_assets) for test in tests.values()])} tests)...\n<{reporter.base_path}/test-runs/{reporter.test_run_id}|View in the Information Radiator>"
        ]
    )
    collector = ResultCollector()
    # loop over all tests
    for test in tqdm(tests.values()):
        status = "PASSED"
        env = test.test_env
        # check if acceptance test
        if not test.test_assets or not test.test_case_objective:
            logger.warning(f"Test has missing required fields: {test.id}")
            continue
        if test.test_case_objective == "AcceptanceTest":
            assets = test.test_assets
            test_ids = []
            biolink_object_aspect_qualifier = ""
            biolink_object_direction_qualifier = ""
            for qualifier in test.qualifiers:
                if qualifier.parameter == "biolink_object_aspect_qualifier":
                    biolink_object_aspect_qualifier = qualifier.value
                elif qualifier.parameter == "biolink_object_direction_qualifier":
                    biolink_object_direction_qualifier = qualifier.value

            # normalize all the curies
            curies = [asset.output_id for asset in assets]
            curies.append(test.test_case_input_id)
            normalized_curies = await normalize_curies(test, logger)
            input_curie = normalized_curies[test.test_case_input_id]["id"]["identifier"]
            # try and get normalized input category, but default to original
            # input_category = normalized_curies[test.test_case_input_id].get(
            #     "type", [test.input_category]
            # )[0]
            # TODO: figure out the right way to handle input category wrt normalization
            input_category = test.input_category

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
                            "environment": test.test_env,
                            "predicate": test.test_case_predicate_name,
                            "runner_settings": test.test_runner_settings,
                            "expected_output": asset.expected_output,
                            "biolink_object_aspect_qualifier": biolink_object_aspect_qualifier,
                            "biolink_object_direction_qualifier": biolink_object_direction_qualifier,
                            "input_category": input_category,
                            "input_curie": input_curie,
                            "output_curie": normalized_curies[asset.output_id]["id"][
                                "identifier"
                            ],
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
            output_ids = [
                normalized_curies[asset.output_id]["id"]["identifier"]
                for asset in assets
            ]
            expected_outputs = [asset.expected_output for asset in assets]
            test_inputs = [
                test.test_env,
                test.test_case_predicate_name,
                test.test_runner_settings,
                expected_outputs,
                biolink_object_aspect_qualifier,
                biolink_object_direction_qualifier,
                input_category,
                input_curie,
                output_ids,
            ]
            try:
                ars_result, ars_url = await run_ars_test(*test_inputs)
            except Exception as e:
                err_msg = f"ARS Test Runner failed with {traceback.format_exc()}"
                logger.error(f"[{test.id}] {err_msg}")
                ars_result = {
                    "pks": {},
                    # this will effectively act as a list that we access by index down below
                    "results": defaultdict(lambda: {"error": err_msg}),
                }
                # full_report[test["test_case_input_id"]]["ars"] = {"error": str(e)}
            try:
                ars_pk = ars_result.get("pks", {}).get("parent_pk")
                if ars_pk:
                    async with httpx.AsyncClient() as client:
                        await client.post(f"{ars_url}retain/{ars_pk}")
            except Exception as e:
                logger.error(f"Failed to retain PK on ARS.")
            # grab individual results for each asset
            for index, (test_id, asset) in enumerate(zip(test_ids, assets)):
                status = "PASSED"
                try:
                    results = ars_result.get("results", [])
                    if isinstance(results, list):
                        test_result = {
                            "pks": ars_result.get("pks", {}),
                            "result": results[index],
                        }
                    elif isinstance(results, dict):
                        # make sure it has a single error message
                        assert "error" in results
                        test_result = {
                            "pks": ars_result.get("pks", {}),
                            "result": results,
                        }
                    else:
                        # got something completely unexpected from the ARS Test Runner
                        raise Exception()
                    # grab only ars result if it exists, otherwise default to failed
                    if test_result["result"].get("error") is not None:
                        status = "SKIPPED"
                    else:
                        status = (
                            test_result["result"].get("ars", {}).get("status", "FAILED")
                        )
                    full_report[status] += 1
                    collector.collect_result(test, asset, test_result, f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}")
                    if not err_msg and status != "SKIPPED":
                        # only upload ara labels if the test ran successfully
                        try:
                            labels = [
                                {
                                    "key": ara,
                                    "value": get_tag(result),
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
                except Exception as e:
                    logger.error(
                        f"[{test.id}] failed to parse test results: {ars_result}"
                    )
                    try:
                        await reporter.upload_log(
                            test_id,
                            f"Failed to parse results: {json.dumps(ars_result)}",
                        )
                    except Exception as e:
                        logger.error(f"[{test.id}] failed to upload failure log.")
                try:
                    await reporter.finish_test(test_id, status)
                except Exception as e:
                    logger.error(f"[{test.id}] failed to upload finished status.")
            # full_report[test["test_case_input_id"]]["ars"] = ars_result
        elif test.test_case_objective == "QuantitativeTest":
            continue
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
            """Test Suite: {test_suite}\nDuration: {duration} | Environment: {env}\n<{ir_url}|View in the Information Radiator>\n> Test Results:\n> Passed: {num_passed}, Failed: {num_failed}, Skipped: {num_skipped}""".format(
                test_suite=args["suite"],
                duration=round(time.time() - start_time, 2),
                env=env,
                ir_url=f"{reporter.base_path}/test-runs/{reporter.test_run_id}",
                num_passed=full_report["PASSED"],
                num_failed=full_report["FAILED"],
                num_skipped=full_report["SKIPPED"],
            )
        ]
    )
    await slacker.upload_test_results_file(filename, collector.stats)
    await slacker.upload_test_results_file(filename, collector.csv)
    return full_report
