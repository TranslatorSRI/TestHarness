"""General utilities for the Test Harness."""

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Dict, List, Optional, Tuple, Union

import httpx
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    TestAsset,
    TestCase,
)

NODE_NORM_URL = {
    "dev": "https://nodenormalization-sri.renci.org/1.4",
    "ci": "https://nodenorm-es.ci.transltr.io",
    "test": "https://nodenorm-es.test.transltr.io",
    "prod": "https://nodenorm.transltr.io/1.4",
}

# The MVP query types, keyed by the test asset predicate that decides which
# query template an asset gets in runner/generate_query.py.
QUERY_TYPE_PREDICATES = {
    # drug treats disease
    "MVP1": "biolink:treats",
    # chemical affects gene
    "MVP2": "biolink:affects",
}


class AgentStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_RESULTS = "NO_RESULTS"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass
class AgentReport:
    """Dictionary for single agent report."""

    status: AgentStatus
    message: Optional[str]
    # Where the expected answer landed in this agent's response: a "found"
    # flag plus the ARS/ARA rank (int) and score (float) when it was found.
    actual_output: Optional[dict[str, Union[bool, int, float, None]]]
    # About the response itself rather than the analysis of it. None when it
    # isn't known: no response, or (for response_time_s) one fanned out by the
    # ARS, which the harness only sees through its 10s polling.
    http_status: Optional[int] = None
    n_results: Optional[int] = None
    response_time_s: Optional[float] = None


@dataclass
class PathfinderReport(AgentReport):
    """Dictionary for single Pathfinder agent report."""

    expected_nodes_found: Optional[str] = None


@dataclass
class TestReport:
    """Dictionary for single test report."""

    pks: dict[str, str]
    result: dict[str, AgentReport]
    test_details: Optional[dict[str, str | int]]


def normalize_curies(
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
    with httpx.Client() as client:
        try:
            response = client.post(
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


def filter_tests_by_query_type(
    tests: Dict[str, Union[TestCase, PathfinderTestCase]],
    query_type: Optional[str],
    logger: logging.Logger = logging.getLogger(__name__),
) -> Dict[str, Union[TestCase, PathfinderTestCase]]:
    """Keep only the tests whose queries are of the given MVP query type.

    ``query_type`` is ``MVP1`` (drug treats disease), ``MVP2`` (chemical
    affects gene), or ``None`` to run everything. Filtering happens per test
    asset, because that's what a query is generated from, and a test case
    drops out once none of its assets are left. Pathfinder test cases never
    produce an MVP query, so they're dropped whenever a query type is given.
    """
    if query_type is None:
        return tests
    predicate = QUERY_TYPE_PREDICATES.get(query_type)
    if predicate is None:
        raise ValueError(
            f"Unknown query type '{query_type}'. "
            f"Expected one of: {', '.join(QUERY_TYPE_PREDICATES)}."
        )

    filtered: Dict[str, Union[TestCase, PathfinderTestCase]] = {}
    kept_assets = 0
    total_assets = 0
    for test_id, test in tests.items():
        assets = test.test_assets or []
        total_assets += len(assets)
        if isinstance(test, PathfinderTestCase):
            continue
        matching = [asset for asset in assets if asset.predicate_id == predicate]
        if not matching:
            continue
        kept_assets += len(matching)
        # Copy rather than mutate: the caller's tests are left as they were.
        filtered[test_id] = test.model_copy(update={"test_assets": matching})

    logger.info(
        f"Running only {query_type} ({predicate}) queries: "
        f"{len(filtered)} of {len(tests)} test cases, "
        f"{kept_assets} of {total_assets} test assets."
    )
    return filtered


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
