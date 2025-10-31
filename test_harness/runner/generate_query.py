"""Given a Test Asset, generate a TRAPI query."""

import copy
from typing import Union

from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    TestAsset,
)

from test_harness.utils import get_qualifier_constraints

MVP1 = {
    "message": {
        "query_graph": {
            "nodes": {
                "ON": {"categories": ["biolink:Disease"]},
                "SN": {"categories": ["biolink:ChemicalEntity"]},
            },
            "edges": {
                "t_edge": {
                    "object": "ON",
                    "subject": "SN",
                    "predicates": ["biolink:treats"],
                }
            },
        }
    }
}

MVP2 = {
    "message": {
        "query_graph": {
            "nodes": {
                "ON": {"categories": ["biolink:Gene"]},
                "SN": {"categories": ["biolink:ChemicalEntity"]},
            },
            "edges": {
                "t_edge": {
                    "object": "ON",
                    "subject": "SN",
                    "predicates": ["biolink:affects"],
                    "qualifier_constraints": [
                        {
                            "qualifier_set": [
                                {
                                    "qualifier_type_id": "biolink:object_aspect_qualifier",
                                    "qualifier_value": "",
                                },
                                {
                                    "qualifier_type_id": "biolink:object_direction_qualifier",
                                    "qualifier_value": "",
                                },
                            ]
                        }
                    ],
                }
            },
        }
    }
}

PATHFINDER = {
    "message": {
        "query_graph": {
            "nodes": {
                "SN": {
                    "set_interpretation": "BATCH",
                    "constraints": [],
                    "member_ids": [],
                },
                "ON": {
                    "set_interpretation": "BATCH",
                    "constraints": [],
                    "member_ids": [],
                },
            },
            "paths": {"p0": {"subject": "SN", "object": "ON"}},
        }
    }
}


def generate_query(test_asset: Union[TestAsset, PathfinderTestAsset]) -> dict:
    """Generate a TRAPI query."""
    query = {}
    if isinstance(test_asset, PathfinderTestAsset):
        source_id = test_asset.source_input_id
        target_id = test_asset.target_input_id
        query = copy.deepcopy(PATHFINDER)
        query["message"]["query_graph"]["nodes"]["SN"] = {
            "ids": [source_id],
            "categories": [test_asset.source_input_category],
        }
        query["message"]["query_graph"]["nodes"]["ON"] = {
            "ids": [target_id],
            "categories": [test_asset.target_input_category],
        }
    elif test_asset.predicate_id == "biolink:treats":
        # MVP1
        query = copy.deepcopy(MVP1)
        # add id to node
        if test_asset.input_category == "biolink:Disease":
            query["message"]["query_graph"]["nodes"]["ON"]["ids"] = [
                test_asset.input_id
            ]
        else:
            raise Exception(
                f"Unsupported input category for MVP1: {test_asset.input_category}"
            )
        # add knowledge_type
        if "inferred" in test_asset.test_runner_settings:
            query["message"]["query_graph"]["edges"]["t_edge"][
                "knowledge_type"
            ] = "inferred"
    elif test_asset.predicate_id == "biolink:affects":
        # MVP2
        query = copy.deepcopy(MVP2)
        # add id to corresponding node
        if test_asset.input_category == "biolink:ChemicalEntity":
            query["message"]["query_graph"]["nodes"]["SN"]["ids"] = [
                test_asset.input_id
            ]
        elif test_asset.input_category == "biolink:Gene":
            query["message"]["query_graph"]["nodes"]["ON"]["ids"] = [
                test_asset.input_id
            ]
        else:
            raise Exception(f"Unsupported input category: {test_asset.input_category}")
        # add qualifier constraints
        aspect_qualifier, direction_qualifier = get_qualifier_constraints(test_asset)
        query["message"]["query_graph"]["edges"]["t_edge"]["qualifier_constraints"][0][
            "qualifier_set"
        ][0]["qualifier_value"] = aspect_qualifier
        query["message"]["query_graph"]["edges"]["t_edge"]["qualifier_constraints"][0][
            "qualifier_set"
        ][1]["qualifier_value"] = direction_qualifier
        # add knowledge_type
        if "inferred" in test_asset.test_runner_settings:
            query["message"]["query_graph"]["edges"]["t_edge"][
                "knowledge_type"
            ] = "inferred"
    else:
        raise Exception(f"Unsupported predicate: {test_asset.predicate_id}")

    return query


if __name__ == "__main__":
    test_asset = TestAsset.parse_obj({
    "id": "Asset_450",
    "name": "NeverShow: MMP3 increases activity or abundance of Potassium ion",
    "description": "NeverShow: MMP3 increases activity or abundance of Potassium ion",
    "tags": [],
    "test_runner_settings": [
        "inferred"
    ],
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
        {
            "parameter": "biolink_qualified_predicate",
            "value": "biolink:causes"
        },
        {
            "parameter": "biolink_object_aspect_qualifier",
            "value": "activity_or_abundance"
        },
        {
            "parameter": "biolink_object_direction_qualifier",
            "value": "increased"
        }
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
        "test_annotations": []
    }
})
    query = generate_query(test_asset)
    print(query)
