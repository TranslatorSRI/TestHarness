"""Run tests through the Test Runners."""

import logging
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
from test_harness.pathfinder_test_runner import (
    expected_path_nodes,
    pathfinder_ndcg,
    pathfinder_pass_fail_analysis,
)
from test_harness.performance_test_runner import (
    describe_run,
    helmsdeep_target,
    resolve_profile,
    run_performance_test,
)
from test_harness.result_collector import ResultCollector
from test_harness.runner.query_runner import QueryRunner, env_map
from test_harness.trapi import DEFAULT_TRAPI_VERSION
from test_harness.utils import (
    AgentReport,
    AgentStatus,
    PathfinderReport,
    TestReport,
    hash_test_asset,
    normalized_output_ids,
    skipped_because,
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


def asset_url(collector: ResultCollector, test, asset) -> str:
    """Where to read an asset's result: its page in the radiator, or nothing
    when the run isn't going to one."""
    radiator = collector.radiator
    return (radiator.asset_url(test.id, asset.id) if radiator else None) or ""


def run_tests(
    tests: Dict[str, Union[TestCase, PathfinderTestCase]],
    collector: ResultCollector,
    logger: logging.Logger = logging.getLogger(__name__),
    args: Dict[str, Any] = {},
) -> None:
    """Send tests through the Test Runners."""
    logger.info(f"Running {len(tests)} queries...")
    target_url = args.get("target_url")
    target = args.get("target")
    trapi_version = args.get("trapi_version") or DEFAULT_TRAPI_VERSION
    query_runner = QueryRunner(
        logger,
        args["env"],
        target_url=target_url,
        target=target,
        trapi_version=trapi_version,
        query_parameters=args.get("query_parameters"),
    )
    logger.info("Runner is getting service registry")
    query_runner.retrieve_registry(trapi_version=trapi_version)
    # The overall test status is normally driven by the ARS. When the target
    # service specified in the tests is overridden (eg to run against a
    # locally running ARA), it is driven by the override target instead.
    status_agent = "ars"
    if target_url is not None and target is not None:
        status_agent = target.split("infores:")[-1]
    include_skipped = bool(args.get("include_skipped"))
    # loop over all tests
    for test in tqdm(list(tests.values())):
        # check if acceptance test
        if not test.test_assets or not test.test_case_objective:
            missing = "test_assets" if not test.test_assets else "test_case_objective"
            logger.warning(
                f"[{test.id}] skipped, and not sent to the Information Radiator: "
                f"the test case has no {missing}"
            )
            continue

        query_responses = {}
        if test.test_case_objective == "AcceptanceTest":
            # Assets the suite skips aren't queried, but are still reported,
            # as SKIPPED with the reason.
            skips = {
                asset.id: skipped_because(test, asset, include_skipped)
                for asset in test.test_assets
            }
            to_run = [asset for asset in test.test_assets if not skips[asset.id]]
            if to_run:
                query_responses, normalized_curies = query_runner.run_queries(
                    test
                    if len(to_run) == len(test.test_assets)
                    else test.model_copy(update={"test_assets": to_run})
                )
            else:
                logger.info(f"[{test.id}] every asset is skipped; not querying")
                normalized_curies = {}
            for asset in test.test_assets:
                # throw out any assets with unsupported expected outputs, i.e. OverlyGeneric
                if asset.expected_output not in collector.query_types:
                    logger.warning(
                        f"[{test.id}] asset {asset.id} skipped, and not sent to "
                        "the Information Radiator: its expected output "
                        f"{asset.expected_output!r} isn't one the harness tests "
                        f"({', '.join(collector.query_types)})"
                    )
                    continue
                skip_message = skips[asset.id]
                test_asset_hash = hash_test_asset(asset)
                test_query = (
                    None if skip_message else query_responses.get(test_asset_hash)
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
                                ",".join(curies)
                                for curies in expected_path_nodes(
                                    asset, normalized_curies
                                )
                            ),
                        }
                    for agent, response in test_query["responses"].items():
                        report.result[agent] = (
                            PathfinderReport
                            if isinstance(asset, PathfinderTestAsset)
                            else AgentReport
                        )(
                            status=AgentStatus.SKIPPED,
                            message=None,
                            actual_output=None,
                        )
                        agent_report = report.result[agent]
                        record_response_meta(agent_report, response)
                        try:
                            if response.get("submission_failed"):
                                # The query never made it into the component
                                # (eg the ARS returned a 502 on submit). That's
                                # an error on the component, not a test failure.
                                agent_report.status = AgentStatus.ERROR
                                agent_report.message = f"Query submission failed with status code: {response['status_code']}"
                                continue
                            elif response["status_code"] > 299:
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
                                if isinstance(asset, PathfinderTestAsset):
                                    # no paths, so none of them are relevant
                                    agent_report.ndcg = pathfinder_ndcg(
                                        response["response"]["message"],
                                        expected_path_nodes(asset, normalized_curies),
                                        asset.minimum_required_path_nodes,
                                    )
                                continue
                            if isinstance(test, PathfinderTestCase) and isinstance(
                                asset, PathfinderTestAsset
                            ):
                                path_nodes = expected_path_nodes(
                                    asset, normalized_curies
                                )
                                pathfinder_pass_fail_analysis(
                                    report.result,
                                    agent,
                                    response["response"]["message"],
                                    path_nodes,
                                    asset.minimum_required_path_nodes,
                                )
                                # NDCG is extra to pass/fail: a response it
                                # can't score mustn't fail the test.
                                try:
                                    agent_report.ndcg = pathfinder_ndcg(
                                        response["response"]["message"],
                                        path_nodes,
                                        asset.minimum_required_path_nodes,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        f"[{test.id}] couldn't score the NDCG of "
                                        f"{agent}'s paths for {asset.id}: {e!r}"
                                    )
                            elif isinstance(asset, TestAsset):
                                run_acceptance_pass_fail_analysis(
                                    report.result,
                                    agent,
                                    response["response"]["message"]["results"],
                                    normalized_output_ids(asset, normalized_curies),
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
                    # all to SKIPPED so the radiator, CSV, and JSON stats
                    # stay consistent with the skipped test-level status.
                    force_skipped = status == AgentStatus.SKIPPED
                    collector.collect_acceptance_result(
                        test,
                        asset,
                        report,
                        test_query["pks"].get("parent_pk"),
                        asset_url(collector, test, asset),
                        force_skipped=force_skipped,
                        status=status,
                    )

                else:
                    # Skipped by the suite, or no query response for this asset
                    # (eg query generation failed). Record it as skipped across
                    # every agent so it still appears in the per-agent stats,
                    # CSV, and radiator as SKIPPED instead of being
                    # dropped entirely.
                    if skip_message:
                        logger.info(f"[{test.id}] asset {asset.id}: {skip_message}")
                    else:
                        logger.warning(
                            f"[{test.id}] asset {asset.id}: no query response (its "
                            "query couldn't be built or sent; see the errors "
                            "above), so it's recorded as SKIPPED"
                        )
                    status = AgentStatus.SKIPPED
                    collector.collect_acceptance_result(
                        test,
                        asset,
                        TestReport(pks={}, result={}, test_details=None),
                        None,
                        asset_url(collector, test, asset),
                        force_skipped=True,
                        status=status,
                        message=skip_message,
                    )
                collector.acceptance_report[status.value] += 1
        elif test.test_case_objective == "QuantitativeTest":
            for asset in test.test_assets:
                if not isinstance(test, PerformanceTestCase):
                    logger.warning(
                        f"[{test.id}] asset {asset.id} skipped, and not sent to "
                        "the Information Radiator: only performance tests are "
                        "run as QuantitativeTests"
                    )
                if isinstance(test, PerformanceTestCase):
                    if target_url is not None:
                        host = query_runner.target_url
                        perf_target = status_agent
                    else:
                        host = query_runner.registry[env_map[query_runner.env]][
                            test.components[0]
                        ][0]["url"]
                        perf_target = None
                    skip_message = skipped_because(test, asset, include_skipped)
                    if skip_message:
                        # no load test, but the radiator still gets the test,
                        # as SKIPPED with the reason
                        logger.info(
                            f"[{test.id}] {skip_message}; not running HelmsDeep"
                        )
                        collector.record_performance_skip(
                            test, asset, host, skip_message
                        )
                        continue
                    # HelmsDeep sends its own varied corpus, so the asset's
                    # TRAPI query is not what goes under load. Log the run plan
                    # -- layer, ramp, SLO, checkpoints -- which is what a
                    # reader actually needs to interpret the result.
                    try:
                        profile = resolve_profile(
                            test, args.get("performance_profile"), logger
                        )
                        run_type = helmsdeep_target(
                            perf_target or test.components[0], profile
                        )
                        logger.info(
                            f"[{test.id}] "
                            f"{describe_run(test, host, run_type, profile)}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Could not describe the HelmsDeep plan for "
                            f"{test.id}: {e}"
                        )
                    try:
                        results = run_performance_test(
                            test,
                            host,
                            target=perf_target,
                            output_dir=args.get("output_dir") or "test_results",
                            profile=args.get("performance_profile"),
                            logger=logger,
                            trapi_version=trapi_version,
                        )
                        collector.collect_performance_result(
                            test,
                            asset,
                            asset_url(collector, test, asset),
                            host,
                            results,
                        )
                        if results.get("error"):
                            logger.error(
                                f"Performance run for {test.id} did not "
                                f"complete: {results['error']}"
                            )
                    except Exception as e:
                        logger.error(
                            f"[{test.id}] performance test against {host} failed: "
                            f"{type(e).__name__}: {e}"
                        )
                        collector.record_performance_error(test, asset, host, str(e))
        else:
            logger.error(
                f"[{test.id}] not run, and not sent to the Information "
                f"Radiator: unsupported test type {test.test_case_objective!r}"
            )

        # Send this test case's results to the radiator now rather than when a
        # batch fills: each case waits minutes on its queries, so a whole run
        # can be under one batch, and nothing would show until the end.
        collector.flush()

        # delete this big object to help out the garbage collector
        del query_responses
