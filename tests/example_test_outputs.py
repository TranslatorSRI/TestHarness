"""
Example output for specified tests
"""

example_one_hops_test_output = {
    "pks": {
        "arax": "arax",
        "molepro": "molepro"
    },
    "results": {
        "TestAsset_1-by_subject": {
            "molepro": {
                "status": "PASSED",
                "messages": {
                    "info": {
                        "info.compliant": {
                            "global": None
                        }
                    }
                }
            }
        },
        "TestAsset_1-inverse_by_new_subject": {
            "molepro": {
                "status": "FAILED",
                "messages": {
                    "critical": {
                        "critical.trapi.request.invalid": {
                            "global": {
                                "predicate 'biolink:is_active_metabolite_of'": [
                                    {
                                        "context": "inverse_by_new_subject",
                                        "reason": "is an unknown or has no inverse?"
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        },
        "TestAsset_1-by_object": {
            "molepro": {
                "status": "FAILED",
                "messages": {
                    "error": {
                        "error.trapi.response.knowledge_graph.missing_expected_edge": {
                            "global": {
                                "TestAsset_1|(CHEBI:58579#biolink:SmallMolecule)-[biolink:is_active_metabolite_of]->(UniProtKB:Q9NQ88#biolink:Protein)": None
                            }
                        }
                    }
                }
            }
        },
        "TestAsset_1-raise_subject_entity": {
            "molepro": {
                "status": "FAILED",
                "messages": {
                    "critical": {
                        "critical.trapi.request.invalid": {
                            "global": {
                                "subject 'CHEBI:58579[biolink:SmallMolecule]'": [
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
        "TestAsset_1-raise_object_entity": {
            "molepro": {
                "status": "FAILED",
                "messages": {
                    "critical": {
                        "critical.trapi.request.invalid": {
                            "global": {
                                "object 'UniProtKB:Q9NQ88[biolink:Protein]'": [
                                    {
                                        "context": "raise_object_entity",
                                        "reason": "has no 'is_a' parent since it is either not an ontology term or does not map onto a parent ontology term."
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        },
        "TestAsset_1-raise_object_by_subject": {
            "molepro": {
                "status": "FAILED",
                "messages": {
                    "error": {
                        "error.trapi.response.knowledge_graph.missing_expected_edge": {
                            "global": {
                                "TestAsset_1|(CHEBI:58579#biolink:SmallMolecule)-[biolink:is_active_metabolite_of]->(UniProtKB:Q9NQ88#biolink:Protein)": None
                            }
                        }
                    }
                }
            }
        },
        "TestAsset_1-raise_predicate_by_subject": {
            "molepro": {
                "status": "FAILED",
                "messages": {
                    "critical": {
                        "critical.trapi.request.invalid": {
                            "global": {
                                "predicate 'biolink:is_active_metabolite_of'": [
                                    {
                                        "context": "raise_predicate_by_subject",
                                        "reason": "has no 'is_a' parent"
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }
    }
}
