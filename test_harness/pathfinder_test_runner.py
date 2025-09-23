from typing import Dict, Union, List

async def pass_fail_analysis(
        report: Dict[str, any],
        agent: str,
        message: Dict[str, any],
        path_curies: List[str]
) -> Dict[str, any]:
    found_path_nodes = set()
    for analysis in message["results"][0]["analyses"]:
        for path_bindings in analysis["path_bindings"].values():
            for path_binding in path_bindings:
                path_id = path_binding["id"]
                for edge_id in message["auxiliary_graphs"][path_id]["edges"]:
                    edge = message["knowledge_graph"]["edges"][edge_id]
                    if edge["subject"] in path_curies:
                        found_path_nodes.add(edge["subject"])
                    elif edge["object"] in path_curies:
                        found_path_nodes.add(edge["object"])
    if len(found_path_nodes) > 0:
        report[agent]["status"] = "PASSED"
        report[agent]["expected_path_nodes"] = found_path_nodes
    else:
        report[agent]["status"] = "FAILED"
            
    return report