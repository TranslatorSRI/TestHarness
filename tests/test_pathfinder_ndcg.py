"""Test the NDCG of Pathfinder responses."""

import json
from math import log2
from pathlib import Path

import pytest
from translator_testing_model.datamodel.pydanticmodel import PathfinderTestCase

from test_harness.pathfinder_test_runner import (
    merge_expected_nodes,
    pathfinder_ndcg,
    pathfinder_pass_fail_analysis,
    ranked_paths,
)
from test_harness.run import run_tests
from test_harness.utils import AgentStatus, PathfinderReport, hash_test_asset

from .helpers.logger import setup_logger
from .helpers.mocks import MockReporter, MockResultCollector

logger = setup_logger()

IMATINIB, ASTHMA = "CHEBI:45783", "MONDO:0004979"
KIT, HISTAMINE, MAST_CELL = "NCBIGene:3815", "CHEBI:18295", "CL:0000097"


def path_message(*paths, scores=None):
    """A Pathfinder message with one analysis per path, each a chain of nodes
    from imatinib to asthma. Analyses are scored best first unless ``scores``
    says otherwise."""
    scores = scores or [len(paths) - i for i in range(len(paths))]
    edges, aux_graphs, analyses = {}, {}, []
    for i, (nodes, score) in enumerate(zip(paths, scores)):
        chain = [IMATINIB, *nodes, ASTHMA]
        edge_ids = []
        for j, (subject, object) in enumerate(zip(chain, chain[1:])):
            edge_ids.append(f"e{i}_{j}")
            edges[f"e{i}_{j}"] = {"subject": subject, "object": object}
        aux_graphs[f"aux{i}"] = {"edges": edge_ids}
        analyses.append(
            {
                "resource_id": "infores:ara",
                "score": score,
                "path_bindings": {"p0": {"ids": [f"aux{i}"]}},
            }
        )
    return {
        "results": [{"node_bindings": {}, "analyses": analyses}],
        "auxiliary_graphs": aux_graphs,
        "knowledge_graph": {"nodes": {}, "edges": edges},
    }


def at_rank(rank):
    """NDCG of an asset whose first relevant path is at ``rank``."""
    return 1 / log2(rank + 1)


EXPECTED = [[KIT], [HISTAMINE], [MAST_CELL]]


def test_a_relevant_top_path_is_perfect():
    message = path_message([KIT, MAST_CELL, HISTAMINE])
    for required in (1, 2, 3):
        assert pathfinder_ndcg(message, EXPECTED, required) == pytest.approx(1.0)


def test_minimum_one_scores_the_first_path_with_an_expected_node():
    message = path_message(["NCBIGene:1"], [HISTAMINE], [KIT, MAST_CELL])
    assert pathfinder_ndcg(message, EXPECTED, 1) == pytest.approx(at_rank(2))


def test_paths_with_too_few_expected_nodes_arent_relevant():
    message = path_message(
        [KIT],  # one expected node: not enough for 2
        [KIT, "NCBIGene:1"],  # still one
        [MAST_CELL, HISTAMINE],  # two: the first relevant path
        [KIT, MAST_CELL, HISTAMINE],  # three, but too late to count
    )
    assert pathfinder_ndcg(message, EXPECTED, 2) == pytest.approx(at_rank(3))
    assert pathfinder_ndcg(message, EXPECTED, 3) == pytest.approx(at_rank(4))


def test_nodes_spread_over_paths_dont_add_up():
    """Every expected node is found, but never two on one path."""
    message = path_message([KIT], [MAST_CELL], [HISTAMINE])
    assert pathfinder_ndcg(message, EXPECTED, 2) == 0


def test_ndcg_is_at_most_one():
    """A path with more expected nodes than required (here the query's
    endpoints are expected too) is no better than one with just enough."""
    expected = EXPECTED + [[IMATINIB], [ASTHMA]]
    message = path_message([KIT, MAST_CELL, HISTAMINE])
    assert pathfinder_ndcg(message, expected, 1) == pytest.approx(1.0)


def test_paths_are_ranked_by_analysis_score():
    # the KIT path comes second in the response, but has the better score
    message = path_message(["NCBIGene:1"], [KIT], scores=[0.2, 0.9])
    assert ranked_paths(message) == ["aux1", "aux0"]
    assert pathfinder_ndcg(message, EXPECTED, 1) == pytest.approx(1.0)


def test_unscored_analyses_go_last():
    message = path_message([KIT], ["NCBIGene:1"], scores=[None, 0.1])
    assert ranked_paths(message) == ["aux1", "aux0"]


def test_only_the_top_500_paths_count():
    message = path_message(*[["NCBIGene:1"]] * 500, [KIT])
    assert pathfinder_ndcg(message, EXPECTED, 1) == 0


def test_no_paths_scores_zero():
    message = {"results": [], "auxiliary_graphs": {}, "knowledge_graph": {}}
    assert pathfinder_ndcg(message, EXPECTED, 1) == 0


def test_no_expected_nodes_scores_nothing():
    assert pathfinder_ndcg(path_message([KIT]), [], 1) is None


def test_an_expected_node_listed_twice_counts_once():
    merged = merge_expected_nodes([[KIT], ["A", "B"], [KIT, "C"], ["B"], []])
    assert sorted(map(sorted, merged)) == [["A", "B"], ["C", KIT]]


def run_pathfinder(mocker, message, normalized=None):
    """Run a Pathfinder case of two assets sharing one query, the first
    expecting KIT and the second mast cell and histamine on one path, with the ARS
    answering ``message``. ``normalized`` are the CURIEs the node normalizer
    returns (all of them by default). Returns each asset's ARS report."""
    suite = json.loads(
        (
            Path(__file__).parents[1] / "test_harness/test_suites/local_pathfinder.json"
        ).read_text()
    )
    case = suite["test_cases"]["TestCase_1"]
    kit_asset = case["test_assets"][0]
    mast_cell_asset = {
        **kit_asset,
        "id": "Asset_2",
        "path_nodes": [
            {"ids": [MAST_CELL], "name": "mast cell"},
            {"ids": [HISTAMINE], "name": "histamine"},
        ],
        "minimum_required_path_nodes": 2,
    }
    case["test_assets"] = [kit_asset, mast_cell_asset]
    test = PathfinderTestCase.model_validate(case)

    query_runner = mocker.Mock()
    query_runner.run_queries.return_value = (
        {
            hash_test_asset(test.test_assets[0]): {
                "query": {},
                "pks": {"parent_pk": "pk", "ars": "pk"},
                "responses": {
                    "ars": {"status_code": 200, "response": {"message": message}}
                },
            }
        },
        {
            curie: curie
            for curie in normalized or (IMATINIB, ASTHMA, KIT, HISTAMINE, MAST_CELL)
        },
    )
    mocker.patch("test_harness.run.QueryRunner", return_value=query_runner)
    collector = MockResultCollector("ci", logger)
    collect = mocker.spy(collector, "collect_acceptance_result")

    run_tests(
        tests={test.id: test},
        reporter=MockReporter(base_url="http://test"),
        collector=collector,
        logger=logger,
        args={"env": "ci"},
    )

    return [call.args[2].result["ars"] for call in collect.call_args_list]


def test_ndcg_is_scored_per_asset(mocker):
    """Two assets sharing a query get the same response but are each scored
    against their own expected nodes: KIT is on the top path, mast cell and
    histamine together on the second."""
    reports = run_pathfinder(mocker, path_message([KIT], [MAST_CELL, HISTAMINE]))
    assert [report.ndcg for report in reports] == pytest.approx([1.0, at_rank(2)])


def test_an_unreadable_path_visits_nothing(mocker):
    """A path with no auxiliary graph (here the best scored) is skipped by
    both pass/fail and NDCG, and the rest of the response is still read."""
    message = path_message([KIT], [MAST_CELL, HISTAMINE])
    message["results"][0]["analyses"].append(
        {"score": 9, "path_bindings": {"p0": {"ids": ["gone"]}}}
    )
    reports = run_pathfinder(mocker, message)
    assert [report.status for report in reports] == [AgentStatus.PASSED] * 2
    assert [report.ndcg for report in reports] == pytest.approx(
        [at_rank(2), at_rank(3)]
    )


def test_a_curie_the_normalizer_missed_is_matched_as_given(mocker):
    """No normalized CURIE for mast cell: the asset is still scored, against
    mast cell as given, rather than erroring."""
    reports = run_pathfinder(
        mocker,
        path_message([KIT], [MAST_CELL, HISTAMINE]),
        normalized=(IMATINIB, ASTHMA, KIT, HISTAMINE),
    )
    assert [report.status for report in reports] == [AgentStatus.PASSED] * 2
    assert [report.ndcg for report in reports] == pytest.approx([1.0, at_rank(2)])


def test_pass_fail_counts_a_node_once_on_a_path():
    """Two CURIEs of one expected node on a path are still one node."""
    message = path_message([KIT, "UniProtKB:P10721"])
    report = {"ara": PathfinderReport(AgentStatus.SKIPPED, None, None, "")}
    path_nodes = [[KIT, "UniProtKB:P10721"], [MAST_CELL]]
    pathfinder_pass_fail_analysis(report, "ara", message, path_nodes, 2)
    assert report["ara"].status == AgentStatus.FAILED
    assert report["ara"].expected_nodes_found == KIT
    assert pathfinder_ndcg(message, path_nodes, 2) == 0
