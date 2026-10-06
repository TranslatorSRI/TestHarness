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
from test_harness.performance_test_runner import (
    describe_run,
    helmsdeep_target,
    resolve_profile,
    run_performance_test,
)
from test_harness.reporter import LocalReporter, Reporter
from test_harness.result_collector import ResultCollector
from test_harness.runner.query_runner import QueryRunner, env_map
from test_harness.utils import (
    AgentReport,
    AgentStatus,
    TestReport,
    hash_test_asset,
)


def record_response_meta(agent_report: AgentReport, response: Any) -> None:
    """Record what the response itself says, before any analysis of it.

    ``n_results`` is only recorded for a successful response: the query runner
    stands in an empty result list when an agent errors or times out, and that
    is not an agent answering with zero results.
    """
    if not isinstance(response, dict):
        return
    status_code = response.get("status_code")
    # after a failed ARS poll this is the ARS's status string, eg "Running"
    agent_report.http_status = status_code if isinstance(status_code, int) else None
    agent_report.response_time_s = response.get("elapsed_s")
    body = response.get("response")
    message = body.get("message") if isinstance(body, dict) else None
    results = message.get("results") if isinstance(message, dict) else None
    status_code = agent_report.http_status
    if isinstance(results, list) and status_code is not None and status_code < 300:
        agent_report.n_results = len(results)


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
                asset_reporter = reporter
                try:
                    test_id = reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception:
                    # Zebrunner failing for this asset mustn't drop it: run
                    # and collect it anyway, just without reporting it there.
                    logger.error(f"Failed to create test: {test.id}")
                    asset_reporter = LocalReporter(logger=logger)

                test_asset_hash = hash_test_asset(asset)
                test_query = query_responses.get(test_asset_hash)
                if test_query is not None:
                    message = json.dumps(test_query["query"], indent=4)
                else:
                    message = "Unable to retrieve response for test asset."
                asset_reporter.upload_log(
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
                        record_response_meta(agent_report, response)
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
                        status=status,
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
                        asset_reporter.upload_labels(test_id, labels)
                    except Exception as e:
                        logger.warning(f"[{test.id}] failed to upload labels: {e}")
                    logger.info(f"Full report: {json.dumps(asdict(report), indent=4)}")
                    asset_reporter.upload_log(
                        test_id, json.dumps(asdict(report), indent=4)
                    )
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
                        status=status,
                    )
                    try:
                        asset_reporter.upload_labels(
                            test_id,
                            [
                                {"key": ara, "value": AgentStatus.SKIPPED.value}
                                for ara in collector.agents
                            ],
                        )
                    except Exception as e:
                        logger.warning(f"[{test.id}] failed to upload labels: {e}")

                asset_reporter.finish_test(test_id, status.value)
                collector.acceptance_report[status.value] += 1
        elif test.test_case_objective == "QuantitativeTest":
            # create test in Test Dashboard
            test_ids = []
            for asset in test.test_assets:
                test_id = ""
                asset_reporter = reporter
                try:
                    test_id = reporter.create_test(test, asset)
                    test_ids.append(test_id)
                except Exception as e:
                    logger.error(f"Failed to create test: {test.id}: {e}")
                    asset_reporter = LocalReporter(logger=logger)

                if isinstance(test, PerformanceTestCase):
                    if target_url is not None:
                        host = query_runner.target_url
                        perf_target = status_agent
                    else:
                        host = query_runner.registry[env_map[test.test_env]][
                            test.components[0]
                        ][0]["url"]
                        perf_target = None
                    # HelmsDeep sends its own varied corpus, so the asset's
                    # TRAPI query is not what goes under load. Log the run plan
                    # -- layer, ramp, SLO, checkpoints -- which is what the
                    # radiator's reader actually needs to interpret the result.
                    try:
                        profile = resolve_profile(
                            test, args.get("performance_profile"), logger
                        )
                        run_type = helmsdeep_target(
                            perf_target or test.components[0], profile
                        )
                        asset_reporter.upload_log(
                            test_id,
                            describe_run(test, host, run_type, profile),
                        )
                    except Exception as e:
                        logger.warning(
                            f"Could not describe the HelmsDeep plan for "
                            f"{test.id}: {e}"
                        )
                    # Give the performance test a terminal status in the
                    # Information Radiator. Without this the test is created
                    # but never finished, so it shows up as perpetually
                    # incomplete in the dashboard.
                    status = AgentStatus.PASSED
                    try:
                        results = run_performance_test(
                            test,
                            host,
                            target=perf_target,
                            output_dir=args.get("output_dir") or "test_results",
                            profile=args.get("performance_profile"),
                            logger=logger,
                        )
                        collector.collect_performance_result(
                            test,
                            asset,
                            f"{reporter.base_path}/test-runs/{reporter.test_run_id}/tests/{test_id}",
                            host,
                            results,
                        )
                        # A run that never produced a summary failed outright.
                        # One that produced a summary with a missed checkpoint
                        # is a real, measured failure -- both belong in the
                        # radiator as FAILED, so the dashboard agrees with what
                        # Slack says.
                        summary = results.get("summary") or {}
                        if results.get("error"):
                            logger.error(
                                f"Performance run for {test.id} did not "
                                f"complete: {results['error']}"
                            )
                            status = AgentStatus.FAILED
                        elif summary.get("checkpoints_passed") is False:
                            status = AgentStatus.FAILED
                    except Exception as e:
                        logger.error(
                            f"Failed to run performance test for {test.id}: {e}"
                        )
                        status = AgentStatus.FAILED
                        collector.record_performance_error(test, asset, host, str(e))
                    asset_reporter.finish_test(test_id, status.value)
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
