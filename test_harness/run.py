"""Run tests through the Test Runners."""

import json
import logging
from typing import Any, Dict, Union

from tqdm import tqdm

# from standards_validation_test_runner import StandardsValidationTest
# from benchmarks_runner import run_benchmarks
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestCase,
    PerformanceTestCase,
    TestCase,
)

from test_harness.acceptance_test_runner import run_acceptance_pass_fail_analysis
from test_harness.pathfinder_test_runner import pathfinder_pass_fail_analysis
from test_harness.performance_test_runner import run_performance_test
from test_harness.reporter import Reporter
from test_harness.result_collector import ResultCollector
from test_harness.runner.generate_query import generate_query
from test_harness.runner.query_runner import QueryRunner, env_map
from test_harness.utils import get_tag, hash_test_asset


def run_tests(
    tests: Dict[str, Union[TestCase, PathfinderTestCase]],
    reporter: Reporter,
    collector: ResultCollector,
    logger: logging.Logger = logging.getLogger(__name__),
    args: Dict[str, Any] = {},
) -> None:
    """Send tests through the Test Runners."""
    logger.info(f"Running {len(tests)} queries...")
    query_runner = QueryRunner(logger)
    logger.info("Runner is getting service registry")
    query_runner.retrieve_registry(trapi_version=args["trapi_version"])
    # loop over all tests
    for test in tqdm(list(tests.values())):
        status = "PASSED"
        # check if acceptance test
        if not test.test_assets or not test.test_case_objective:
            logger.warning(f"Test has missing required fields: {test.id}")
            continue

        query_responses = {}
        if test.test_case_objective == "AcceptanceTest":
            query_responses, normalized_curies = query_runner.run_queries(test)
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
                    test_id = reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception as e:
                    logger.error(f"Failed to create test: {test.id}")
                    continue

                test_asset_hash = hash_test_asset(asset)
                test_query = query_responses.get(test_asset_hash)
                if test_query is not None:
                    message = json.dumps(test_query["query"], indent=2)
                else:
                    message = "Unable to retrieve response for test asset."
                reporter.upload_log(
                    test_id,
                    message,
                )

                if test_query is not None:
                    report = {
                        "pks": test_query["pks"],
                        "result": {},
                    }
                    if isinstance(test, PathfinderTestCase):
                        report["test_details"] = {
                            "minimum_required_path_nodes": asset.minimum_required_path_nodes,
                            "expected_path_nodes": "; ".join(
                                [
                                    ",".join(
                                        [
                                            normalized_curies[path_node_id]
                                            for path_node_id in path_node.ids
                                        ]
                                    )
                                    for path_node in asset.path_nodes
                                ]
                            ),
                        }
                    for agent, response in test_query["responses"].items():
                        report["result"][agent] = {
                            # "trapi_validation": "NA",
                        }
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
                                continue
                            elif (
                                "response" not in response
                                or "message" not in response["response"]
                            ):
                                agent_report["status"] = "FAILED"
                                agent_report["message"] = "Test Error"
                                continue
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse basic response fields from {agent}: {e}"
                            )
                        # try:
                        #     svt = StandardsValidationTest(
                        #         test_asset=asset,
                        #         environment=test.test_env,
                        #         component=agent,
                        #         trapi_version=args["trapi_version"],
                        #         biolink_version="suppress",
                        #         runner_settings="Inferred",
                        #     )
                        #     results = svt.test_case_processor(
                        #         trapi_response=response["response"]
                        #     )
                        #     agent_report["trapi_validation"] = results[
                        #         next(iter(results.keys()))
                        #     ][agent]["status"]
                        #     if agent_report["trapi_validation"] == "FAILED":
                        #         agent_report["status"] = "FAILED"
                        #         agent_report["message"] = "TRAPI Validation Error"
                        #         continue
                        # except Exception as e:
                        #     logger.warning(f"Failed to run TRAPI validation with {e}")
                        #     agent_report["trapi_validation"] = "ERROR"
                        try:
                            if (
                                response["response"]["message"].get("results") is None
                                or len(response["response"]["message"]["results"]) == 0
                            ):
                                agent_report["status"] = "DONE"
                                agent_report["message"] = "No results"
                                continue
                            if isinstance(test, PathfinderTestCase):
                                pathfinder_pass_fail_analysis(
                                    report["result"],
                                    agent,
                                    response["response"]["message"],
                                    [
                                        [
                                            normalized_curies[path_node_id]
                                            for path_node_id in path_node.ids
                                        ]
                                        for path_node in asset.path_nodes
                                    ],
                                    asset.minimum_required_path_nodes,
                                )
                            else:
                                run_acceptance_pass_fail_analysis(
                                    report["result"],
                                    agent,
                                    response["response"]["message"]["results"],
                                    normalized_curies[asset.output_id],
                                    asset.expected_output,
                                )
                        except Exception as e:
                            logger.error(
                                f"Failed to run acceptance test analysis on {agent}: {e}"
                            )
                            agent_report["status"] = "FAILED"
                            agent_report["message"] = "Test Error"

                    status = "PASSED"
                    # grab only ars result if it exists, otherwise default to failed
                    ars_status = report["result"].get("ars", {}).get("status")
                    status = ars_status if ars_status is not None else "SKIPPED"
                    collector.acceptance_report[status] += 1

                    collector.collect_acceptance_result(
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
                            reporter.upload_labels(test_id, labels)
                        except Exception as e:
                            logger.warning(f"[{test.id}] failed to upload labels: {e}")
                    try:
                        reporter.upload_log(test_id, json.dumps(report, indent=4))
                    except Exception:
                        logger.error(f"[{test.id}] failed to upload logs.")
                else:
                    status = "SKIPPED"

                try:
                    reporter.finish_test(test_id, status)
                except Exception:
                    logger.error(f"[{test.id}] failed to upload finished status.")
        elif test.test_case_objective == "QuantitativeTest":
            # create test in Test Dashboard
            test_ids = []
            for asset in test.test_assets:
                test_id = ""
                try:
                    test_id = reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception as e:
                    logger.error(f"Failed to create test: {test.id}", e)
                    continue

                if isinstance(test, PerformanceTestCase):
                    test_query = generate_query(asset)
                    if test_query is not None:
                        message = json.dumps(test_query, indent=2)
                    else:
                        message = "Unable to retrieve response for test asset."
                    reporter.upload_log(
                        test_id,
                        message,
                    )
                    host = query_runner.registry[env_map[test.test_env]][
                        test.components[0]
                    ][0]["url"]
                    results = run_performance_test(test, test_query, host)

                    collector.collect_performance_result(
                        test,
                        asset,
                        f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}",
                        host,
                        results,
                    )
            # try:
            #     test_inputs = [
            #         assets.id,
            #         # TODO: update this. Assumes is going to be ARS
            #         test.components[0],
            #     ]
            #     await reporter.upload_log(
            #         test_id,
            #         f"Calling Benchmark Test Runner with: {json.dumps(test_inputs, indent=4)}",
            #     )
            #     benchmark_results, screenshots = await run_benchmarks(*test_inputs)
            #     await reporter.upload_log(test_id, ("\n").join(benchmark_results))
            #     # ex:
            #     # {
            #     #   "aragorn": {
            #     #     "precision": screenshot
            #     #   }
            #     # }
            #     for target_screenshots in screenshots.values():
            #         for screenshot in target_screenshots.values():
            #             await reporter.upload_screenshot(test_id, screenshot)
            #     await reporter.finish_test(test_id, "PASSED")
            #     collector.full_report["PASSED"] += 1
            # except Exception as e:
            #     logger.error(f"Benchmarks failed with {e}: {traceback.format_exc()}")
            #     collector.full_report["FAILED"] += 1
            #     try:
            #         await reporter.upload_log(test_id, traceback.format_exc())
            #     except Exception:
            #         logger.error(
            #             f"Failed to upload fail logs for test {test_id}: {traceback.format_exc()}"
            #         )
            #     await reporter.finish_test(test_id, "FAILED")
        else:
            try:
                test_id = reporter.create_test(test, test.test_assets[0])
                logger.error(f"Unsupported test type: {test.id}")
                reporter.upload_log(
                    test_id, f"Unsupported test type in test: {test.id}"
                )
                status = "FAILED"
                reporter.finish_test(test_id, status)
            except Exception:
                logger.error(f"Failed to report errors with: {test.id}")

        # delete this big object to help out the garbage collector
        del query_responses
