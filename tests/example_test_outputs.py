"""
Example output for specified tests
"""

example_one_hops_test_output = {
    "pks": [
        "arax",
        "molepro"
    ],
    "results": [
        [
            {
                "arax": {
                    "by_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "arax": {
                    "inverse_by_new_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {},
                        "critical": {
                            "critical.trapi.response.unexpected_http_code": {
                                "global": {
                                    "400": None
                                }
                            }
                        }
                    }
                }
            },
            {
                "arax": {
                    "by_object": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "arax": {
                    "raise_subject_entity": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {},
                        "critical": {
                            "critical.trapi.request.invalid": {
                                "global": {
                                    "subject 'DRUGBANK:DB01592[biolink:SmallMolecule]'": [
                                        {
                                            "context": "raise_subject_entity",
                                            "reason": "has no 'is_a' parent since it is either not an ontology term or does not map onto a parent ontology term."
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            {
                "arax": {
                    "raise_object_entity": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "arax": {
                    "raise_object_by_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "arax": {
                    "raise_predicate_by_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            }
        ],
        [
            {
                "molepro": {
                    "by_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "molepro": {
                    "inverse_by_new_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "molepro": {
                    "by_object": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "molepro": {
                    "raise_subject_entity": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {},
                        "critical": {
                            "critical.trapi.request.invalid": {
                                "global": {
                                    "subject 'DRUGBANK:DB01592[biolink:SmallMolecule]'": [
                                        {
                                            "context": "raise_subject_entity",
                                            "reason": "has no 'is_a' parent since it is either not an ontology term or does not map onto a parent ontology term."
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            {
                "molepro": {
                    "raise_object_entity": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "molepro": {
                    "raise_object_by_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            },
            {
                "molepro": {
                    "raise_predicate_by_subject": {
                        "info": {},
                        "skipped": {},
                        "warning": {},
                        "error": {
                            "error.trapi.response.knowledge_graph.missing_expected_edge": {
                                "global": {
                                    "TestAsset:00001|(DRUGBANK:DB01592#biolink:SmallMolecule)-[biolink:has_side_effect]->(MONDO:0011426#biolink:Disease)": None
                                }
                            }
                        },
                        "critical": {}
                    }
                }
            }
        ]
    ]
}