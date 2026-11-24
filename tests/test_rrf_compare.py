"""Comparing the confidence ranking of ARS results with their RRF ranking."""

import csv
import json

import httpx
import pytest

from test_harness import rrf_compare
from test_harness.rrf_compare import compare_asset, compute_rrf

ARAGORN = "infores:shepherd-aragorn"
ARAX = "infores:shepherd-arax"
WEIGHTS = {ARAGORN: 0.9, ARAX: 0.1}


def result(chem, disease="MONDO:1", confidence=None, rrf_score=None, trapi2=False):
    bindings = (
        {"sn": {"ids": [chem]}, "on": {"ids": [disease]}}
        if trapi2
        else {"sn": [{"id": chem}], "on": [{"id": disease}]}
    )
    res = {"node_bindings": bindings, "analyses": []}
    if confidence is not None:
        res["ordering_components"] = {"confidence": confidence}
    if rrf_score is not None:
        res["rrf_score"] = rrf_score
    return res


def chems(results):
    return [rrf_compare.result_key(r) - {"MONDO:1"} for r in results]


# The cases from Relay's tr_sys/tests/unit/test_ranker_fusion.py, which the
# computed RRF has to agree with.


def test_rrf_sorts_results_and_adds_metadata():
    a, b, c = result("CHEBI:1"), result("CHEBI:2"), result("CHEBI:3")
    ordered = compute_rrf(
        [a, b, c], {ARAGORN: [a, b], ARAX: [b, c]}, {ARAGORN: 0.5, ARAX: 0.5}, 60
    )
    assert chems(ordered) == [{"CHEBI:2"}, {"CHEBI:1"}, {"CHEBI:3"}]
    assert ordered[0]["rrf_rank"] == 1
    assert ordered[0]["rrf_ranker_ranks"] == {ARAGORN: 2, ARAX: 1}
    assert ordered[0]["rrf_score"] == pytest.approx(0.5 / 62 + 0.5 / 61)
    assert ordered[0]["rrf_score"] > ordered[1]["rrf_score"]


def test_rrf_keeps_unmatched_results_at_end_stably():
    a, b, c = result("CHEBI:1"), result("CHEBI:2"), result("CHEBI:3")
    ordered = compute_rrf([c, b, a], {ARAGORN: [a]}, {ARAGORN: 1.0}, 60)
    assert chems(ordered) == [{"CHEBI:1"}, {"CHEBI:3"}, {"CHEBI:2"}]
    assert ordered[1]["rrf_score"] == ordered[2]["rrf_score"] == 0.0


def test_rrf_tied_scores_share_rank():
    a, b = result("CHEBI:1"), result("CHEBI:2")
    ordered = compute_rrf(
        [b, a], {ARAGORN: [a, b], ARAX: [b, a]}, {ARAGORN: 0.5, ARAX: 0.5}, 60
    )
    assert chems(ordered) == [{"CHEBI:2"}, {"CHEBI:1"}]
    assert [r["rrf_rank"] for r in ordered] == [1, 1]


def test_rrf_noops_without_matches():
    a = result("CHEBI:1")
    assert compute_rrf([a], {"infores:other": [a]}, {ARAGORN: 1.0}, 60) is None
    assert compute_rrf([a], {ARAGORN: [result("CHEBI:9")]}, WEIGHTS, 40) is None


def test_rrf_reads_trapi_2_bindings():
    a, b = result("CHEBI:1", trapi2=True), result("CHEBI:2", trapi2=True)
    ordered = compute_rrf([a, b], {ARAGORN: [b, a]}, WEIGHTS, 40)
    assert chems(ordered) == [{"CHEBI:2"}, {"CHEBI:1"}]
    assert ordered[0]["rrf_score"] == pytest.approx(0.9 / 41)


def test_rrf_first_duplicate_keeps_its_rank():
    a = result("CHEBI:1")
    ranks = rrf_compare.rank_map([result("CHEBI:2"), a, dict(a)])
    assert ranks[rrf_compare.result_key(a)] == 2


# --- comparing one asset ------------------------------------------------------


def asset(expected="TopAnswer", outputs=("CHEBI:3",)):
    return {
        "test_case_id": "TestCase_1",
        "asset_id": "Asset_1",
        "expected_output": expected,
        "output_curies": list(outputs),
        "agents": [],
    }


def test_compare_uses_the_ars_rrf_when_there_is_one():
    # confidence puts CHEBI:3 last of 40; RRF puts it first
    results = [
        result(f"CHEBI:{i}", confidence=1 - i / 100, rrf_score=0.001)
        for i in range(4, 44)
    ] + [result("CHEBI:3", confidence=0.001, rrf_score=0.5)]
    row = compare_asset(asset(), results, ["CHEBI:3"], None, WEIGHTS, 40)
    assert row["rrf_source"] == "ars"
    assert row["confidence_rank"] == 41
    assert row["confidence_status"] == "FAILED"
    assert row["rrf_rank"] == 1
    assert row["rrf_score"] == 0.5
    assert row["rrf_status"] == "PASSED"
    assert row["rank_change"] == 40
    assert row["status_change"] == "FAILED->PASSED"
    assert row["found_curie"] == "CHEBI:3"


def test_compare_computes_the_rrf_without_one_from_the_ars():
    a = result("CHEBI:1", confidence=0.9)
    b = result("CHEBI:3", confidence=0.1)
    row = compare_asset(
        asset(), [a, b], ["CHEBI:3"], {ARAGORN: [b, a], ARAX: [a, b]}, WEIGHTS, 40
    )
    assert row["rrf_source"] == "computed"
    assert (row["confidence_rank"], row["rrf_rank"]) == (2, 1)
    assert row["rrf_ranker_ranks"] == "shepherd-aragorn=1;shepherd-arax=2"
    assert row["rank_change"] == 1
    assert "status_change" not in row  # both pass: top 30


def test_compare_recompute_ignores_the_ars_rrf():
    a = result("CHEBI:1", confidence=0.9, rrf_score=0.9)
    b = result("CHEBI:3", confidence=0.1, rrf_score=0.1)
    row = compare_asset(
        asset(), [a, b], ["CHEBI:3"], {ARAGORN: [b, a]}, WEIGHTS, 40, recompute=True
    )
    assert row["rrf_source"] == "computed"
    assert row["rrf_rank"] == 1


def test_compare_without_any_rrf_still_reports_confidence():
    a = result("CHEBI:3", confidence=0.9)
    row = compare_asset(asset(), [a], ["CHEBI:3"], {}, WEIGHTS, 40)
    assert row["rrf_source"] == "unavailable"
    assert row["confidence_status"] == "PASSED"
    assert "rrf_status" not in row


def test_compare_never_show():
    results = [result("CHEBI:1", confidence=0.9, rrf_score=0.1)]
    row = compare_asset(
        asset("NeverShow", ("CHEBI:9",)), results, ["CHEBI:9"], None, WEIGHTS, 40
    )
    assert row["confidence_status"] == row["rrf_status"] == "PASSED"
    assert row["confidence_rank"] is None and "rrf_rank" not in row


# --- end to end, against a fake radiator, ARS and node normalizer ------------


def fake_services(merged_has_rrf=True):
    run = {
        "run_id": "run-2",
        "suite": "mvp_tests",
        "env": "ci",
        "origin": None,
        "started_at": "2026-11-20T00:00:00",
        "ended_at": "2026-11-20T01:00:00",
    }
    full = {
        **run,
        "results": [
            {
                "test_case_id": "TestCase_1",
                "asset_id": "Asset_1",
                "name": "drug treats disease",
                "kind": "acceptance",
                "expected_output": "TopAnswer",
                "input_curie": "MONDO:1",
                "output_curies": ["DRUGBANK:3"],
                "parent_pk": "parent-1",
                "agents": [
                    {"agent": "ars", "pk": "merged-1", "status": "PASSED", "rank": 2},
                    {"agent": "shepherd-aragorn", "pk": "aragorn-1"},
                    {"agent": "shepherd-arax", "pk": "arax-1"},
                ],
            },
            {
                "test_case_id": "TestCase_2",
                "asset_id": "Asset_2",
                "kind": "acceptance",
                "expected_output": "TopAnswer",
                "output_curies": ["CHEBI:7"],
                "agents": [{"agent": "ars", "pk": "None"}],
            },
            {
                "test_case_id": "TestCase_3",
                "asset_id": "Asset_3",
                "kind": "pathfinder",
                "expected_output": "TopAnswer",
                "agents": [{"agent": "ars", "pk": "merged-3"}],
            },
        ],
    }
    a = result("CHEBI:1", confidence=0.9, rrf_score=0.01 if merged_has_rrf else None)
    b = result("CHEBI:3", confidence=0.5, rrf_score=0.02 if merged_has_rrf else None)
    messages = {
        "merged-1": [a, b],
        "aragorn-1": [result("CHEBI:3"), result("CHEBI:1")],
        "arax-1": [result("CHEBI:3"), result("CHEBI:1")],
    }
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        path = request.url.path
        if path == "/api/runs":
            return httpx.Response(
                200, json=[{**run, "run_id": "run-3", "ended_at": None}, run]
            )
        if path == "/api/runs/run-2":
            return httpx.Response(200, json=full)
        if path.startswith("/ars/api/messages/"):
            pk = path.rsplit("/", 1)[1]
            return httpx.Response(
                200,
                json={
                    "fields": {
                        "status": "Done",
                        "data": {"message": {"results": messages[pk]}},
                    }
                },
            )
        if path == "/get_normalized_nodes":
            curies = json.loads(request.content)["curies"]
            return httpx.Response(
                200,
                json={
                    c: {"id": {"identifier": "CHEBI:3" if c == "DRUGBANK:3" else c}}
                    for c in curies
                },
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


@pytest.mark.parametrize("merged_has_rrf", [True, False])
def test_compare_run(merged_has_rrf):
    client, seen = fake_services(merged_has_rrf)
    run = rrf_compare.find_run(client, "http://radiator", None, None, "ci")
    assert run["run_id"] == "run-2"  # run-3 hasn't finished
    rows = rrf_compare.compare_run(
        run, "http://ars", "ci", WEIGHTS, 40, client=client, workers=1
    )
    assert len(rows) == 2  # the pathfinder asset isn't compared
    row, missing = rows
    assert row["rrf_source"] == ("ars" if merged_has_rrf else "computed")
    assert (row["confidence_rank"], row["rrf_rank"]) == (2, 1)
    assert row["found_curie"] == "CHEBI:3"  # found under its normalized id
    assert (row["radiator_status"], row["radiator_rank"]) == ("PASSED", 2)
    assert missing["error"] == "no ARS merged pk in the radiator"
    # the ARA responses are only fetched when the RRF is computed here
    assert ("/ars/api/messages/aragorn-1" in seen) == (not merged_has_rrf)
    report = rrf_compare.summary(rows)
    assert "RRF ranks the expected answer higher for 1" in report


def test_cli_writes_csv(monkeypatch, tmp_path, capsys):
    client, _ = fake_services()
    monkeypatch.setattr(rrf_compare.httpx, "Client", lambda **kw: client)
    out = tmp_path / "out.csv"
    rrf_compare.cli(["--radiator-url", "http://radiator", "--out", str(out)])
    with open(out) as f:
        rows = list(csv.DictReader(f))
    assert [r["asset_id"] for r in rows] == ["Asset_1", "Asset_2"]
    assert rows[0]["rrf_rank"] == "1"
    assert "TopAnswer passed" in capsys.readouterr().out


def test_cli_needs_a_radiator(monkeypatch):
    with pytest.raises(SystemExit):
        rrf_compare.cli([])
