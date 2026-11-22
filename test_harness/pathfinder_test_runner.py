from math import log2
from typing import Any, Dict, List, Optional, Set

from translator_testing_model.datamodel.pydanticmodel import PathfinderTestAsset

from test_harness.trapi import binding_ids
from test_harness.utils import AgentStatus, PathfinderReport

# Only this many of an agent's best scored paths count towards its NDCG.
NDCG_CUTOFF = 500


def merge_expected_nodes(path_nodes: List[List[str]]) -> List[Set[str]]:
    """Merge expected nodes that share a CURIE into one node.

    An asset can list the same node more than once, under the same or
    overlapping CURIEs. Each must count once. A node with no CURIEs can never
    be found, so it's left out.
    """
    merged: List[Set[str]] = []
    for curies in path_nodes:
        node = set(curies)
        if not node:
            continue
        for other in [other for other in merged if other & node]:
            node |= other
            merged.remove(other)
        merged.append(node)
    return merged


def ranked_paths(message: Dict[str, Any]) -> List[str]:
    """The ids of the paths in ``message``, best scored analysis first.

    Pathfinder answers its one subject-object pair with a single result, so
    every path is in an analysis of ``results[0]``.

    Every path is ranked on its own; paths of one analysis share its score
    and keep their order. Analyses without a score go last.
    """
    results = message.get("results") or [{}]
    analyses = list(results[0].get("analyses") or [])
    analyses.sort(
        key=lambda analysis: (
            analysis.get("score") is None,
            -(analysis.get("score") or 0),
        )
    )
    return [
        path_id
        for analysis in analyses
        for path_binding in (analysis.get("path_bindings") or {}).values()
        for path_id in binding_ids(path_binding)
    ]


def expected_path_nodes(
    asset: PathfinderTestAsset, normalized_curies: Dict[str, str]
) -> List[List[str]]:
    """An asset's expected nodes, each as its normalized CURIEs. A CURIE the
    node normalizer didn't return is matched as given."""
    return [
        [normalized_curies.get(curie, curie) for curie in path_node.ids]
        for path_node in asset.path_nodes
    ]


def path_curies(message: Dict[str, Any], path_id: str) -> Set[str]:
    """Every node a path visits: each subject and object of its edges.

    A path, or an edge of it, missing from the message visits nothing, so one
    unreadable path doesn't stop the rest of the response being read.
    """
    auxiliary_graph = (message.get("auxiliary_graphs") or {}).get(path_id) or {}
    kg_edges = (message.get("knowledge_graph") or {}).get("edges") or {}
    curies = set()
    for edge_id in auxiliary_graph.get("edges") or []:
        edge = kg_edges.get(edge_id)
        if edge is not None:
            curies.update((edge["subject"], edge["object"]))
    return curies


def visited_expected_nodes(expected: List[Set[str]], on_path: Set[str]) -> List[str]:
    """The expected nodes a path visits, each as the CURIE it visits it by."""
    return [min(curies & on_path) for curies in expected if curies & on_path]


def pathfinder_ndcg(
    message: Dict[str, Any],
    path_nodes: List[List[str]],
    minimum_required_path_nodes: int,
) -> Optional[float]:
    """NDCG of a Pathfinder response's top ``NDCG_CUTOFF`` paths, for one asset.

    ``path_nodes`` are the asset's expected nodes, each as its normalized
    CURIEs. A path is relevant when it visits at least
    ``minimum_required_path_nodes`` of them, the most the asset asks of one
    path, and only the first relevant path counts: the asset is answered once
    it's found. A path with fewer expected nodes isn't relevant at all. That's
    the same test, on the same paths, as ``pathfinder_pass_fail_analysis``.

    The ideal is a relevant path at rank 1, so NDCG is ``1 / log2(rank + 1)``
    of the first relevant path (its gain cancels out), from 0 when there's
    none to 1 when it's the top path.

    None when the asset expects no nodes, so there's nothing to score.
    """
    expected = merge_expected_nodes(path_nodes)
    if not expected:
        return None
    required = max(1, minimum_required_path_nodes)
    for rank, path_id in enumerate(ranked_paths(message)[:NDCG_CUTOFF], start=1):
        visited = visited_expected_nodes(expected, path_curies(message, path_id))
        if len(visited) >= required:
            return 1 / log2(rank + 1)
    return 0.0


def pathfinder_pass_fail_analysis(
    report: Dict[str, PathfinderReport],
    agent: str,
    message: Dict[str, Any],
    path_nodes: List[List[str]],
    minimum_required_path_nodes: int,
) -> Dict[str, Any]:
    """Pass when any path visits at least
    ``minimum_required_path_nodes`` of the expected nodes.

    Paths are read the way ``pathfinder_ndcg`` reads them, so the two always
    agree on which paths answer the asset: it passes exactly when its NDCG,
    over every path rather than the top ``NDCG_CUTOFF``, is above 0.
    """
    expected = merge_expected_nodes(path_nodes)
    required = max(1, minimum_required_path_nodes)
    found_path_nodes = set()
    unmatched_paths = set()
    for path_id in ranked_paths(message):
        visited = visited_expected_nodes(expected, path_curies(message, path_id))
        if len(visited) >= required:
            found_path_nodes.add(",".join(visited))
        elif visited:
            unmatched_paths.add(",".join(visited))

    if found_path_nodes:
        report[agent].status = AgentStatus.PASSED
        report[agent].expected_nodes_found = "; ".join(sorted(found_path_nodes))
    elif unmatched_paths:
        report[agent].status = AgentStatus.FAILED
        report[agent].expected_nodes_found = "; ".join(sorted(unmatched_paths))
    else:
        report[agent].status = AgentStatus.FAILED

    return report
