from typing import Dict


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
