"""Run tests through the Test Runners."""
from typing import Optional, List, Dict
from collections import defaultdict
import httpx
import json
import logging
import time
from tqdm import tqdm
import traceback

from translator_testing_model.datamodel.pydanticmodel import (
    TestAsset,
    TestCase,
    TestEnvEnum,
    ComponentEnum
)

from ARS_Test_Runner.semantic_test import run_semantic_test as run_ars_test
# from benchmarks_runner import run_benchmarks

from graph_validation_tests.utils.unit_test_templates import get_compliance_tests
from standards_validation_test_runner import run_standards_validation_tests
from one_hop_test_runner import run_one_hop_tests

# from benchmarks_runner import run_benchmarks

from .reporter import Reporter
from .slacker import Slacker

from .utils import normalize_curies, get_tag, get_graph_validation_test_case_results


async def run_tests(
    reporter: Reporter,
    slacker: Slacker,
    tests: Dict[str, TestCase],
    trapi_version: Optional[str] = None,
    biolink_version: Optional[str] = None,
    logger: logging.Logger = logging.getLogger(__name__),
    suite_name: str = "automated tests",
) -> Dict:
    """Send tests through the Test Runners.
    """
    start_time = time.time()
    logger.info(f"Running {len(tests)} tests...")
    full_report = {
        "PASSED": 0,
        "FAILED": 0,
        "SKIPPED": 0,
    }
    environment: Optional[TestEnvEnum] = None
    await slacker.post_notification(
        messages=[
            f"Running {suite_name} ({sum([len(test.test_assets) for test in tests.values()])} tests)...\n<{reporter.base_path}/test-runs/{reporter.test_run_id}|View in the Information Radiator>"
        ]
    )
    # loop over all tests
    for test in tqdm(tests.values()):
        status = "PASSED"
        environment: TestEnvEnum = test.test_env

        components: Optional[List[ComponentEnum]] = test.components

        if not test.test_assets or not test.test_case_objective:
            logger.warning(f"Test has missing required fields: {test.id}")
            continue

        # check if acceptance test
        if test.test_case_objective == "AcceptanceTest":
            assets: List[TestAsset] = test.test_assets
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
            asset: TestAsset
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
                environment,
                test.test_case_predicate_name,
                test.test_runner_settings,
                expected_outputs,
                biolink_object_aspect_qualifier,
                biolink_object_direction_qualifier,
                input_category,
                input_curie,
                output_ids,
            ]
            ars_url: str
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
                        test_case_results = {
                            "pks": ars_result.get("pks", {}),
                            "result": results[index],
                        }
                    elif isinstance(results, dict):
                        # make sure it has a single error message
                        assert "error" in results
                        test_case_results = {
                            "pks": ars_result.get("pks", {}),
                            "result": results,
                        }
                    else:
                        # got something completely unexpected from the ARS Test Runner
                        raise Exception()
                    # grab only ars result if it exists, otherwise default to failed
                    if test_case_results["result"].get("error") is not None:
                        status = "SKIPPED"
                    else:
                        status = test_case_results["result"].get("ars", {}).get("status", "FAILED")
                    full_report[status] += 1
                    if not err_msg and status != "SKIPPED":
                        # only upload ara labels if the test ran successfully
                        try:
                            labels = [
                                {
                                    "key": ara,
                                    "value": get_tag(result),
                                }
                                for ara, result in test_case_results["result"].items()
                            ]
                            await reporter.upload_labels(test_id, labels)
                        except Exception as e:
                            logger.warning(f"[{test.id}] failed to upload labels: {e}")
                    try:
                        await reporter.upload_log(
                            test_id, json.dumps(test_case_results, indent=4)
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

        elif test.test_case_objective in ("StandardsValidationTest", "OneHopTest"):
            # One Hop Tests and TRAPI/Biolink ("standards") validation test have comparable test inputs and
            # configuration, except for the TestRunner that is executed, hence, are aggregated in this section.

            # 1. Standards Validation TestRunner uses the "reasoner-validator" package to validate
            # TRAPI and Biolink Model compliance of inputs and outputs templated TRAPI queries.

            # 2. One Hop Tests seem a bit different from other types of tests. Generally, a single OneHopTest TestAsset
            # is single S-P-O triplet with categories, used internally to generate a half dozen distinct TestCases and
            # a single KP or ARA TRAPI service is called several times, once for each generated TestCase.
            #
            # There is no sense of "ExpectedOutput" in the tests rather, test pass, fail or skip status is an intrinsic
            # outcome pertaining to the recovery of the input test asset values in the output of the various TestCases.
            # A list of such TestAssets run against a given KP or ARA target service, could be deemed a "TestSuite".
            # But a set of such TestSuites could be run in batch within a given TestSession. It is somewhat hard
            # to align with this framework to the new Translator Test Harness, or at least, not as efficient to run.
            #
            # To make this work, we will do some violence to the testing model and wrap each input S-P-O triple as a
            # single TestCase, extract a single associated TestAsset, which we'll feed in with the value of the
            # TestCase 'components' field value, which will be taken as the 'infores' of the ARA or KP to be tested.
            #
            # Internally, we'll generate and run TRAPI queries of the actual TestCase instances against the 'infores'
            # specified resources, then return the results, suitably indexed.  Alternately, if the specified target
            # is the 'ars', then the returned results will be indexed by 'pks'(?)

            # As indicated above, we only expect a single TestAsset
            asset = test.test_assets[0]

            # Remapping fields semantically onto
            # StandardsValidationTest/OneHopTest inputs
            test_inputs = {
                # One test edge (asset)
                "test_asset_id": asset.id,
                "subject_id": asset.input_id,
                "subject_category": asset.input_category,
                "predicate_id": asset.predicate_id,
                "object_id": asset.output_id,
                "object_category": asset.output_category,

                "environment": environment,
                "components": components,
                "trapi_version": trapi_version,
                "biolink_version": biolink_version,
                "runner_settings": asset.test_runner_settings,
                # TODO: in principle, additional (optional) keyword arguments could be given in
                #       the test_inputs as a means to configure the BiolinkValidator class in reasoner-validator
                #       with additional parameters like target_provenance and strict_validation; however,
                #       it is unclear at this moment where and how these can or should be specified.
                # **kwargs
            }
            err_msg = ""

            # create tests in Test Dashboard
            test_cases: Dict = dict()
            for test_case_name in get_compliance_tests(test):
                test_case_id: str
                test_run_id: int
                try:
                    test_case_id, test_run_id = await reporter.create_compliant_test(test_case_name, asset)
                    test_input_json = json.dumps(test_inputs, indent=2)
                    await reporter.upload_log(
                        test_run_id,
                        f"Calling {test.test_case_objective} with: {test_input_json}"
                    )
                    test_cases[test_case_id] = test_run_id
                except Exception as e:
                    err_msg = (f"{test.test_case_objective} '{test_case_name}' test " +
                               f"creation failed with {traceback.format_exc()}")
                    logger.error(f"[{asset.id}] {err_msg}")
            try:
                # we pass the test arguments as named parameters,
                # instead than as a simple argument sequence.
                if test.test_case_objective == "StandardsValidationTest":
                    test_run_results = await run_standards_validation_tests(**test_inputs)
                elif test.test_case_objective == "OneHopTest":
                    test_run_results = await run_one_hop_tests(**test_inputs)
                else:
                    raise NotImplementedError(f"Unexpected test_case_objective: {test.test_case_objective}?")
            except Exception as e:
                err_msg = f"{test.test_case_objective} Test Runner failed with {traceback.format_exc()}"
                logger.error(f"[{asset.id}] {err_msg}")
                test_run_results = {
                    "pks": {},
                    # this will effectively act as a list that we access by index down below
                    "results": defaultdict(lambda: {"error": err_msg}),
                }
            if not err_msg:
                # only upload component labels if the test run ran successfully
                test_case_id: str
                test_run_id: int
                for test_case_id, test_run_id in test_cases.items():
                    test_case_results = get_graph_validation_test_case_results(test_case_id, test_run_results)
                    try:
                        labels: List[Dict[str, str]] = list()
                        for component, result in test_case_results.items():
                            status: str = result["status"] if "status" in result else "PASSED"
                            # TODO: unsure if the status should be counted here (with respect to the test cases?)
                            #       or whether it should be tallied somewhere else
                            full_report[status] += 1
                            labels.append({"key": component, "value": status})
                            await reporter.upload_labels(test_run_id, labels)
                    except Exception as e:
                        logger.warning(f"[{test_run_id}] failed to upload labels: {e}")
                    try:
                        await reporter.upload_log(test_run_id, json.dumps(test_case_results, indent=4))
                    except Exception as e:
                        logger.error(f"[{test_run_id}] failed to upload logs.")
                    try:
                        await reporter.finish_test(test_run_id, status)
                    except Exception as e:
                        logger.error(f"[{test_run_id}] failed to upload finished status.")

        elif test.test_case_objective == "QuantitativeTest":
            assets: TestAsset = test.test_assets[0]
            try:
                test_id = await reporter.create_test(test, assets)
            except Exception:
                logger.error(f"Failed to create test: {test.id}")
                continue
            try:
                test_inputs = [
                    assets.id,
                    # TODO: update this. Assumes is going to be ARS
                    components[0] if components else ComponentEnum("ars"),
                ]
                await reporter.upload_log(
                    test_id,
                    f"Calling Benchmark Test Runner with: {json.dumps(test_inputs, indent=4)}",
                )
                benchmark_results, screenshots = {}, {} # await run_benchmarks(*test_inputs)
                await reporter.upload_log(test_id, "\n".join(benchmark_results))
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
                test_id = await reporter.create_test(test, assets)
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
                test_suite=suite_name,
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
