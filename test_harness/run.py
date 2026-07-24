"""Run tests through the Test Runners."""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, Union

from tqdm import tqdm

# from standards_validation_test_runner import StandardsValidationTest
# from benchmarks_runner import run_benchmarks
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    PerformanceTestCase,
    TestAsset,
    TestCase,
)

from test_harness.acceptance_test_runner import run_acceptance_pass_fail_analysis
from test_harness.pathfinder_test_runner import pathfinder_pass_fail_analysis
from test_harness.performance_test_runner import run_performance_test
from test_harness.reporter import Reporter
from test_harness.result_collector import ResultCollector
from test_harness.runner.generate_query import generate_query
from test_harness.runner.query_runner import QueryRunner, env_map
from test_harness.utils import (
    AgentReport,
    AgentStatus,
    TestReport,
    hash_test_asset,
)


def run_tests(
    tests: Dict[str, Union[TestCase, PathfinderTestCase]],
    reporter: Reporter,
    collector: ResultCollector,
    logger: logging.Logger = logging.getLogger(__name__),
    args: Dict[str, Any] = {},
) -> None:
    """Send tests through the Test Runners."""
    logger.info(f"Running {len(tests)} queries...")
    target_url = args.get("target_url")
    target = args.get("target")
    query_runner = QueryRunner(logger, target_url=target_url, target=target)
    logger.info("Runner is getting service registry")
    query_runner.retrieve_registry(trapi_version=args["trapi_version"])
    # The overall test status is normally driven by the ARS. When the target
    # service specified in the tests is overridden (eg to run against a
    # locally running ARA), it is driven by the override target instead.
    status_agent = "ars"
    if target_url is not None and target is not None:
        status_agent = target.split("infores:")[-1]
    # loop over all tests
    for test in tqdm(list(tests.values())):
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
                except Exception:
                    logger.error(f"Failed to create test: {test.id}")
                    continue

                test_asset_hash = hash_test_asset(asset)
                test_query = query_responses.get(test_asset_hash)
                if test_query is not None:
                    message = json.dumps(test_query["query"], indent=4)
                else:
                    message = "Unable to retrieve response for test asset."
                reporter.upload_log(
                    test_id,
                    message,
                )

                if test_query is not None:
                    report = TestReport(
                        pks=test_query["pks"],
                        result={},
                        test_details=None,
                    )
                    if isinstance(test, PathfinderTestCase) and isinstance(
                        asset, PathfinderTestAsset
                    ):
                        report.test_details = {
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
                        report.result[agent] = AgentReport(
                            status=AgentStatus.SKIPPED,
                            message=None,
                            actual_output=None,
                        )
                        agent_report = report.result[agent]
                        try:
                            if response["status_code"] > 299:
                                agent_report.status = AgentStatus.FAILED
                                if str(response["status_code"]) == "598":
                                    agent_report.message = "Timed out"
                                else:
                                    agent_report.message = (
                                        f"Status code: {response['status_code']}"
                                    )
                                continue
                            elif (
                                "response" not in response
                                or "message" not in response["response"]
                            ):
                                agent_report.status = AgentStatus.FAILED
                                agent_report.message = "Test Error"
                                continue
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse basic response fields from {agent}: {e}"
                            )
                            agent_report.status = AgentStatus.FAILED
                            agent_report.message = "Test Error"
                        try:
                            if (
                                response["response"]["message"].get("results") is None
                                or len(response["response"]["message"]["results"]) == 0
                            ):
                                agent_report.status = AgentStatus.NO_RESULTS
                                agent_report.message = "No results"
                                continue
                            if isinstance(test, PathfinderTestCase) and isinstance(
                                asset, PathfinderTestAsset
                            ):
                                pathfinder_pass_fail_analysis(
                                    report.result,
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
                            elif isinstance(asset, TestAsset):
                                run_acceptance_pass_fail_analysis(
                                    report.result,
                                    agent,
                                    response["response"]["message"]["results"],
                                    (
                                        normalized_curies.get(asset.output_id, "")
                                        if asset.output_id is not None
                                        else ""
                                    ),
                                    asset.expected_output,
                                )
                        except Exception as e:
                            logger.error(
                                f"Failed to run acceptance test analysis on {agent}: {e}"
                            )
                            agent_report.status = AgentStatus.FAILED
                            agent_report.message = "Test Error"

                    # The overall test status is driven by the status agent
                    # (the ARS, or the override target when one is given). If
                    # it didn't produce a result, the whole test is considered
                    # skipped.
                    if status_agent not in report.result:
                        status = AgentStatus.SKIPPED
                    else:
                        status = report.result[status_agent].status

                    # When the test is skipped, every agent is skipped too: the
                    # query never really ran, so the incidental per-ARA
                    # error/no-result statuses would be misleading. Force them
                    # all to SKIPPED so the radiator labels, CSV, and JSON stats
                    # stay consistent with the skipped test-level status.
                    force_skipped = status == AgentStatus.SKIPPED

                    collector.collect_acceptance_result(
                        test,
                        asset,
                        report,
                        test_query["pks"].get("parent_pk"),
                        f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}",
                        force_skipped=force_skipped,
                    )

                    try:
                        if force_skipped:
                            labels = [
                                {
                                    "key": ara,
                                    "value": AgentStatus.SKIPPED.value,
                                }
                                for ara in collector.agents
                            ]
                        else:
                            labels = [
                                {
                                    "key": ara,
                                    "value": report.result[ara].status.value,
                                }
                                for ara in collector.agents
                                if ara in report.result
                            ]
                        reporter.upload_labels(test_id, labels)
                    except Exception as e:
                        logger.warning(f"[{test.id}] failed to upload labels: {e}")
                    logger.info(f"Full report: {json.dumps(asdict(report), indent=4)}")
                    reporter.upload_log(test_id, json.dumps(asdict(report), indent=4))
                else:
                    # No query response for this asset (eg query generation
                    # failed). Record it as skipped across every agent so it
                    # still appears in the per-agent stats, CSV, and radiator
                    # labels as SKIPPED instead of being dropped entirely.
                    status = AgentStatus.SKIPPED
                    collector.collect_acceptance_result(
                        test,
                        asset,
                        TestReport(pks={}, result={}, test_details=None),
                        None,
                        f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}",
                        force_skipped=True,
                    )
                    try:
                        reporter.upload_labels(
                            test_id,
                            [
                                {"key": ara, "value": AgentStatus.SKIPPED.value}
                                for ara in collector.agents
                            ],
                        )
                    except Exception as e:
                        logger.warning(f"[{test.id}] failed to upload labels: {e}")

                reporter.finish_test(test_id, status.value)
                collector.acceptance_report[status.value] += 1
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
                    # Give the performance test a terminal status in the
                    # Information Radiator. Without this the test is created
                    # but never finished, so it shows up as perpetually
                    # incomplete in the dashboard.
                    status = AgentStatus.PASSED
                    if test_query is None:
                        logger.error(
                            f"Unable to generate performance query for asset: {asset.id}"
                        )
                        status = AgentStatus.FAILED
                    else:
                        if target_url is not None:
                            host = query_runner.target_url
                            perf_target = status_agent
                        else:
                            host = query_runner.registry[env_map[test.test_env]][
                                test.components[0]
                            ][0]["url"]
                            perf_target = None
                        try:
                            results = run_performance_test(
                                test, test_query, host, target=perf_target
                            )
                            collector.collect_performance_result(
                                test,
                                asset,
                                f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}",
                                host,
                                results,
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to run performance test for {test.id}: {e}"
                            )
                            status = AgentStatus.FAILED
                    reporter.finish_test(test_id, status.value)
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
