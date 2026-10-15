"""Mock Test Responses, in TRAPI 2.0."""

kp_response = {
    "message": {
        "query_graph": {
            "nodes": {
                "n0": {"ids": ["MESH:D008687"]},
                "n1": {"categories": ["biolink:Disease"]},
            },
            "edges": {
                "n0n1": {
                    "subject": "n0",
                    "object": "n1",
                    "predicates": ["biolink:treats"],
                }
            },
        },
        "knowledge_graph": {
            "nodes": {
                "MESH:D008687": {
                    "categories": ["biolink:SmallMolecule"],
                    "name": "Metformin",
                    "attributes": [],
                },
                "MONDO:0005148": {
                    "categories": [
                        "biolink:Disease",
                    ],
                    "name": "type 2 diabetes mellitus",
                    "attributes": [],
                },
            },
            "edges": {
                "n0n1": {
                    "subject": "MESH:D008687",
                    "object": "MONDO:0005148",
                    "predicate": "biolink:treats",
                    "knowledge_level": "knowledge_assertion",
                    "agent_type": "manual_agent",
                    "sources": [
                        {
                            "resource_id": "infores:kp0",
                            "resource_role": "primary_knowledge_source",
                        }
                    ],
                    "attributes": [],
                },
            },
        },
        "results": [
            {
                "node_bindings": {
                    "n0": {"ids": ["MESH:D008687"]},
                    "n1": {"ids": ["MONDO:0005148"]},
                },
                "analyses": [
                    {
                        "resource_id": "infores:kp0",
                        "edge_bindings": {"n0n1": {"ids": ["n0n1"]}},
                    }
                ],
            },
        ],
    },
}
