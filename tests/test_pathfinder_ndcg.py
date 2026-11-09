"""Test the NDCG of Pathfinder responses."""

import json
from math import log2
from pathlib import Path

import pytest
from translator_testing_model.datamodel.pydanticmodel import PathfinderTestCase

from test_harness.pathfinder_test_runner import (
    ideal_dcg,
    merge_expected_nodes,
    pathfinder_ndcg,
    ranked_paths,
)
from test_harness.run import run_tests
from test_harness.utils import AgentStatus, hash_test_asset

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


def dcg(*gains):
    return sum(gain / log2(rank + 1) for rank, gain in enumerate(gains, start=1))


EXPECTED = [[KIT], [HISTAMINE], [MAST_CELL]]


def test_ideal_dcg_packs_three_nodes_a_path():
    assert ideal_dcg(3) == 7
    # 7 nodes: 3 on the first path, 3 on the second, 1 on the third
    assert ideal_dcg(7) == pytest.approx(dcg(7, 7, 1))
    assert ideal_dcg(0) == 0


def test_every_expected_node_on_the_top_path_is_perfect():
    message = path_message([KIT, MAST_CELL, HISTAMINE])
    assert pathfinder_ndcg(message, EXPECTED) == pytest.approx(1.0)


def test_only_the_first_visit_of_a_node_counts():
    message = path_message(
        [KIT],  # KIT, first seen: 2**1 - 1
        ["NCBIGene:1"],  # nothing expected
        [KIT],  # KIT again: already seen, so not relevant
        [KIT, HISTAMINE],  # only histamine is new: 2**1 - 1
    )
    assert pathfinder_ndcg(message, EXPECTED) == pytest.approx(dcg(1, 0, 0, 1) / 7)


def test_paths_are_ranked_by_analysis_score():
    # the KIT path comes second in the response, but has the better score
    message = path_message(["NCBIGene:1"], [KIT], scores=[0.2, 0.9])
    assert ranked_paths(message) == ["aux1", "aux0"]
    assert pathfinder_ndcg(message, EXPECTED) == pytest.approx(1 / 7)


def test_unscored_analyses_go_last():
    message = path_message([KIT], ["NCBIGene:1"], scores=[None, 0.1])
    assert ranked_paths(message) == ["aux1", "aux0"]


def test_only_the_top_500_paths_count():
    message = path_message(*[["NCBIGene:1"]] * 500, [KIT])
    assert pathfinder_ndcg(message, EXPECTED) == 0


def test_no_expected_nodes_scores_nothing():
    assert pathfinder_ndcg(path_message([KIT]), []) is None


def test_an_expected_node_listed_twice_counts_once():
    merged = merge_expected_nodes([[KIT], ["A", "B"], [KIT, "C"], ["B"], []])
    assert sorted(map(sorted, merged)) == [["A", "B"], ["C", KIT]]


def run_pathfinder(mocker, message):
    """Run a Pathfinder case of two assets sharing one query, the first
    expecting KIT and the second mast cell and histamine, with the ARS
    answering ``message``. Returns each asset's ARS report."""
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
        {curie: curie for curie in (IMATINIB, ASTHMA, KIT, HISTAMINE, MAST_CELL)},
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


def test_ndcg_is_scored_per_query(mocker):
    """Two assets sharing a query are scored against both their expected
    nodes, and get the same NDCG."""
    reports = run_pathfinder(mocker, path_message([KIT], [MAST_CELL, HISTAMINE]))
    assert [report.ndcg for report in reports] == pytest.approx([dcg(1, 3) / 7] * 2)


def test_a_response_ndcg_cant_score_still_passes(mocker):
    """A path NDCG can't read (here one with no auxiliary graph, in a second
    result) leaves the NDCG unset but doesn't fail a test that passed."""
    message = path_message([KIT], [MAST_CELL, HISTAMINE])
    message["results"].append(
        {
            "node_bindings": {},
            "analyses": [{"score": 9, "path_bindings": {"p0": {"ids": ["gone"]}}}],
        }
    )
    reports = run_pathfinder(mocker, message)
    assert [report.status for report in reports] == [AgentStatus.PASSED] * 2
    assert [report.ndcg for report in reports] == [None, None]
