"""Tests for running a single type of query out of a suite (--query_type)."""

import copy

import pytest
from translator_testing_model.datamodel.pydanticmodel import TestSuite

from test_harness.main import main
from test_harness.runner.generate_query import generate_query
from test_harness.utils import filter_tests_by_query_type

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mocks import MockReporter, MockSlacker

logger = setup_logger()


def _asset(asset_id, predicate, input_id, input_category, output_id):
    return {
        "id": asset_id,
        "name": asset_id,
        "description": asset_id,
        "tags": [],
        "test_runner_settings": ["inferred"],
        "input_id": input_id,
        "input_name": input_id,
        "input_category": input_category,
        "predicate_id": predicate,
        "predicate_name": predicate.split(":")[-1],
        "output_id": output_id,
        "output_name": output_id,
        "output_category": None,
        "association": None,
        "qualifiers": [
            {"parameter": "biolink_object_aspect_qualifier", "value": "activity"},
            {"parameter": "biolink_object_direction_qualifier", "value": "increased"},
        ],
        "expected_output": "TopAnswer",
    }


def _mixed_suite():
    """A suite with an MVP1 case, an MVP2 case, and a case holding both."""
    mvp1_asset = _asset(
        "Asset_treats", "biolink:treats", "MONDO:1", "biolink:Disease", "DRUGBANK:1"
    )
    mvp2_asset = _asset(
        "Asset_affects",
        "biolink:affects",
        "CHEBI:2",
        "biolink:ChemicalEntity",
        "NCBIGene:1",
    )

    def case(case_id, assets):
        return {
            "id": case_id,
            "name": case_id,
            "description": case_id,
            "tags": [],
            "test_env": "ci",
            "test_assets": copy.deepcopy(assets),
            "components": ["ars"],
            "test_case_objective": "AcceptanceTest",
            "test_runner_settings": ["inferred"],
        }

    return TestSuite.model_validate(
        {
            "id": "TestSuite_mixed",
            "tags": [],
            "test_cases": {
                "TestCase_mvp1": case("TestCase_mvp1", [mvp1_asset]),
                "TestCase_mvp2": case("TestCase_mvp2", [mvp2_asset]),
                "TestCase_both": case("TestCase_both", [mvp1_asset, mvp2_asset]),
            },
        }
    ).test_cases


def test_no_query_type_runs_everything():
    """Without a query type nothing is filtered out."""
    tests = _mixed_suite()
    assert filter_tests_by_query_type(tests, None, logger) is tests


def test_mvp1_keeps_only_drug_treats_disease():
    """MVP1 keeps the treats assets, including in a case that mixes types."""
    filtered = filter_tests_by_query_type(_mixed_suite(), "MVP1", logger)

    assert set(filtered) == {"TestCase_mvp1", "TestCase_both"}
    for test in filtered.values():
        assert [asset.predicate_id for asset in test.test_assets] == ["biolink:treats"]


def test_mvp2_keeps_only_chemical_affects_gene():
    """MVP2 keeps the affects assets, including in a case that mixes types."""
    filtered = filter_tests_by_query_type(_mixed_suite(), "MVP2", logger)

    assert set(filtered) == {"TestCase_mvp2", "TestCase_both"}
    for test in filtered.values():
        assert [asset.predicate_id for asset in test.test_assets] == ["biolink:affects"]


def test_filtering_leaves_the_original_tests_alone():
    """Filtering copies the test cases it trims, so a caller holding onto the
    unfiltered tests still sees every asset."""
    tests = _mixed_suite()
    filter_tests_by_query_type(tests, "MVP1", logger)

    assert len(tests["TestCase_both"].test_assets) == 2
    assert set(tests) == {"TestCase_mvp1", "TestCase_mvp2", "TestCase_both"}


def test_pathfinder_tests_are_dropped_by_query_type():
    """Pathfinder cases don't generate an MVP query, so asking for one leaves
    them out (the fixture's pathfinder case has neither predicate)."""
    filtered = filter_tests_by_query_type(example_test_cases, "MVP1", logger)

    assert set(filtered) == {"TestCase_1"}
    assert len(filtered["TestCase_1"].test_assets) == 2

    assert filter_tests_by_query_type(example_test_cases, "MVP2", logger) == {}


def test_unknown_query_type_is_rejected():
    with pytest.raises(ValueError, match="MVP1"):
        filter_tests_by_query_type(_mixed_suite(), "MVP3", logger)


def test_filtered_assets_generate_the_expected_query_type():
    """The filter and the query templates agree on what MVP1/MVP2 mean."""
    mvp1 = filter_tests_by_query_type(_mixed_suite(), "MVP1", logger)
    query = generate_query(mvp1["TestCase_mvp1"].test_assets[0])
    edge = query["message"]["query_graph"]["edges"]["t_edge"]
    assert edge["predicates"] == ["biolink:treats"]
    assert query["message"]["query_graph"]["nodes"]["ON"]["ids"] == ["MONDO:1"]

    mvp2 = filter_tests_by_query_type(_mixed_suite(), "MVP2", logger)
    query = generate_query(mvp2["TestCase_mvp2"].test_assets[0])
    edge = query["message"]["query_graph"]["edges"]["t_edge"]
    assert edge["predicates"] == ["biolink:affects"]
    assert query["message"]["query_graph"]["nodes"]["SN"]["ids"] == ["CHEBI:2"]


def test_main_runs_only_the_requested_query_type(mocker):
    """--query_type reaches run_tests as an already-filtered set of tests."""
    run_tests = mocker.patch("test_harness.main.run_tests", return_value={})
    mocker.patch("test_harness.main.Slacker", return_value=MockSlacker())
    mocker.patch("test_harness.main.Reporter", return_value=MockReporter())

    main(
        {
            "tests": _mixed_suite(),
            "suite": "testing",
            "query_type": "MVP2",
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
        }
    )

    tests = run_tests.call_args.args[0]
    assert set(tests) == {"TestCase_mvp2", "TestCase_both"}
    for test in tests.values():
        assert [asset.predicate_id for asset in test.test_assets] == ["biolink:affects"]


def test_main_without_query_type_runs_every_test(mocker):
    run_tests = mocker.patch("test_harness.main.run_tests", return_value={})
    mocker.patch("test_harness.main.Slacker", return_value=MockSlacker())
    mocker.patch("test_harness.main.Reporter", return_value=MockReporter())

    main(
        {
            "tests": _mixed_suite(),
            "suite": "testing",
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
        }
    )

    tests = run_tests.call_args.args[0]
    assert set(tests) == {"TestCase_mvp1", "TestCase_mvp2", "TestCase_both"}
