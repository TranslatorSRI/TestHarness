"""General utilities for the Test Harness."""

import httpx
import logging
from typing import Dict, Union, List, Tuple

from translator_testing_model.datamodel.pydanticmodel import (
    TestCase,
    PathfinderTestCase,
    TestAsset,
    PathfinderTestAsset,
)

NODE_NORM_URL = {
    "dev": "https://nodenormalization-sri.renci.org/1.4",
    "ci": "https://nodenorm.ci.transltr.io",
    "test": "https://nodenorm.test.transltr.io/1.4",
    "prod": "https://nodenorm.transltr.io/1.4",
}


async def normalize_curies(
    test: Union[TestCase, PathfinderTestCase],
    logger: logging.Logger = logging.getLogger(__name__),
) -> Dict[str, Dict[str, Union[Dict[str, str], List[str]]]]:
    """Normalize a list of curies."""
    node_norm = NODE_NORM_URL.get(test.test_env)
    # collect all curies from test
    if isinstance(test, PathfinderTestCase):
        curies = set([asset.source_input_id for asset in test.test_assets])
        curies.update([asset.target_input_id for asset in test.test_assets])
        curies.update(
            [
                path_node_id
                for asset in test.test_assets
                for path_node in asset.path_nodes
                for path_node_id in path_node.ids
            ]
        )
    else:
        curies = set([asset.output_id for asset in test.test_assets])
        curies.update([asset.input_id for asset in test.test_assets])
        curies.add(test.test_case_input_id)

    normalized_curies = {}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                node_norm + "/get_normalized_nodes",
                json={
                    "curies": list(curies),
                    "conflate": True,
                    "drug_chemical_conflate": True,
                },
            )
            response.raise_for_status()
            response = response.json()
            for curie, attrs in response.items():
                if attrs is None:
                    # keep original curie
                    normalized_curies[curie] = curie
                else:
                    # choose the perferred id
                    normalized_curies[curie] = attrs["id"]["identifier"]
        except Exception as e:
            logger.error(f"Node norm failed with: {e}")
            logger.error("Using original curies.")
            for curie in curies:
                normalized_curies[curie] = curie
    return normalized_curies


def get_tag(result):
    """Given a result, get the correct tag for the label."""
    tag = result.get("status", "FAILED")
    if tag != "PASSED":
        message = result.get("message")
        if message:
            tag = message
    return tag


def hash_test_asset(test_asset: Union[TestAsset, PathfinderTestAsset]) -> int:
    """Given a test asset, return its unique hash."""
    if isinstance(test_asset, PathfinderTestAsset):
        asset_hash = hash(
            (
                test_asset.source_input_id,
                test_asset.target_input_id,
                test_asset.predicate_id,
                *[qualifier.value for qualifier in (test_asset.qualifiers or [])],
            )
        )
    else:
        asset_hash = hash(
            (
                test_asset.input_id,
                test_asset.predicate_id,
                *[qualifier.value for qualifier in test_asset.qualifiers],
            )
        )
    return asset_hash


def get_qualifier_constraints(test_asset: TestAsset) -> Tuple[str, str]:
    """Get qualifier constraints from a Test Asset."""
    biolink_object_aspect_qualifier = ""
    biolink_object_direction_qualifier = ""
    for qualifier in test_asset.qualifiers:
        if qualifier.parameter == "biolink_object_aspect_qualifier":
            biolink_object_aspect_qualifier = qualifier.value
        elif qualifier.parameter == "biolink_object_direction_qualifier":
            biolink_object_direction_qualifier = qualifier.value

    return biolink_object_aspect_qualifier, biolink_object_direction_qualifier
