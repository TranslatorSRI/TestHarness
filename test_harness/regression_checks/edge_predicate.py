"""Edge predicate regression check.

Verifies that every predicate returned in the knowledge graph (and bound to a
query-graph edge) is the queried predicate itself or a biolink descendant of
it. A returned predicate that is an ancestor of the queried predicate (less
specific) or otherwise unrelated counts as a regression.
"""

from typing import Any, Dict, List, Optional, Set

from test_harness.regression_checks.base import (
    RegressionCheckResult,
    RegressionStatus,
)


class EdgePredicateMatchCheck:
    name = "edge_predicate_match"

    def __init__(self) -> None:
        self._toolkit = None
        self._toolkit_init_error: Optional[str] = None
        self._descendants_cache: Dict[str, Set[str]] = {}

    def _get_toolkit(self):
        if self._toolkit is not None or self._toolkit_init_error is not None:
            return self._toolkit
        try:
            import bmt  # imported lazily; bmt.Toolkit() is slow
            self._toolkit = bmt.Toolkit()
        except Exception as e:
            self._toolkit_init_error = f"{type(e).__name__}: {e}"
        return self._toolkit

    def _allowed_predicates(self, predicate: str) -> Set[str]:
        cached = self._descendants_cache.get(predicate)
        if cached is not None:
            return cached
        toolkit = self._get_toolkit()
        if toolkit is None:
            return set()
        descendants = toolkit.get_descendants(
            predicate, reflexive=False, formatted=True
        ) or []
        allowed = {predicate, *descendants}
        self._descendants_cache[predicate] = allowed
        return allowed

    def run(
        self, message: Dict[str, Any], query_graph: Dict[str, Any]
    ) -> RegressionCheckResult:
        qg_edges = (query_graph or {}).get("edges") or {}
        qg_edges_with_predicates = {
            edge_id: edge
            for edge_id, edge in qg_edges.items()
            if edge.get("predicates")
        }
        if not qg_edges_with_predicates:
            return RegressionCheckResult(
                name=self.name,
                status=RegressionStatus.SKIPPED,
                message="No query predicates to check.",
            )

        if self._get_toolkit() is None:
            return RegressionCheckResult(
                name=self.name,
                status=RegressionStatus.SKIPPED,
                message=f"biolink toolkit unavailable: {self._toolkit_init_error}",
            )

        allowed_by_edge: Dict[str, Set[str]] = {}
        for edge_id, edge in qg_edges_with_predicates.items():
            allowed: Set[str] = set()
            for predicate in edge["predicates"]:
                allowed |= self._allowed_predicates(predicate)
            allowed_by_edge[edge_id] = allowed

        kg_edges = (message.get("knowledge_graph") or {}).get("edges") or {}
        results = message.get("results") or []
        mismatches: List[Dict[str, Any]] = []

        for result_idx, result in enumerate(results):
            for analysis_idx, analysis in enumerate(result.get("analyses") or []):
                for qg_edge_id, bindings in (analysis.get("edge_bindings") or {}).items():
                    allowed = allowed_by_edge.get(qg_edge_id)
                    if allowed is None:
                        continue
                    for binding in bindings or []:
                        kg_edge_id = binding.get("id")
                        kg_edge = kg_edges.get(kg_edge_id)
                        if kg_edge is None:
                            mismatches.append({
                                "result_index": result_idx,
                                "analysis_index": analysis_idx,
                                "qg_edge_id": qg_edge_id,
                                "kg_edge_id": kg_edge_id,
                                "reason": "kg_edge_missing",
                            })
                            continue
                        predicate = kg_edge.get("predicate")
                        if predicate not in allowed:
                            mismatches.append({
                                "result_index": result_idx,
                                "analysis_index": analysis_idx,
                                "qg_edge_id": qg_edge_id,
                                "kg_edge_id": kg_edge_id,
                                "predicate": predicate,
                                "expected_predicates": sorted(allowed),
                            })

        if mismatches:
            return RegressionCheckResult(
                name=self.name,
                status=RegressionStatus.FAILED,
                message=f"{len(mismatches)} edge(s) returned predicates not compatible with the query graph.",
                details={"mismatches": mismatches, "count": len(mismatches)},
            )
        return RegressionCheckResult(
            name=self.name,
            status=RegressionStatus.PASSED,
        )
