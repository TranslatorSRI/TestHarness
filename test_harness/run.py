"""Run tests through the Test Runners."""

import json
import logging
import time
from tqdm import tqdm
import traceback
from typing import Dict

from ARS_Test_Runner.semantic_test import pass_fail_analysis

# from benchmarks_runner import run_benchmarks

from translator_testing_model.datamodel.pydanticmodel import TestCase

from test_harness.runner.query_runner import QueryRunner
from test_harness.reporter import Reporter
from test_harness.slacker import Slacker
from test_harness.result_collector import ResultCollector
from test_harness.utils import get_tag, hash_test_asset


async def run_tests(
    reporter: Reporter,
    slacker: Slacker,
    tests: Dict[str, TestCase],
    logger: logging.Logger = logging.getLogger(__name__),
    args: Dict[str, any] = {},
) -> Dict:
    """Send tests through the Test Runners."""
    start_time = time.time()
    logger.info(f"Running {len(tests)} queries...")
    full_report = {
        "PASSED": 0,
        "FAILED": 0,
        "SKIPPED": 0,
    }
    env = "None"
    await slacker.post_notification(
        messages=[
            f"Running {args['suite']} ({sum([len(test.test_assets) for test in tests.values()])} tests, {len(tests.values())} queries)...\n<{reporter.base_path}/test-runs/{reporter.test_run_id}|View in the Information Radiator>"
        ]
    )
    query_runner = QueryRunner(logger)
    logger.info("Runner is getting service registry")
    await query_runner.retrieve_registry(trapi_version="1.5.0")
    collector = ResultCollector(logger)
    # loop over all tests
    for test in tqdm(tests.values()):
        status = "PASSED"
        env = test.test_env
        # check if acceptance test
        if not test.test_assets or not test.test_case_objective:
            logger.warning(f"Test has missing required fields: {test.id}")
            continue

        query_responses, normalized_curies = await query_runner.run_queries(test)
        if test.test_case_objective == "AcceptanceTest":
            test_ids = []

            for asset in test.test_assets:
                # throw out any assets with unsupported expected outputs, i.e. OverlyGeneric
                if asset.expected_output not in collector.query_types:
                    logger.warning(
                        f"Asset id {asset.id} has unsupported expected output."
                    )
                    continue
                # create test in Test Dashboard
                test_id = ""
                try:
                    test_id = await reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception:
                    logger.error(f"Failed to create test: {test.id}")
                    continue

                test_asset_hash = hash_test_asset(asset)
                test_query = query_responses.get(test_asset_hash)
                if test_query is not None:
                    message = json.dumps(test_query["query"], indent=2)
                else:
                    message = "Unable to retrieve response for test asset."
                await reporter.upload_log(
                    test_id,
                    message,
                )

                if test_query is not None:
                    report = {
                        "pks": test_query["pks"],
                        "result": {},
                    }
                    for agent, response in test_query["responses"].items():
                        report["result"][agent] = {}
                        agent_report = report["result"][agent]
                        try:
                            if response["status_code"] > 299:
                                agent_report["status"] = "FAILED"
                                if response["status_code"] == "598":
                                    agent_report["message"] = "Timed out"
                                else:
                                    agent_report["message"] = (
                                        f"Status code: {response['status_code']}"
                                    )
                            elif (
                                "response" not in response
                                or "message" not in response["response"]
                            ):
                                agent_report["status"] = "FAILED"
                                agent_report["message"] = "Test Error"
                            elif (
                                response["response"]["message"].get("results") is None
                                or len(response["response"]["message"]["results"]) == 0
                            ):
                                agent_report["status"] = "DONE"
                                agent_report["message"] = "No results"
                            else:
                                await pass_fail_analysis(
                                    report["result"],
                                    agent,
                                    response["response"]["message"]["results"],
                                    normalized_curies[asset.output_id],
                                    asset.expected_output,
                                )
                        except Exception as e:
                            logger.error(f"Failed to run analysis on {agent}: {e}")
                            agent_report["status"] = "FAILED"
                            agent_report["message"] = "Test Error"

                    status = "PASSED"
                    # grab only ars result if it exists, otherwise default to failed
                    ars_status = report["result"].get("ars", {}).get("status")
                    status = ars_status if ars_status is not None else "SKIPPED"
                    full_report[status] += 1

                    collector.collect_result(
                        test,
                        asset,
                        report["result"],
                        test_query["pks"].get("parent_pk"),
                        f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}",
                    )

                    if status != "SKIPPED":
                        # only upload ara labels if the test ran successfully
                        try:
                            labels = [
                                {
                                    "key": ara,
                                    "value": get_tag(report["result"][ara]),
                                }
                                for ara in collector.agents
                                if ara in report["result"]
                            ]
                            await reporter.upload_labels(test_id, labels)
                        except Exception as e:
                            logger.warning(f"[{test.id}] failed to upload labels: {e}")
                    try:
                        await reporter.upload_log(test_id, json.dumps(report, indent=4))
                    except Exception:
                        logger.error(f"[{test.id}] failed to upload logs.")
                else:
                    status = "SKIPPED"

                try:
                    await reporter.finish_test(test_id, status)
                except Exception:
                    logger.error(f"[{test.id}] failed to upload finished status.")
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

        # delete this big object to help out the garbage collector
        del query_responses

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
    await slacker.upload_test_results_file(reporter.test_name, "json", collector.stats)
    await slacker.upload_test_results_file(reporter.test_name, "csv", collector.csv)
    return full_report
