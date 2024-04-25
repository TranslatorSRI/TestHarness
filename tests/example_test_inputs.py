"""Example tests for the Test Harness."""

from translator_testing_model.datamodel.pydanticmodel import TestSuite, ComponentEnum

example_acceptance_test_cases = TestSuite.model_validate(
    {
        "id": "TestSuite_1",
        "name": None,
        "description": None,
        "tags": [],
        "test_runner_settings": ["inferred"],
        "test_metadata": {
            "id": "1",
            "name": None,
            "description": None,
            "tags": [],
            "test_source": "SMURF",
            "test_reference": None,
            "test_objective": "AcceptanceTest",
            "test_annotations": [],
        },
        "test_cases": {
            "TestCase_1": {
                "id": "TestCase_1",
                "name": "what treats MONDO:0010794",
                "description": "Valproic_Acid_treats_NARP_Syndrome; Barbiturates_treats_NARP_Syndrome",
                "tags": [],
                "test_env": "ci",
                "query_type": None,
                "test_assets": [
                    {
                        "id": "Asset_3",
                        "name": "Valproic_Acid_treats_NARP_Syndrome",
                        "description": "Valproic_Acid_treats_NARP_Syndrome",
                        "tags": [],
                        "input_id": "MONDO:0010794",
                        "input_name": "NARP Syndrome",
                        "input_category": None,
                        "predicate_id": "biolink:treats",
                        "predicate_name": "treats",
                        "output_id": "DRUGBANK:DB00313",
                        "output_name": "Valproic Acid",
                        "output_category": None,
                        "association": None,
                        "qualifiers": [],
                        "expected_output": "NeverShow",
                        "test_issue": None,
                        "semantic_severity": None,
                        "in_v1": None,
                        "well_known": False,
                        "test_reference": None,
                        "test_runner_settings": ["inferred"],
                        "test_metadata": {
                            "id": "1",
                            "name": None,
                            "description": None,
                            "tags": [],
                            "test_source": "SMURF",
                            "test_reference": "https://github.com/NCATSTranslator/Feedback/issues/147",
                            "test_objective": "AcceptanceTest",
                            "test_annotations": [],
                        },
                    },
                    {
                        "id": "Asset_4",
                        "name": "Barbiturates_treats_NARP_Syndrome",
                        "description": "Barbiturates_treats_NARP_Syndrome",
                        "tags": [],
                        "input_id": "MONDO:0010794",
                        "input_name": "NARP Syndrome",
                        "input_category": None,
                        "predicate_id": "biolink:treats",
                        "predicate_name": "treats",
                        "output_id": "MESH:D001463",
                        "output_name": "Barbiturates",
                        "output_category": None,
                        "association": None,
                        "qualifiers": [],
                        "expected_output": "NeverShow",
                        "test_issue": None,
                        "semantic_severity": None,
                        "in_v1": None,
                        "well_known": False,
                        "test_reference": None,
                        "test_runner_settings": ["inferred"],
                        "test_metadata": {
                            "id": "1",
                            "name": None,
                            "description": None,
                            "tags": [],
                            "test_source": "SMURF",
                            "test_reference": "https://github.com/NCATSTranslator/Feedback/issues/147",
                            "test_objective": "AcceptanceTest",
                            "test_annotations": [],
                        },
                    },
                ],
                "preconditions": [],
                "trapi_template": None,
                "components": [ComponentEnum("ars")],
                "test_case_objective": "AcceptanceTest",
                "test_case_source": None,
                "test_case_predicate_name": "treats",
                "test_case_predicate_id": "biolink:treats",
                "test_case_input_id": "MONDO:0010794",
                "test_runner_settings": ["inferred"],
            }
        }
    }
).test_cases


example_one_hop_test_cases = TestSuite.model_validate(
    {
        "id": "TestSuite_2",
        "name": None,
        "description": None,
        "tags": [],
        "test_runner_settings": None,
        "test_metadata": {
            "id": "1",
            "name": None,
            "description": None,
            "tags": [],
            "test_source": "TranslatorTeam",
            "test_reference": None,
            "test_objective": "OneHopTest",
            "test_annotations": [],
        },
        "test_cases": {
            "TestCase_1": {
                "id": "TestCase_1",
                "name": "Metformin affects MTOR",
                "description": "Metformin affects the MTOR mechanistic target of rapamycin kinase",
                "tags": [],
                "test_env": "prod",
                # "trapi_version": trapi_version,  # Optional[str] = None; latest community release if not given
                # "biolink_version": biolink_version,  # Optional[str] = None;  Biolink Toolkit default if not given
                "query_type": None,
                "test_assets": [
                    {
                        "id": "TestAsset_1",
                        "name": "Metformin affects MTOR",
                        "description": "Metformin affects the MTOR mechanistic target of rapamycin kinase",
                        "tags": [],
                        "input_id": "PUBCHEM.COMPOUND:4091",
                        "input_name": "Metformin",
                        "input_category": "biolink:SmallMolecule",
                        "predicate_id": "biolink:affects",
                        "predicate_name": "affects",
                        "output_id": "NCBIGene:2475",
                        "output_name": "MTOR mechanistic target of rapamycin kinase",
                        "output_category": "biolink:Gene",
                        "association": "biolink:ChemicalGeneInteractionAssociation",
                        "qualifiers": [],
                        "expected_output": "Acceptable",
                        "test_issue": None,
                        "semantic_severity": None,
                        "in_v1": None,
                        "well_known": False,
                        "test_reference": None,
                        "test_runner_settings": None,
                        "test_metadata": {
                            "id": "1",
                            "name": None,
                            "description": None,
                            "tags": [],
                            "test_source": "TranslatorTeam",
                            "test_reference": None,
                            "test_objective": "OneHopTest",
                            "test_annotations": [],
                        },
                    },
                ],
                "preconditions": [],
                "trapi_template": None,
                "components": [ComponentEnum("molepro")],
                "test_case_objective": "OneHopTest",
                "test_case_source": None,
                "test_case_predicate_name": "affects",
                "test_case_predicate_id": "biolink:affects",
                "test_case_input_id": "PUBCHEM.COMPOUND:4091",
                "test_runner_settings": None,
            }
        }
    }
).test_cases
