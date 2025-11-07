"""Acceptance Test Pass Fail Analysis Runner."""

from typing import Any, Dict, List


def run_acceptance_pass_fail_analysis(
    report: Dict[str, Any],
    agent: str,
    results: List[Dict[str, Any]],
    out_curie: str,
    expect_output: str,
):
    """ "Function to run pass fail analysis on individual results."""
    # get the top_n result's ids
    try:
        all_ids = []
        for res in results:
            for res_node, res_value in res["node_bindings"].items():
                for val in res_value:
                    ids = str(val["id"])
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
                for val in res_value:
                    ids = str(val["id"])
                    if ids not in n_perc_ids:
                        n_perc_ids.append(ids)
        # get the sugeno score & rank
        for idx, res in enumerate(results):
            node_bindings = res.get("node_bindings", {})
            for k in node_bindings.keys():
                nb = node_bindings[k]
                the_id = None
                for c in nb:
                    the_id = c.get("id")
                if the_id == out_curie:
                    if "sugeno" in res.keys() and "rank" in res.keys():
                        ars_score = res["sugeno"]
                        ars_rank = res["rank"]
                        ara_score = None
                        ara_rank = None
                    else:
                        ars_score = None
                        ars_rank = None
                        for anal in res["analyses"]:
                            if "score" in anal.keys():
                                ara_score = anal["score"]
                            else:
                                ara_score = None
                        ara_rank = idx + 1

                    report[agent]["actual_output"] = {}
                    if ars_score is not None and ars_rank is not None:
                        report[agent]["actual_output"]["ars_score"] = ars_score
                        report[agent]["actual_output"]["ars_rank"] = ars_rank

                    if ara_score is not None and ara_rank is not None:
                        report[agent]["actual_output"]["ara_score"] = ara_score
                        report[agent]["actual_output"]["ara_rank"] = ara_rank

        if expect_output in ["TopAnswer", "Acceptable"]:
            if out_curie in n_perc_ids:
                report[agent]["status"] = "PASSED"
            elif out_curie not in n_perc_ids:
                if out_curie in all_ids:
                    report[agent]["status"] = "FAILED"
                else:
                    report[agent]["status"] = "FAILED"
                    report[agent]["actual_output"] = {}
                    if agent == "ars":
                        report[agent]["actual_output"]["ars_score"] = None
                        report[agent]["actual_output"]["ars_rank"] = None
                    else:
                        report[agent]["actual_output"]["ara_score"] = None
                        report[agent]["actual_output"]["ara_rank"] = None

        elif expect_output == "BadButForgivable":
            if out_curie in n_perc_ids:
                report[agent]["status"] = "PASSED"
            elif out_curie not in n_perc_ids and out_curie in all_ids:
                report[agent]["status"] = "FAILED"
            elif out_curie not in n_perc_ids and out_curie not in all_ids:
                report[agent]["status"] = "PASSED"
                report[agent]["actual_output"] = {}
                if agent == "ars":
                    report[agent]["actual_output"]["ars_score"] = None
                    report[agent]["actual_output"]["ars_rank"] = None
                else:
                    report[agent]["actual_output"]["ara_score"] = None
                    report[agent]["actual_output"]["ara_rank"] = None

        elif expect_output == "NeverShow":
            if out_curie in n_perc_ids:
                report[agent]["status"] = "FAILED"
            elif out_curie not in all_ids:
                report[agent]["status"] = "PASSED"
                report[agent]["actual_output"] = {}
                if agent == "ars":
                    report[agent]["actual_output"]["ars_score"] = None
                    report[agent]["actual_output"]["ars_rank"] = None
                else:
                    report[agent]["actual_output"]["ara_score"] = None
                    report[agent]["actual_output"]["ara_rank"] = None
    except Exception as e:
        report[agent]["status"] = "FAILED"
        report[agent]["message"] = f"An exception happened: {type(e), str(e)}"

    return report
