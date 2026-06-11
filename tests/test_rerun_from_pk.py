"""Tests for the rerun_from_pk script (offline / no network)."""

import csv
import json
import logging

from test_harness.rerun_from_pk import (
    _result_confidence,
    compute_ars_status,
    parse_expected_output,
    parse_pk,
    rerun_csv,
    sort_results_by_confidence,
)

logger = logging.getLogger("test.rerun_from_pk")


def _make_result(curie, confidence, oc_level="result"):
    """Build a minimal TRAPI result with a single chemical node binding.

    ``analyses`` is always present (even if empty) so the acceptance runner's
    score-extraction branch doesn't KeyError on the matched result.
    """
    result = {
        "node_bindings": {
            "sn": [{"id": curie}],
            "on": [{"id": "MONDO:0010794"}],
        },
        "analyses": [],
    }
    if oc_level == "result":
        result["ordering_components"] = {"confidence": confidence}
    else:
        result["analyses"] = [{"ordering_components": {"confidence": confidence}}]
    return result


def test_parse_pk():
    assert parse_pk("https://arax.ci.transltr.io/?r=abc-123") == "abc-123"
    assert parse_pk("https://arax.ci.transltr.io/?foo=bar&r=xyz") == "xyz"
    assert parse_pk("") is None
    assert parse_pk("https://arax.ci.transltr.io/") is None


def test_parse_expected_output():
    assert parse_expected_output("TopAnswer: something") == "TopAnswer"
    assert parse_expected_output("Acceptable: foo: bar") == "Acceptable"
    assert parse_expected_output("NeverShow: x") == "NeverShow"
    # not a valid category prefix
    assert parse_expected_output("Valproic_Acid_treats_NARP") is None
    assert parse_expected_output("Bogus: thing") is None


def test_result_confidence_levels():
    # result-level
    assert _result_confidence({"ordering_components": {"confidence": 0.7}}) == 0.7
    # analysis-level (max across analyses)
    assert (
        _result_confidence(
            {
                "analyses": [
                    {"ordering_components": {"confidence": 0.2}},
                    {"ordering_components": {"confidence": 0.8}},
                ]
            }
        )
        == 0.8
    )
    # missing -> sorts last
    assert _result_confidence({"analyses": []}) == float("-inf")


def test_sort_results_by_confidence_descending():
    results = [
        _make_result("CHEBI:A", 0.1),
        _make_result("CHEBI:B", 0.9),
        _make_result("CHEBI:C", 0.5),
    ]
    ordered = sort_results_by_confidence(results)
    ids = [r["node_bindings"]["sn"][0]["id"] for r in ordered]
    assert ids == ["CHEBI:B", "CHEBI:C", "CHEBI:A"]


def test_compute_ars_status_flips_after_resort():
    """The expected curie is in the bottom half by merge order but the top half
    by confidence, so 'Acceptable' should flip FAILED -> PASSED after sorting."""
    out_curie = "CHEBI:OUT"
    results = [
        _make_result("CHEBI:A", 0.1),
        _make_result("CHEBI:B", 0.2),
        _make_result("CHEBI:C", 0.3),
        _make_result("CHEBI:OUT", 0.9),  # last by merge order, highest confidence
    ]

    # Original (merge) order: out_curie is in the bottom 50% -> FAILED
    assert (
        compute_ars_status(results, 200, out_curie, "Acceptable", logger) == "FAILED"
    )
    # After confidence sort: out_curie moves into the top 50% -> PASSED
    assert (
        compute_ars_status(
            sort_results_by_confidence(results), 200, out_curie, "Acceptable", logger
        )
        == "PASSED"
    )


def test_compute_ars_status_guards():
    assert compute_ars_status([], 200, "CHEBI:OUT", "Acceptable", logger) == "NO_RESULTS"
    assert (
        compute_ars_status([_make_result("CHEBI:A", 0.1)], 410, "CHEBI:OUT", "Acceptable", logger)
        == "FAILED"
    )


# --- Full driver test -------------------------------------------------------

SUITE = {
    "id": "TestSuite_T",
    "name": None,
    "description": None,
    "tags": [],
    "test_metadata": {
        "id": "1",
        "test_source": "SMURF",
        "test_objective": "AcceptanceTest",
    },
    "test_cases": {
        "TestCase_1": {
            "id": "TestCase_1",
            "name": "rerun case",
            "test_env": "ci",
            "test_assets": [
                {
                    "id": "Asset_3",
                    "name": "Acceptable: chebi out",
                    "input_id": "MONDO:0010794",
                    "input_category": "biolink:Disease",
                    "predicate_id": "biolink:treats",
                    "output_id": "CHEBI:OUT",
                    "expected_output": "Acceptable",
                    "qualifiers": [],
                },
                {
                    "id": "Asset_4",
                    "name": "Acceptable: chebi c",
                    "input_id": "MONDO:0010794",
                    "input_category": "biolink:Disease",
                    "predicate_id": "biolink:treats",
                    "output_id": "CHEBI:C",
                    "expected_output": "Acceptable",
                    "qualifiers": [],
                },
            ],
            "components": ["ars"],
            "test_case_objective": "AcceptanceTest",
            "test_case_input_id": "MONDO:0010794",
        }
    },
}


def test_rerun_csv_end_to_end(tmp_path, mocker):
    # Avoid network: stub the merged-message fetch and curie normalization.
    results = [
        _make_result("CHEBI:A", 0.1),
        _make_result("CHEBI:B", 0.2),
        _make_result("CHEBI:C", 0.3),
        _make_result("CHEBI:OUT", 0.9),
    ]
    mock_fetch = mocker.patch(
        "test_harness.rerun_from_pk.fetch_merged_message",
        return_value=(results, 200),
    )
    mocker.patch(
        "test_harness.rerun_from_pk.normalize_curies",
        return_value={
            "CHEBI:OUT": "CHEBI:OUT",
            "CHEBI:C": "CHEBI:C",
            "MONDO:0010794": "MONDO:0010794",
        },
    )

    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(SUITE))

    fieldnames = [
        "name",
        "url",
        "pk",
        "TestCase",
        "TestAsset",
        "ars",
        "shepherd-aragorn",
        "shepherd-arax",
        "shepherd-bte",
    ]
    input_csv = tmp_path / "input.csv"
    with open(input_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        # two rows in the suite that SHARE the same pk: ars should flip to PASSED
        # for both, but the merged message should only be fetched once.
        writer.writerow(
            {
                "name": "Acceptable: chebi out",
                "url": "http://dashboard/1",
                "pk": "https://arax.ci.transltr.io/?r=PK123",
                "TestCase": "TestCase_1",
                "TestAsset": "Asset_3",
                "ars": "FAILED",
                "shepherd-aragorn": "PASSED",
                "shepherd-arax": "SKIPPED",
                "shepherd-bte": "NO_RESULTS",
            }
        )
        writer.writerow(
            {
                "name": "Acceptable: chebi c",
                "url": "http://dashboard/1",
                "pk": "https://arax.ci.transltr.io/?r=PK123",
                "TestCase": "TestCase_1",
                "TestAsset": "Asset_4",
                "ars": "FAILED",
                "shepherd-aragorn": "FAILED",
                "shepherd-arax": "PASSED",
                "shepherd-bte": "SKIPPED",
            }
        )
        # row not in the suite: should be carried over unchanged
        writer.writerow(
            {
                "name": "Acceptable: missing",
                "url": "http://dashboard/2",
                "pk": "https://arax.ci.transltr.io/?r=PK999",
                "TestCase": "TestCase_1",
                "TestAsset": "Asset_999",
                "ars": "PASSED",
                "shepherd-aragorn": "FAILED",
                "shepherd-arax": "FAILED",
                "shepherd-bte": "FAILED",
            }
        )

    output_csv = tmp_path / "output.csv"
    rerun_csv(
        input_csv=str(input_csv),
        test_suite_path=str(suite_path),
        output_csv=str(output_csv),
        ars_url="http://fake-ars",
        trapi_version="1.6.0",
        logger=logger,
    )

    with open(output_csv, newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == fieldnames
        out_rows = list(reader)

    assert len(out_rows) == 3
    # the two same-pk rows shared one merged message: only one fetch
    assert mock_fetch.call_count == 1
    # in-suite rows: ars recomputed to PASSED, other agent columns preserved
    assert out_rows[0]["ars"] == "PASSED"
    assert out_rows[0]["shepherd-aragorn"] == "PASSED"
    assert out_rows[0]["shepherd-arax"] == "SKIPPED"
    assert out_rows[0]["shepherd-bte"] == "NO_RESULTS"
    assert out_rows[0]["name"] == "Acceptable: chebi out"
    assert out_rows[1]["ars"] == "PASSED"
    assert out_rows[1]["shepherd-arax"] == "PASSED"
    # not-in-suite row: carried over unchanged
    assert out_rows[2]["ars"] == "PASSED"
    assert out_rows[2]["shepherd-aragorn"] == "FAILED"


def test_rerun_csv_resolves_ars_url_once(tmp_path, mocker):
    """The registry lookup should happen once for the whole run, not per row."""
    results = [_make_result("CHEBI:OUT", 0.9), _make_result("CHEBI:A", 0.1)]
    mocker.patch(
        "test_harness.rerun_from_pk.fetch_merged_message",
        return_value=(results, 200),
    )
    mocker.patch(
        "test_harness.rerun_from_pk.normalize_curies",
        return_value={"CHEBI:OUT": "CHEBI:OUT", "MONDO:0010794": "MONDO:0010794"},
    )
    # env "ci" maps to "staging" in env_map
    mock_registry = mocker.patch(
        "test_harness.rerun_from_pk.retrieve_registry_from_smartapi",
        return_value={"staging": {"ars": [{"url": "http://registry-ars"}]}},
    )

    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(SUITE))

    fieldnames = ["name", "pk", "TestCase", "TestAsset", "ars"]
    input_csv = tmp_path / "input.csv"
    with open(input_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for asset_id in ("Asset_3", "Asset_4"):
            writer.writerow(
                {
                    "name": "Acceptable: x",
                    "pk": "https://arax.ci.transltr.io/?r=PK123",
                    "TestCase": "TestCase_1",
                    "TestAsset": asset_id,
                    "ars": "FAILED",
                }
            )

    output_csv = tmp_path / "output.csv"
    # ars_url=None forces a registry lookup
    rerun_csv(
        input_csv=str(input_csv),
        test_suite_path=str(suite_path),
        output_csv=str(output_csv),
        ars_url=None,
        trapi_version="1.6.0",
        logger=logger,
    )

    assert mock_registry.call_count == 1
