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
from standards_validation_test import run_standards_validation_tests
from one_hop_test import run_one_hop_tests
# from benchmarks_runner import run_benchmarks

from .reporter import Reporter
from .slacker import Slacker


def get_tag(result):
    """Given a result, get the correct tag for the label."""
    tag = result.get("status", "FAILED")
    if tag != "PASSED":
        message = result.get("message")
        if message:
            tag = message
    return tag


def get_test_case_results(test_case_id: str, test_run_results: Dict) -> Dict[str, Dict]:
    """
    Reformats the output of 'graph-validation-tests' TestRunners
    into a TestHarness summary of component/test status.

    :param test_case_id: str, local identifier of the test case of interest.
    :param test_run_results: Dict, raw test run results from the graph-validation-tests TestRunners.
    :return: Dict[str, Dict], where the top-level keys are "pks" and "results" and
             associated value dictionaries are the pks of the various component runs, and
             the asset_id-test_name indexed status of each component test result
    """
    # Raw test result is something like this:
    #
    # {
    #     # TODO: the value of the component pk ought to be a test run identifier of some sort
    #     'pks': ['arax': 'molepro'],
    #     'results': [
    #         [
    #             {
    #                 'Asset_1-by_subject': {
    #                     'molepro':
    #                         (   # TODO: the returned data is a 2-tuple, but could easily be returned as a dictionary
    #                             #       with { "status": 'PASSED', 'messages': {reasoner-validator messages...}
    #                             <TestCaseResultEnum.PASSED: 'PASSED'>,
    #                             {}
    #                         )
    #                 },
    #                 'Asset_1-inverse_by_new_subject': {
    #                     'molepro': (
    #                         <TestCaseResultEnum.FAILED: 'FAILED'>,  # test case 'status' outcome
    #                         {
    #                             'error': {
    #                                 'error.trapi.response.knowledge_graph.missing_expected_edge': {
    #                                     'global': {
    #                                         'Asset_1|(PUBCHEM.COMPOUND:4091#biolink:SmallMolecule)-[biolink:affects]->(NCBIGene:2475#biolink:Gene)': None
    #                                     }
    #                                 }
    #                             }
    #                         }
    #                     )
    #                 },
    #                 'Asset_1-by_object': {
    #                     # etc... 'molepro': (<TestCaseResultEnum.FAILED: 'FAILED'>, {'error': {'error.trapi.response.knowledge_graph.missing_expected_edge': {'global': {'Asset_1|(PUBCHEM.COMPOUND:4091#biolink:SmallMolecule)-[biolink:affects]->(NCBIGene:2475#biolink:Gene)': None}}}})
    #                 },
    #                 # etc...
    #         ]
    #     ]
    # ]
    #
    # Sample return value:
    # {
    #     "pks": {
    #         "aragorn": "14953570-7451-4d1b-a817-fc9e7879b477",
    #         "arax": "8c88ead6-6cbf-4c9a-9570-ca76392ddb7a",
    #         "molepro": "bd084e27-2a0e-4df4-843c-417bfac6f8c7",
    #         "bte": "d28a4146-9486-4e98-973d-8cdd33270595",
    #         "improving": "d8d3c905-ec07-491f-a078-7ef0f489a409"
    #     },
    #     "results": {
    #         # TODO: the 'Asset_1-by_subject' is effectively the 'test_id' generated
    #         #       from TestCase 'by_subject' and test_asset 'Asset_1'?
    #         "Asset_1-by_subject": {
    #             "aragorn": "PASSED",
    #             "arax": "PASSED",
    #             "molepro": "FAILED",
    #         },
    #         "Asset_1-inverse_by_new_subject": {
    #             "aragorn": "FAILED",
    #             "arax": "PASSED",
    #             "molepro": "PASSED",
    #         },
    #         # etc...
    #      }
    # }
    if test_case_id not in test_run_results["results"]:
        return dict()
    return test_run_results["results"][test_case_id]


# TODO: this method should be moved and imported from the "graph-validation-tests" package/module.
def get_compliance_tests(test: TestCase) -> List[str]:
    # TODO: compliance 'test' names - e.g. 'by_subject', etc. - for 'graph-validation-tests'
    #       are dynamically internally specified and constructed within the respective test runners.
    #       In fact, each 'test' TestAsset is one-to-many mapped onto such TestCases.
    #       So how can this test_id be generated in advance of running the test runner?
    #       Two options: 1) expect a list of the test case identifiers in 'test_runner_settings' or
    #                    2) retrieve a list of test case names from the test runner module
    #       In both cases, these would be the values used to 'create' test instances in the IR.
    #       Since this represents more than one test_id, we need to track them accordingly.
    if test.test_runner_settings:
        return test.test_runner_settings
    if test.test_case_objective == "StandardsValidationTest":
        return ["by_subject", "by_object"]
    elif test.test_case_objective == "OneHopTest":
        return [
            "by_subject",
            "inverse_by_new_subject",
            "by_object",
            "raise_subject_entity",
            "raise_object_entity",
            "raise_object_by_subject",
            "raise_predicate_by_subject"
        ]
    else:
        raise NotImplementedError(f"Unexpected test_case_objective: {test.case_objective}?")


async def run_tests(
    reporter: Reporter,
    slacker: Slacker,
    tests: Dict[str, TestCase],
    trapi_version: Optional[str] = None,
    biolink_version: Optional[str] = None,
    logger: logging.Logger = logging.getLogger(__name__),
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
            f"Running {len(tests)} tests...\n<{reporter.base_path}/test-runs/{reporter.test_run_id}|View in the Information Radiator>"
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
            # TODO: move qualifiers to TestCase as all the assets should have the same one
            for qualifier in assets[0].qualifiers:
                if qualifier.parameter == "biolink_object_aspect_qualifier":
                    biolink_object_aspect_qualifier = qualifier.value
                elif qualifier.parameter == "biolink_object_direction_qualifier":
                    biolink_object_direction_qualifier = qualifier.value
            # TODO: move input category up as well
            input_category = assets[0].input_category
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
                biolink_object_aspect_qualifier,
                biolink_object_direction_qualifier,
                input_category,
                test.test_case_input_id,
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
                ars_pk = ars_result["pks"].get("parent_pk")
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
                    logger.error(f"[{test.id}] failed to parse test results: {ars_result}")
                    try:
                        await reporter.upload_log(
                            test_id, f"Failed to parse results: {json.dumps(ars_result)}"
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
                    test_case_results = get_test_case_results(test_case_id, test_run_results)
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
