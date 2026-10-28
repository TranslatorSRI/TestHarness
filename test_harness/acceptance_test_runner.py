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


def run_acceptance_pass_fail_analysis(
    report: Dict[str, AgentReport],
    agent: str,
    results: List[Dict[str, Any]],
    out_curie: str,
    expect_output: str,
):
    """Function to run pass fail analysis on individual results."""
    # get the top_n result's ids
    try:
        # ARS results are scored by their confidence, so make sure they're
        # ordered by it before slicing out the top n and assigning ranks.
        is_ars = any(get_ars_confidence(res) is not None for res in results)
        if is_ars:
            results = sort_by_ars_confidence(results)
        all_ids = []
        for res in results:
            for res_value in res["node_bindings"].values():
                for ids in binding_ids(res_value):
                    if ids not in all_ids:
                        all_ids.append(ids)
        if expect_output == "TopAnswer":
            n_perc_res = results[0:30]
        elif expect_output == "Acceptable":
            n_perc_res = results[0 : int(len(results) * (float(50) / 100))]
        elif expect_output == "BadButForgivable":
            n_perc_res = results[int(len(results) * (float(50) / 100)) :]
        elif expect_output == "NeverShow":
            n_perc_res = results
        else:
            error_mesg = {
                "error": "You have indicated a wrong category for expected output",
            }
            return error_mesg
        n_perc_ids = []
        for res in n_perc_res:
            for res_value in res["node_bindings"].values():
                for ids in binding_ids(res_value):
                    if ids not in n_perc_ids:
                        n_perc_ids.append(ids)
        # Record up front whether the expected answer came back at all. The
        # rank/score below only exist when it did, so this flag is what tells
        # "in the response, but unranked" apart from "never showed up".
        no_scores = {
            "ars_score": None,
            "ars_rank": None,
            "ara_score": None,
            "ara_rank": None,
        }
        not_found_output = {"found": False, **no_scores}
        report[agent].actual_output = {"found": out_curie in all_ids, **no_scores}
        # get the confidence score & rank
        for idx, res in enumerate(results):
            node_bindings = res.get("node_bindings", {})
            for k in node_bindings.keys():
                if out_curie in binding_ids(node_bindings[k]):
                    ars_score = None
                    ars_rank = None
                    ara_score = None
                    ara_rank = None
                    if is_ars:
                        ars_score = get_ars_confidence(res)
                        ars_rank = idx + 1
                    else:
                        for anal in res["analyses"]:
                            if "score" in anal.keys():
                                ara_score = anal["score"]
                        ara_rank = idx + 1

                    report[agent].actual_output = {
                        "found": True,
                        "ars_score": ars_score,
                        "ars_rank": ars_rank,
                        "ara_score": ara_score,
                        "ara_rank": ara_rank,
                    }

        if expect_output in ["TopAnswer", "Acceptable"]:
            if out_curie in n_perc_ids:
                report[agent].status = AgentStatus.PASSED
            elif out_curie not in n_perc_ids:
                if out_curie in all_ids:
                    report[agent].status = AgentStatus.FAILED
                else:
                    report[agent].status = AgentStatus.FAILED
                    report[agent].actual_output = dict(not_found_output)

        elif expect_output == "BadButForgivable":
            if out_curie in n_perc_ids:
                report[agent].status = AgentStatus.PASSED
            elif out_curie not in n_perc_ids and out_curie in all_ids:
                report[agent].status = AgentStatus.FAILED
            elif out_curie not in n_perc_ids and out_curie not in all_ids:
                report[agent].status = AgentStatus.PASSED
                report[agent].actual_output = dict(not_found_output)

        elif expect_output == "NeverShow":
            if out_curie in n_perc_ids:
                report[agent].status = AgentStatus.FAILED
            elif out_curie not in all_ids:
                report[agent].status = AgentStatus.PASSED
                report[agent].actual_output = dict(not_found_output)
    except Exception as e:
        report[agent].status = AgentStatus.FAILED
        report[agent].message = f"An exception happened: {type(e), str(e)}"

    return report
