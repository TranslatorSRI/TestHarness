"""Given a Test Asset, generate a TRAPI query."""

import copy
from translator_testing_model.datamodel.pydanticmodel import TestAsset

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


def generate_query(test_asset: TestAsset) -> dict:
    """Generate a TRAPI query."""
    query = {}
    if test_asset.predicate_id == "biolink:treats":
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
