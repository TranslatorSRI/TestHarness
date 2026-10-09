"""Query parameters: given once, sent with every query."""

import json
import os

import pytest
from pytest_httpx import HTTPXMock
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    TestAsset,
)

from radiator_schema import RunPayload
from test_harness.main import _query_parameters, main
from test_harness.run import run_tests
from test_harness.runner.generate_query import generate_query
from test_harness.trapi import parse_query_parameters, validate_query_parameters

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mock_responses import kp_response
from .helpers.mocks import MockReporter, MockResultCollector

logger = setup_logger()

ONE_HOP = TestAsset.model_validate(
    {
        "id": "Asset_1",
        "input_id": "MONDO:0004979",
        "input_category": "biolink:Disease",
        "predicate_id": "biolink:treats",
        "output_id": "CHEBI:31690",
        "expected_output": "TopAnswer",
        "qualifiers": [],
    }
)


def test_parse_json_and_files(tmp_path):
    assert parse_query_parameters('{"timeout": 300}') == {"timeout": 300}
    assert parse_query_parameters("") is None
    assert parse_query_parameters(None) is None
    path = tmp_path / "params.json"
    path.write_text('{"bypass_cache": true}')
    assert parse_query_parameters(f"@{path}") == {"bypass_cache": True}
    with pytest.raises(ValueError, match="valid JSON"):
        parse_query_parameters("{timeout: 300}")
    with pytest.raises(ValueError, match="JSON object"):
        parse_query_parameters("[1, 2]")


def test_validation_trapi_2():
    params = {"timeout": 300, "log_level": "DEBUG", "my_service_flag": "x"}
    # the standard ones are checked; a service's own ones pass through
    assert validate_query_parameters(params, "2.0.0") == {
        "timeout": 300.0,
        "log_level": "DEBUG",
        "my_service_flag": "x",
    }
    with pytest.raises(ValueError, match="timeout"):
        validate_query_parameters({"timeout": "soon"}, "2.0.0")
    with pytest.raises(ValueError, match="log_level"):
        validate_query_parameters({"log_level": "LOUD"}, "2.0.0")


def test_validation_trapi_1_6():
    assert validate_query_parameters({"bypass_cache": True}, "1.6.0") == {
        "bypass_cache": True
    }
    with pytest.raises(ValueError, match="can't carry timeout"):
        validate_query_parameters({"timeout": 30}, "1.6.0")


def test_queries_carry_the_parameters():
    params = {"timeout": 300.0, "bypass_cache": True}
    query = generate_query(ONE_HOP, "2.0.0", params)
    assert query["parameters"] == params
    assert "timeout" not in query

    legacy = generate_query(ONE_HOP, "1.6.0", {"bypass_cache": True})
    assert legacy["bypass_cache"] is True
    assert "parameters" not in legacy

    pathfinder = PathfinderTestAsset.model_validate(
        {
            "id": "Asset_2",
            "source_input_id": "MONDO:1",
            "target_input_id": "CHEBI:2",
            "predicate_id": "biolink:related_to",
            "expected_output": "TopAnswer",
            "path_nodes": [],
            "minimum_required_path_nodes": 1,
        }
    )
    assert generate_query(pathfinder, "2.0.0", params)["parameters"] == params
    # none given: the query is unchanged
    assert "parameters" not in generate_query(ONE_HOP, "2.0.0")


def test_every_query_sent_carries_them(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://localhost:8080/query", json=kp_response)
    httpx_mock.add_response(
        url="https://nodenorm-es.ci.transltr.io/get_normalized_nodes",
        # nothing normalizes, so every curie is kept as is
        json={
            curie: None
            for curie in [
                "MONDO:0010794",
                "DRUGBANK:DB00313",
                "MESH:D001463",
                "CHEBI:18295",
                "CHEBI:31690",
                "CL:0000097",
                "MONDO:0004979",
                "NCBIGene:3815",
                "NCBIGene:4254",
                "PR:000049994",
            ]
        },
    )
    run_tests(
        tests=example_test_cases,
        reporter=MockReporter(base_url="http://test"),
        collector=MockResultCollector("ci", logger, target="aragorn"),
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "2.0.0",
            "target_url": "http://localhost:8080",
            "target": "aragorn",
            "query_parameters": {"timeout": 300.0},
        },
    )
    sent = [
        json.loads(r.content)
        for r in httpx_mock.get_requests()
        if r.url.path == "/query"
    ]
    assert sent and all(q["parameters"] == {"timeout": 300.0} for q in sent)


def _main_args(tmp_path, **extra):
    return {
        "tests": example_test_cases,
        "suite": "testing",
        "save_to_dashboard": False,
        "json_output": False,
        "log_level": "ERROR",
        "local": True,
        "output_dir": str(tmp_path),
        **extra,
    }


def test_main_refuses_bad_parameters_before_running(mocker, tmp_path):
    run = mocker.patch("test_harness.main.run_tests")
    code = main(
        _main_args(tmp_path, trapi_version="1.6.0", query_parameters={"timeout": 30})
    )
    assert code == 1
    run.assert_not_called()


def test_main_sends_and_records_them(mocker, tmp_path):
    """The run's parameters reach the queries and the radiator's record."""
    run = mocker.patch("test_harness.main.run_tests")
    assert main(_main_args(tmp_path, query_parameters={"timeout": 300})) == 0
    assert run.call_args.args[4]["query_parameters"] == {"timeout": 300.0}
    [saved] = [p for p in os.listdir(tmp_path) if p.startswith("radiator_")]
    with open(tmp_path / saved) as f:
        payload = RunPayload.model_validate(json.load(f))
    assert payload.run.query_parameters == {"timeout": 300.0}


def test_cli_flag_and_environment(monkeypatch):
    assert _query_parameters('{"timeout": 5}') == {"timeout": 5}
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        _query_parameters("not json")
