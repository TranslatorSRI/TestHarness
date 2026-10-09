"""Given a Test Asset, generate a TRAPI query.

Queries are built from ``translator_tom`` (TOM) models for the TRAPI version
under test, so a malformed query fails here, against that version's schema,
rather than at the service it was sent to. ``to_dict()`` drops unset fields,
which is the JSON that goes on the wire.
"""

from typing import Dict, List, Optional, Union

import translator_tom.v1_6 as trapi_1_6
import translator_tom.v2_0 as trapi_2_0
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    TestAsset,
)

from test_harness.trapi import DEFAULT_TRAPI_VERSION, is_trapi_2
from test_harness.utils import QUERY_TYPE_PREDICATES, get_qualifier_constraints

# Which query node a test asset's input pins, by query type and input
# category. SN is the subject (the chemical), ON the object.
INPUT_NODES = {
    "MVP1": {"biolink:Disease": "ON"},
    "MVP2": {"biolink:ChemicalEntity": "SN", "biolink:Gene": "ON"},
}

# The categories of the two query nodes, by query type.
NODE_CATEGORIES = {
    "MVP1": {"SN": "biolink:ChemicalEntity", "ON": "biolink:Disease"},
    "MVP2": {"SN": "biolink:ChemicalEntity", "ON": "biolink:Gene"},
}


def _query_type(test_asset: TestAsset) -> str:
    for query_type, predicate in QUERY_TYPE_PREDICATES.items():
        if test_asset.predicate_id == predicate:
            return query_type
    raise Exception(f"Unsupported predicate: {test_asset.predicate_id}")


def _qualifier_set(test_asset: TestAsset) -> Dict[str, str]:
    """The asset's object aspect / direction qualifiers, leaving out unset ones."""
    aspect_qualifier, direction_qualifier = get_qualifier_constraints(test_asset)
    qualifiers = {
        "biolink:object_aspect_qualifier": aspect_qualifier,
        "biolink:object_direction_qualifier": direction_qualifier,
    }
    return {type_id: value for type_id, value in qualifiers.items() if value}


def _categories(category: Optional[str]) -> Optional[List[str]]:
    return [category] if category else None


def _one_hop_query_2_0(
    query_type: str, test_asset: TestAsset, input_node: str
) -> trapi_2_0.Query:
    qualifier_set = _qualifier_set(test_asset) if query_type == "MVP2" else {}
    edge = trapi_2_0.QEdge(
        subject="SN",
        object="ON",
        predicates=[test_asset.predicate_id],
        # TRAPI 2.0 gathers every edge constraint into one object, and a
        # QualifierSetConstraint is a plain {qualifier_type_id: value} mapping.
        constraints=(
            trapi_2_0.QEdgeConstraints(qualifiers=[qualifier_set])
            if qualifier_set
            else None
        ),
        knowledge_type=(
            "inferred" if "inferred" in test_asset.test_runner_settings else None
        ),
    )
    nodes = {
        node: trapi_2_0.QNode(
            categories=[category],
            ids=[test_asset.input_id] if node == input_node else None,
        )
        for node, category in NODE_CATEGORIES[query_type].items()
    }
    return trapi_2_0.Query(
        message=trapi_2_0.Message(
            query_graph=trapi_2_0.QueryGraph(nodes=nodes, edges={"t_edge": edge})
        )
    )


def _one_hop_query_1_6(
    query_type: str, test_asset: TestAsset, input_node: str
) -> trapi_1_6.Query:
    qualifier_set = _qualifier_set(test_asset) if query_type == "MVP2" else {}
    edge = trapi_1_6.QEdge(
        subject="SN",
        object="ON",
        predicates=[test_asset.predicate_id],
        qualifier_constraints=(
            [
                trapi_1_6.QualifierConstraint(
                    qualifier_set=[
                        trapi_1_6.Qualifier(
                            qualifier_type_id=type_id, qualifier_value=value
                        )
                        for type_id, value in qualifier_set.items()
                    ]
                )
            ]
            if qualifier_set
            else None
        ),
        knowledge_type=(
            "inferred" if "inferred" in test_asset.test_runner_settings else None
        ),
    )
    nodes = {
        node: trapi_1_6.QNode(
            categories=[category],
            ids=[test_asset.input_id] if node == input_node else None,
        )
        for node, category in NODE_CATEGORIES[query_type].items()
    }
    return trapi_1_6.Query(
        message=trapi_1_6.Message(
            query_graph=trapi_1_6.QueryGraph(nodes=nodes, edges={"t_edge": edge})
        )
    )


def _pathfinder_query(
    test_asset: PathfinderTestAsset, trapi_version: str
) -> Union[trapi_2_0.Query, trapi_1_6.Query]:
    trapi = trapi_2_0 if is_trapi_2(trapi_version) else trapi_1_6
    nodes = {
        "SN": trapi.QNode(
            ids=[test_asset.source_input_id],
            categories=_categories(test_asset.source_input_category),
        ),
        "ON": trapi.QNode(
            ids=[test_asset.target_input_id],
            categories=_categories(test_asset.target_input_category),
        ),
    }
    paths = {"p0": trapi.QPath(subject="SN", object="ON")}
    # TRAPI 2.0 makes QueryGraph.edges optional so one model covers Pathfinder
    # queries; 1.6 has a separate PathfinderQueryGraph for them.
    query_graph = (
        trapi_2_0.QueryGraph(nodes=nodes, paths=paths)
        if trapi is trapi_2_0
        else trapi_1_6.PathfinderQueryGraph(nodes=nodes, paths=paths)
    )
    return trapi.Query(message=trapi.Message(query_graph=query_graph))


def generate_query(
    test_asset: Union[TestAsset, PathfinderTestAsset],
    trapi_version: str = DEFAULT_TRAPI_VERSION,
) -> dict:
    """Generate a TRAPI query for ``trapi_version`` (2.0.x or 1.6.x)."""
    if isinstance(test_asset, PathfinderTestAsset):
        return _pathfinder_query(test_asset, trapi_version).to_dict()

    query_type = _query_type(test_asset)
    input_node = INPUT_NODES[query_type].get(test_asset.input_category)
    if input_node is None:
        raise Exception(
            f"Unsupported input category for {query_type}: "
            f"{test_asset.input_category}"
        )
    if is_trapi_2(trapi_version):
        query = _one_hop_query_2_0(query_type, test_asset, input_node)
    else:
        query = _one_hop_query_1_6(query_type, test_asset, input_node)
    return query.to_dict()


if __name__ == "__main__":
    test_asset = TestAsset.model_validate(
        {
            "id": "Asset_450",
            "name": "NeverShow: MMP3 increases activity or abundance of Potassium ion",
            "description": "NeverShow: MMP3 increases activity or abundance of Potassium ion",
            "tags": [],
            "test_runner_settings": ["inferred"],
            "input_id": "CHEBI:29103",
            "input_name": "Potassium ion",
            "input_category": "biolink:ChemicalEntity",
            "predicate_id": "biolink:affects",
            "predicate_name": "affects",
            "output_id": "NCBIGene:4314",
            "output_name": "MMP3",
            "output_category": "biolink:Gene",
            "association": None,
            "qualifiers": [
                {"parameter": "biolink_qualified_predicate", "value": "biolink:causes"},
                {
                    "parameter": "biolink_object_aspect_qualifier",
                    "value": "activity_or_abundance",
                },
                {
                    "parameter": "biolink_object_direction_qualifier",
                    "value": "increased",
                },
            ],
            "expected_output": "NeverShow",
            "test_issue": None,
            "semantic_severity": None,
            "in_v1": None,
            "well_known": False,
            "test_reference": None,
            "test_metadata": {
                "id": "1",
                "name": None,
                "description": None,
                "tags": [],
                "test_runner_settings": [],
                "test_source": "SMURF",
                "test_reference": "https://github.com/NCATSTranslator/Feedback/issues/740",
                "test_objective": "AcceptanceTest",
                "test_annotations": [],
            },
        }
    )
    query = generate_query(test_asset)
    print(query)
