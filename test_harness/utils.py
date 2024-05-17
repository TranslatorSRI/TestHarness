"""General utilities for the Test Harness."""

import httpx
import logging
from typing import Dict, Union, List

from translator_testing_model.datamodel.pydanticmodel import TestCase

NODE_NORM_URL = {
    "dev": "https://nodenormalization-sri.renci.org/1.4",
    "ci": "https://nodenorm.ci.transltr.io",
    "test": "https://nodenorm.test.transltr.io/1.4",
    "prod": "https://nodenorm.transltr.io/1.4",
}


async def normalize_curies(
    test: TestCase,
    logger: logging.Logger = logging.getLogger(__name__),
) -> Dict[str, Dict[str, Union[Dict[str, str], List[str]]]]:
    """Normalize a list of curies."""
    node_norm = NODE_NORM_URL[test.test_env]
    # collect all curies from test
    curies = [asset.output_id for asset in test.test_assets]
    curies.append(test.test_case_input_id)

    async with httpx.AsyncClient() as client:
        normalized_curies = {}
        try:
            response = await client.post(
                node_norm + "/get_normalized_nodes",
                json={
                    "curies": curies,
                    "conflate": True,
                    "drug_chemical_conflate": True,
                },
            )
            response.raise_for_status()
            response = response.json()
            for curie, attrs in response.items():
                if attrs is None:
                    normalized_curies[curie] = {
                        "id": {
                            "identifier": "Unknown",
                        },
                        "type": [
                            "Unknown",
                        ],
                    }
                else:
                    normalized_curies[curie] = attrs
        except Exception as e:
            logger.error(f"Node norm failed with: {e}")
            logger.error("Using original curies.")
            for curie in curies:
                normalized_curies[curie] = {
                    "id": {
                        "identifier": curie,
                    },
                    # intentionally doesn't have a type,
                    # so we can default to the original
                }
        return normalized_curies


def get_tag(result):
    """Given a result, get the correct tag for the label."""
    tag = result.get("status", "FAILED")
    if tag != "PASSED":
        message = result.get("message")
        if message:
            tag = message
    return tag


def get_graph_validation_test_case_results(test_case_id: str, test_run_results: Dict) -> Dict[str, Dict]:
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
    #                                         'Asset_1|(PUBCHEM...:4091...)-[biolink:affects]->(NCBIGene:2475...)': None
    #                                     }
    #                                 }
    #                             }
    #                         }
    #                     )
    #                 },
    #                 'Asset_1-by_object': {
    #                     #  'molepro': (<TestCaseResultEnum.FAILED: etc...
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
