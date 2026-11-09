from math import log2
from typing import Any, Dict, List, Optional, Set

from test_harness.trapi import binding_ids
from test_harness.utils import AgentStatus, PathfinderReport

# Pathfinder puts at most this many nodes between the two endpoints, so it's
# as many expected nodes as one path can carry.
MAX_PATH_NODES = 3
# Only this many of an agent's best scored paths count towards its NDCG.
NDCG_CUTOFF = 500


def merge_expected_nodes(path_nodes: List[List[str]]) -> List[Set[str]]:
    """Merge expected nodes that share a CURIE into one node.

    A query's expected nodes are gathered from every asset that shares it, so
    the same node can be listed more than once, under the same or overlapping
    CURIEs. Each must count once. A node with no CURIEs can never be found,
    so it's left out.
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

    Every path is ranked on its own; paths of one analysis share its score
    and keep their order. Analyses without a score go last.
    """
    analyses = [
        analysis
        for result in message.get("results") or []
        for analysis in result.get("analyses") or []
    ]
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


def ideal_dcg(n_expected: int) -> float:
    """The DCG of the best possible ranking of ``n_expected`` nodes.

    That's the expected nodes packed into the top paths, as many as one path
    can carry (``MAX_PATH_NODES``) in each: 7 nodes go 3, 3, 1.
    """
    dcg = 0.0
    rank = 1
    while n_expected > 0:
        relevance = min(MAX_PATH_NODES, n_expected)
        dcg += (2**relevance - 1) / log2(rank + 1)
        n_expected -= relevance
        rank += 1
    return dcg


def pathfinder_ndcg(
    message: Dict[str, Any], path_nodes: List[List[str]]
) -> Optional[float]:
    """NDCG of a Pathfinder response's top ``NDCG_CUTOFF`` paths.

    ``path_nodes`` are every expected node of the query, each as its
    normalized CURIEs. A path's relevance is how many expected nodes it is the
    first to visit: an expected node seen on a better ranked path counts as
    unexpected from then on. Its gain is ``2**relevance - 1``.

    None when the query expects no nodes, so there's nothing to score.
    """
    expected = merge_expected_nodes(path_nodes)
    if not expected:
        return None
    seen: Set[int] = set()
    dcg = 0.0
    for rank, path_id in enumerate(ranked_paths(message)[:NDCG_CUTOFF], start=1):
        on_path = set()
        for edge_id in message["auxiliary_graphs"][path_id]["edges"]:
            edge = message["knowledge_graph"]["edges"][edge_id]
            on_path.update((edge["subject"], edge["object"]))
        first_visits = {
            index
            for index, curies in enumerate(expected)
            if index not in seen and curies & on_path
        }
        seen |= first_visits
        dcg += (2 ** len(first_visits) - 1) / log2(rank + 1)
    return dcg / ideal_dcg(len(expected))


def pathfinder_pass_fail_analysis(
    report: Dict[str, PathfinderReport],
    agent: str,
    message: Dict[str, Any],
    path_nodes: List[List[str]],
    minimum_required_path_nodes: int,
) -> Dict[str, Any]:
    found_path_nodes = set()
    unmatched_paths = set()
    for analysis in message["results"][0]["analyses"]:
        for path_binding in analysis["path_bindings"].values():
            for path_id in binding_ids(path_binding):
                matching_path_nodes = set()
                for edge_id in message["auxiliary_graphs"][path_id]["edges"]:
                    edge = message["knowledge_graph"]["edges"][edge_id]
                    for node_curies in path_nodes:
                        unhit_node = True
                        for curie in node_curies:
                            if curie in matching_path_nodes:
                                unhit_node = False
                        if unhit_node:
                            if edge["subject"] in node_curies:
                                matching_path_nodes.add(edge["subject"])
                            if edge["object"] in node_curies:
                                matching_path_nodes.add(edge["object"])
                if len(matching_path_nodes) >= minimum_required_path_nodes:
                    found_path_nodes.add(",".join(matching_path_nodes))
                elif len(matching_path_nodes) > 0:
                    unmatched_paths.add(",".join(matching_path_nodes))

    if len(found_path_nodes) > 0:
        report[agent].status = AgentStatus.PASSED
        report[agent].expected_nodes_found = "; ".join(found_path_nodes)
    elif len(unmatched_paths) > 0:
        report[agent].status = AgentStatus.FAILED
        report[agent].expected_nodes_found = "; ".join(unmatched_paths)
    else:
        report[agent].status = AgentStatus.FAILED

    return report
