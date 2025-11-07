from typing import Any, Dict, List


def pathfinder_pass_fail_analysis(
    report: Dict[str, Any],
    agent: str,
    message: Dict[str, Any],
    path_nodes: List[List[str]],
    minimum_required_path_nodes: int,
) -> Dict[str, Any]:
    found_path_nodes = set()
    unmatched_paths = set()
    for analysis in message["results"][0]["analyses"]:
        for path_bindings in analysis["path_bindings"].values():
            for path_binding in path_bindings:
                path_id = path_binding["id"]
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
        report[agent]["status"] = "PASSED"
        report[agent]["expected_nodes_found"] = "; ".join(found_path_nodes)
    elif len(unmatched_paths) > 0:
        report[agent]["status"] = "FAILED"
        report[agent]["expected_nodes_found"] = "; ".join(unmatched_paths)
    else:
        report[agent]["status"] = "FAILED"

    return report
