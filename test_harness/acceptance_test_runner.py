"""Acceptance Test Pass Fail Analysis Runner."""

from typing import Any, Dict, List, Optional

from test_harness.trapi import binding_ids
from test_harness.utils import AgentReport, AgentStatus


def get_ars_confidence(result: Dict[str, Any]) -> Optional[float]:
    """Pull the ARS confidence score off a result, if it has one.

    The ARS reports it either directly on the result or under its
    ``ordering_components``; ARA results carry neither.
    """
    confidence = result.get("confidence")
    if confidence is None:
        confidence = (result.get("ordering_components") or {}).get("confidence")
    return confidence


def sort_by_ars_confidence(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort ARS results by confidence, highest first.

    The ARS doesn't guarantee its results come back in order, so sort them
    here and let a result's position be its rank. Results without a
    confidence score go last, and ties keep their original order.
    """

    def sort_key(res):
        confidence = get_ars_confidence(res)
        return (confidence is None, -(confidence or 0))

    return sorted(results, key=sort_key)


def _verdict(
    expect_output: str,
    in_window: bool,
    in_results: bool,
) -> AgentStatus:
    """Whether one expected CURIE passes, given where it landed."""
    if expect_output in ["TopAnswer", "Acceptable"]:
        return AgentStatus.PASSED if in_window else AgentStatus.FAILED
    if expect_output == "BadButForgivable":
        # forgivable in the bottom half, or left out entirely
        return AgentStatus.PASSED if in_window or not in_results else AgentStatus.FAILED
    # NeverShow: the window is every result
    return AgentStatus.FAILED if in_window else AgentStatus.PASSED


def run_acceptance_pass_fail_analysis(
    report: Dict[str, AgentReport],
    agent: str,
    results: List[Dict[str, Any]],
    out_curies: List[str],
    expect_output: str,
):
    """Function to run pass fail analysis on individual results.

    ``out_curies`` are the (normalized) CURIEs that each stand for the expected
    answer, eg one chemical under its ChEBI and PubChem ids when node
    normalization keeps them apart. Each is judged on its own; a TopAnswer or
    Acceptable asset passes if any of them passes, and a BadButForgivable or
    NeverShow asset fails if any of them fails. The rank and score reported are
    those of the best-ranked result binding any of them.
    """
    try:
        # ARS results are scored by their confidence, so make sure they're
        # ordered by it before slicing out the top n and assigning ranks.
        is_ars = any(get_ars_confidence(res) is not None for res in results)
        if is_ars:
            results = sort_by_ars_confidence(results)
        if expect_output == "TopAnswer":
            window = range(0, min(30, len(results)))
        elif expect_output == "Acceptable":
            window = range(0, int(len(results) * (float(50) / 100)))
        elif expect_output == "BadButForgivable":
            window = range(int(len(results) * (float(50) / 100)), len(results))
        elif expect_output == "NeverShow":
            window = range(0, len(results))
        else:
            error_mesg = {
                "error": "You have indicated a wrong category for expected output",
            }
            return error_mesg

        # where each expected CURIE turns up: the indexes of the results that
        # bind it, best first
        positions: Dict[str, List[int]] = {curie: [] for curie in out_curies}
        for idx, res in enumerate(results):
            bound = set()
            for res_value in res["node_bindings"].values():
                bound.update(binding_ids(res_value))
            for curie in positions:
                if curie in bound:
                    positions[curie].append(idx)

        verdicts = [
            _verdict(
                expect_output,
                any(idx in window for idx in positions[curie]),
                bool(positions[curie]),
            )
            for curie in out_curies
        ]
        if expect_output in ["TopAnswer", "Acceptable"]:
            passed = AgentStatus.PASSED in verdicts
        else:
            passed = AgentStatus.FAILED not in verdicts
        report[agent].status = AgentStatus.PASSED if passed else AgentStatus.FAILED

        # Record whether the expected answer came back at all, and under which
        # of its CURIEs: more than one means Translator split one answer into
        # several. The rank/score only exist when it came back, so "found" is
        # what tells "in the response, but unranked" apart from "never showed
        # up".
        found_curies = sorted(
            (curie for curie in out_curies if positions[curie]),
            key=lambda curie: positions[curie][0],
        )
        actual_output = {
            "found": bool(found_curies),
            "found_curies": found_curies,
            "ars_score": None,
            "ars_rank": None,
            "ara_score": None,
            "ara_rank": None,
        }
        if found_curies:
            best = positions[found_curies[0]][0]
            res = results[best]
            if is_ars:
                actual_output["ars_score"] = get_ars_confidence(res)
                actual_output["ars_rank"] = best + 1
            else:
                for anal in res["analyses"]:
                    if "score" in anal.keys():
                        actual_output["ara_score"] = anal["score"]
                actual_output["ara_rank"] = best + 1
        report[agent].actual_output = actual_output
    except Exception as e:
        report[agent].status = AgentStatus.FAILED
        report[agent].message = f"An exception happened: {type(e), str(e)}"

    return report
