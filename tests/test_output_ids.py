"""Tests for assets whose expected output goes by several CURIEs.

Eg sirolimus is CHEBI:9168 and PUBCHEM.COMPOUND:5284616, which node
normalization no longer merges, so it can come back as two separate answers.
"""

import pytest

from radiator_schema import AssetResult
from test_harness.acceptance_test_runner import run_acceptance_pass_fail_analysis
from test_harness.radiator_client import RadiatorClient
from test_harness.result_collector import ResultCollector
from test_harness.utils import (
    AgentReport,
    AgentStatus,
    TestReport,
    normalized_output_ids,
)

from .helpers.logger import setup_logger
from .test_radiator import _Case, _run

logger = setup_logger()

CHEBI = "CHEBI:9168"
PUBCHEM = "PUBCHEM.COMPOUND:5284616"


def _result(curie, score):
    return {"node_bindings": {"n1": [{"id": curie}]}, "analyses": [{"score": score}]}


def _results(placed):
    """Ten ARA results, with the given CURIEs at the given (1-based) ranks."""
    by_rank = {rank: curie for curie, rank in placed.items()}
    return [
        _result(by_rank.get(rank, f"MONDO:{rank}"), 1 - rank / 100)
        for rank in range(1, 11)
    ]


def _analyse(results, expected_output, curies=(CHEBI, PUBCHEM)):
    report = {"ara": AgentReport(AgentStatus.SKIPPED, None, None)}
    run_acceptance_pass_fail_analysis(
        report, "ara", results, list(curies), expected_output
    )
    return report["ara"]


@pytest.mark.parametrize(
    "expected_output, placed, status",
    [
        # positive: passes if any CURIE lands in the window
        ("Acceptable", {CHEBI: 9, PUBCHEM: 2}, AgentStatus.PASSED),
        ("Acceptable", {CHEBI: 9, PUBCHEM: 7}, AgentStatus.FAILED),
        ("Acceptable", {}, AgentStatus.FAILED),
        ("TopAnswer", {PUBCHEM: 10}, AgentStatus.PASSED),
        # BadButForgivable: fails if any CURIE ranks in the top half
        ("BadButForgivable", {CHEBI: 9, PUBCHEM: 2}, AgentStatus.FAILED),
        ("BadButForgivable", {CHEBI: 9}, AgentStatus.PASSED),
        ("BadButForgivable", {}, AgentStatus.PASSED),
        # NeverShow: fails if any CURIE appears at all
        ("NeverShow", {PUBCHEM: 10}, AgentStatus.FAILED),
        ("NeverShow", {}, AgentStatus.PASSED),
    ],
)
def test_pass_fail_rules(expected_output, placed, status):
    assert _analyse(_results(placed), expected_output).status == status


def test_reports_best_ranked_match_and_every_curie_found():
    """The rank and score are the best-ranked CURIE's, whichever order the
    asset lists them in, and found_curies shows the answer came back split."""
    output = _analyse(_results({CHEBI: 6, PUBCHEM: 3}), "Acceptable").actual_output
    assert output["found"] is True
    assert output["found_curies"] == [PUBCHEM, CHEBI]
    assert output["ara_rank"] == 3
    assert output["ara_score"] == pytest.approx(0.97)


def test_not_found():
    output = _analyse(_results({}), "TopAnswer").actual_output
    assert output["found"] is False
    assert output["found_curies"] == []
    assert output["ara_rank"] is None


def test_best_rank_of_a_curie_bound_more_than_once():
    results = _results({CHEBI: 2})
    results[7] = _result(CHEBI, 0.1)
    output = _analyse(results, "TopAnswer", curies=[CHEBI]).actual_output
    assert output["ara_rank"] == 2


class _Asset:
    id = "Asset_11"
    name = "TopAnswer: sirolimus treats Castleman Disease"
    description = None
    expected_output = "TopAnswer"
    predicate_id = "biolink:treats"
    input_id = "MONDO:0015564"
    output_ids = [PUBCHEM, CHEBI]


def test_normalized_output_ids_dedupes_curies_normalization_merges():
    normalized = {PUBCHEM: CHEBI, CHEBI: CHEBI}
    assert normalized_output_ids(_Asset(), normalized) == [CHEBI]
    # a CURIE normalization didn't return is kept as given
    assert normalized_output_ids(_Asset(), {}) == [PUBCHEM, CHEBI]


def test_radiator_record_carries_every_curie_and_those_found():
    client = RadiatorClient(enabled=False)
    client.start_run(_run())
    collector = ResultCollector("dev", logger, radiator=client)
    report = TestReport(pks={}, result={}, test_details=None)
    report.result["ars"] = AgentReport(AgentStatus.SKIPPED, None, None)
    run_acceptance_pass_fail_analysis(
        report.result,
        "ars",
        _results({CHEBI: 4, PUBCHEM: 1}),
        [PUBCHEM, CHEBI],
        "TopAnswer",
    )
    collector.collect_acceptance_result(_Case(), _Asset(), report, None, "url")

    [result] = client.payload.results
    assert result.output_curies == [PUBCHEM, CHEBI]
    ars = next(agent for agent in result.agents if agent.agent == "ars")
    assert ars.found_curies == [PUBCHEM, CHEBI]
    assert ars.rank == 1


def test_radiator_reads_version_1_records():
    """A run saved before output_curies existed can still be pushed."""
    record = {"test_case_id": "c", "asset_id": "a", "kind": "acceptance"}
    v1 = AssetResult.model_validate(
        {**record, "status": "PASSED", "output_curie": CHEBI}
    )
    assert v1.output_curies == [CHEBI]
    none = AssetResult.model_validate(
        {**record, "status": "PASSED", "output_curie": None}
    )
    assert none.output_curies == []
