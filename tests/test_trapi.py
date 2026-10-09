"""Tests for the TRAPI 2.0 surface: the queries sent and the responses read.

Queries are checked by validating them against TOM, the TRAPI object model,
for the version they were written in. Responses are checked in both binding
shapes, since the ARS can return either.
"""

import json

import pytest
import translator_tom.v1_6 as trapi_1_6
import translator_tom.v2_0 as trapi_2_0
from pytest_httpx import HTTPXMock
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    TestAsset,
)

from test_harness.acceptance_test_runner import run_acceptance_pass_fail_analysis
from test_harness.main import _trapi_version
from test_harness.pathfinder_test_runner import pathfinder_pass_fail_analysis
from test_harness.run import run_tests
from test_harness.runner.generate_query import generate_query
from test_harness.runner.query_runner import QueryRunner
from test_harness.trapi import binding_ids, is_trapi_2, trapi_minor_version
from test_harness.utils import AgentReport, AgentStatus, PathfinderReport

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mock_responses import kp_response
from .helpers.mocks import MockReporter, MockResultCollector

logger = setup_logger()


def _asset(predicate, input_id, input_category, qualifiers=(), inferred=True):
    return TestAsset.model_validate(
        {
            "id": "Asset_1",
            "test_runner_settings": ["inferred"] if inferred else [],
            "input_id": input_id,
            "input_category": input_category,
            "predicate_id": predicate,
            "output_id": "NCBIGene:1",
            "qualifiers": [
                {"parameter": parameter, "value": value}
                for parameter, value in qualifiers
            ],
            "expected_output": "TopAnswer",
        }
    )


MVP2_ASSET = _asset(
    "biolink:affects",
    "CHEBI:2",
    "biolink:ChemicalEntity",
    qualifiers=[
        ("biolink_qualified_predicate", "biolink:causes"),
        ("biolink_object_aspect_qualifier", "activity"),
        ("biolink_object_direction_qualifier", "increased"),
    ],
)

PATHFINDER_ASSET = PathfinderTestAsset.model_validate(
    {
        "id": "PTFQ_1",
        "source_input_id": "CHEBI:31690",
        "source_input_category": "biolink:Drug",
        "target_input_id": "MONDO:0004979",
        "target_input_category": "biolink:Disease",
        "predicate_id": "biolink:related_to",
        "minimum_required_path_nodes": 1,
        "path_nodes": [{"ids": ["NCBIGene:3815"], "name": "KIT"}],
        "expected_output": "TopAnswer",
    }
)


class TestVersions:
    def test_minor_version(self):
        assert trapi_minor_version("2.0.0") == "2.0"
        assert trapi_minor_version("1.6.1") == "1.6"
        assert is_trapi_2("2.0.0")
        assert not is_trapi_2("1.6.0")

    @pytest.mark.parametrize("version", ["1.5.0", "3.0.0", "latest", ""])
    def test_unsupported_version_is_rejected(self, version):
        with pytest.raises(ValueError, match="Unsupported TRAPI version"):
            trapi_minor_version(version)

    def test_cli_rejects_unsupported_version(self):
        from argparse import ArgumentTypeError

        assert _trapi_version("2.0.0") == "2.0.0"
        with pytest.raises(ArgumentTypeError):
            _trapi_version("1.5.0")

    def test_query_runner_rejects_unsupported_version(self):
        with pytest.raises(ValueError):
            QueryRunner(logger, trapi_version="1.5.0")


class TestQueries2_0:
    """Queries default to TRAPI 2.0."""

    def test_mvp1(self):
        query = generate_query(_asset("biolink:treats", "MONDO:1", "biolink:Disease"))
        trapi_2_0.Query.model_validate(query)
        qgraph = query["message"]["query_graph"]
        assert qgraph["nodes"] == {
            "SN": {"categories": ["biolink:ChemicalEntity"]},
            "ON": {"ids": ["MONDO:1"], "categories": ["biolink:Disease"]},
        }
        edge = qgraph["edges"]["t_edge"]
        assert edge["predicates"] == ["biolink:treats"]
        assert edge["knowledge_type"] == "inferred"
        assert "constraints" not in edge

    def test_mvp2_qualifiers_are_one_constraint_mapping(self):
        query = generate_query(MVP2_ASSET)
        trapi_2_0.Query.model_validate(query)
        edge = query["message"]["query_graph"]["edges"]["t_edge"]
        assert edge["constraints"] == {
            "qualifiers": [
                {
                    "biolink:object_aspect_qualifier": "activity",
                    "biolink:object_direction_qualifier": "increased",
                }
            ]
        }
        # the TRAPI 1.x spelling must not be sent: a 2.0 server rejects it
        assert "qualifier_constraints" not in edge

    def test_mvp2_leaves_out_unset_qualifiers(self):
        """2.0 forbids an empty qualifier mapping, so none is sent."""
        asset = _asset(
            "biolink:affects",
            "NCBIGene:1",
            "biolink:Gene",
            qualifiers=[("biolink_object_direction_qualifier", "decreased")],
            inferred=False,
        )
        query = generate_query(asset)
        trapi_2_0.Query.model_validate(query)
        edge = query["message"]["query_graph"]["edges"]["t_edge"]
        assert edge["constraints"] == {
            "qualifiers": [{"biolink:object_direction_qualifier": "decreased"}]
        }
        assert "knowledge_type" not in edge
        assert query["message"]["query_graph"]["nodes"]["ON"]["ids"] == ["NCBIGene:1"]

        asset.qualifiers = []
        edge = generate_query(asset)["message"]["query_graph"]["edges"]["t_edge"]
        assert "constraints" not in edge

    def test_pathfinder(self):
        query = generate_query(PATHFINDER_ASSET)
        trapi_2_0.Query.model_validate(query)
        qgraph = query["message"]["query_graph"]
        assert "edges" not in qgraph
        assert qgraph["paths"] == {"p0": {"subject": "SN", "object": "ON"}}
        assert qgraph["nodes"]["SN"] == {
            "ids": ["CHEBI:31690"],
            "categories": ["biolink:Drug"],
        }

    def test_unsupported_input_category(self):
        with pytest.raises(Exception, match="Unsupported input category for MVP1"):
            generate_query(_asset("biolink:treats", "CHEBI:1", "biolink:Drug"))


class TestQueries1_6:
    """A 1.6 query can still be asked for, for services that have not moved."""

    def test_mvp2(self):
        query = generate_query(MVP2_ASSET, "1.6.0")
        trapi_1_6.Query.model_validate(query)
        edge = query["message"]["query_graph"]["edges"]["t_edge"]
        assert edge["qualifier_constraints"] == [
            {
                "qualifier_set": [
                    {
                        "qualifier_type_id": "biolink:object_aspect_qualifier",
                        "qualifier_value": "activity",
                    },
                    {
                        "qualifier_type_id": "biolink:object_direction_qualifier",
                        "qualifier_value": "increased",
                    },
                ]
            }
        ]
        assert "constraints" not in edge

    def test_pathfinder(self):
        query = generate_query(PATHFINDER_ASSET, "1.6.0")
        trapi_1_6.Query.model_validate(query)
        assert query["message"]["query_graph"]["paths"] == {
            "p0": {"subject": "SN", "object": "ON"}
        }


class TestBindings:
    def test_binding_ids_reads_both_shapes(self):
        assert binding_ids({"ids": ["A", "B"]}) == ["A", "B"]
        assert binding_ids([{"id": "A"}, {"id": "B"}]) == ["A", "B"]
        assert binding_ids({}) == []
        assert binding_ids([]) == []

    def _report(self, agent):
        return {agent: AgentReport(AgentStatus.SKIPPED, None, None)}

    def test_acceptance_reads_2_0_bindings(self):
        results = [
            {
                "node_bindings": {"SN": {"ids": ["CHEBI:9"]}, "ON": {"ids": ["X:1"]}},
                "analyses": [{"resource_id": "infores:ara", "score": 0.9}],
            },
            {
                "node_bindings": {
                    "SN": {"ids": ["CHEBI:8", "CHEBI:2"]},
                    "ON": {"ids": ["X:1"]},
                },
                "analyses": [{"resource_id": "infores:ara", "score": 0.4}],
            },
        ]
        report = run_acceptance_pass_fail_analysis(
            self._report("ara"), "ara", results, "CHEBI:2", "TopAnswer"
        )
        assert report["ara"].status == AgentStatus.PASSED
        assert report["ara"].actual_output == {
            "found": True,
            "ars_score": None,
            "ars_rank": None,
            "ara_score": 0.4,
            "ara_rank": 2,
        }

    def test_acceptance_never_show_with_2_0_bindings(self):
        results = [{"node_bindings": {"SN": {"ids": ["CHEBI:2"]}}, "analyses": []}]
        report = run_acceptance_pass_fail_analysis(
            self._report("ara"), "ara", results, "CHEBI:2", "NeverShow"
        )
        assert report["ara"].status == AgentStatus.FAILED
        assert report["ara"].message is None

    @pytest.mark.parametrize(
        "path_binding", [{"ids": ["aux0"]}, [{"id": "aux0"}]], ids=["2.0", "1.x"]
    )
    def test_pathfinder_reads_path_bindings(self, path_binding):
        message = {
            "results": [
                {
                    "node_bindings": {},
                    "analyses": [
                        {
                            "resource_id": "infores:ara",
                            "path_bindings": {"p0": path_binding},
                        }
                    ],
                }
            ],
            "auxiliary_graphs": {"aux0": {"edges": ["e0", "e1"]}},
            "knowledge_graph": {
                "edges": {
                    "e0": {"subject": "CHEBI:31690", "object": "NCBIGene:3815"},
                    "e1": {"subject": "NCBIGene:3815", "object": "MONDO:0004979"},
                }
            },
        }
        report = {"ara": PathfinderReport(AgentStatus.SKIPPED, None, None, "")}
        pathfinder_pass_fail_analysis(report, "ara", message, [["NCBIGene:3815"]], 1)
        assert report["ara"].status == AgentStatus.PASSED
        assert report["ara"].expected_nodes_found == "NCBIGene:3815"


def test_run_sends_and_reads_trapi_2_0(httpx_mock: HTTPXMock):
    """End to end: a 2.0 query goes out and a 2.0 response is scored."""
    httpx_mock.add_response(url="http://localhost:8080/query", json=kp_response)
    httpx_mock.add_response(
        url="https://nodenorm-es.ci.transltr.io/get_normalized_nodes",
        json={"MONDO:0010794": None, "DRUGBANK:DB00313": None, "MESH:D001463": None},
    )
    collector = MockResultCollector("ci", logger, target="gandalf")
    tests = {"TestCase_1": example_test_cases["TestCase_1"]}
    run_tests(
        tests=tests,
        reporter=MockReporter(base_url="http://test"),
        collector=collector,
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "2.0.0",
            "target_url": "http://localhost:8080",
            "target": "gandalf",
        },
    )
    queries = [
        json.loads(request.content)
        for request in httpx_mock.get_requests(url="http://localhost:8080/query")
    ]
    assert queries
    for query in queries:
        trapi_2_0.Query.model_validate(query)
    # Both assets are NeverShow and kp_response binds neither expected answer,
    # so both pass -- reading 2.0 bindings as 1.x lists would fail them instead.
    assert collector.acceptance_stats["gandalf"]["NeverShow"]["PASSED"] == 2
