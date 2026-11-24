"""Compare ranking ARS results by confidence with ranking them by RRF.

The harness ranks the ARS's merged results by their ``confidence``
(``acceptance_test_runner.sort_by_ars_confidence``). The ARS (Relay,
``tr_sys/tr_ars/ranker_fusion.py``) also ranks them by weighted Reciprocal
Rank Fusion of the ARAGORN and ARAX rankings, and stores the outcome on every
merged result as ``rrf_score``, ``rrf_rank``, ``rrf_ranker_ranks`` and
``rrf_ranker_contributions``.

This re-scores an acceptance run that already happened, rather than running
the queries again: both rankings are judged on exactly the same responses, so
any difference is down to the ranking alone. For each acceptance asset in the
run it takes the ARS merged-message pk the Information Radiator stored,
fetches that message from the ARS (the harness retains every query it runs),
and runs the harness's own pass/fail analysis twice: once ordered by
confidence, once by RRF.

Where the merged message carries no ``rrf_score`` (RRF disabled in that ARS,
a message from before RRF, or no ARAGORN/ARAX result the ARS could match), the
RRF is computed here instead, the way the ARS does, from the ARAGORN and ARAX
responses (their pks are in the radiator too). ``--recompute`` always computes
it here.

    test-harness-rrf-compare --radiator-url https://radiator.example.org
    test-harness-rrf-compare --env ci --suite mvp_tests --out rrf.csv
    test-harness-rrf-compare --run-id 0b9c...  # a particular run

The radiator's read API is open, so no token is needed. Writes one CSV row per
asset and prints a summary.
"""

import argparse
import csv
import logging
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

import httpx

from test_harness.acceptance_test_runner import (
    get_ars_confidence,
    run_acceptance_pass_fail_analysis,
    sort_by_ars_confidence,
)
from test_harness.trapi import binding_ids
from test_harness.utils import NODE_NORM_URL, AgentReport, AgentStatus

logger = logging.getLogger("rrf_compare")

ARS_URL = {
    "dev": "https://ars.dev.transltr.io",
    "ci": "https://ars.ci.transltr.io",
    "test": "https://ars.test.transltr.io",
    "prod": "https://ars.transltr.io",
}

# The ARS's defaults (ARS_RRF_ARAGORN_WEIGHT, whose complement is ARAX's
# weight, and ARS_RRF_C), used when the RRF is computed here.
DEFAULT_ARAGORN_WEIGHT = 0.9
DEFAULT_C = 40
RANKERS = ("shepherd-aragorn", "shepherd-arax")

# Expected outputs the acceptance analysis judges.
EXPECTED_OUTPUTS = ("TopAnswer", "Acceptable", "BadButForgivable", "NeverShow")

COLUMNS = [
    "test_case_id",
    "asset_id",
    "name",
    "expected_output",
    "input_curie",
    "output_curies",
    "parent_pk",
    "ars_pk",
    "n_results",
    "rrf_source",
    "found_curie",
    "confidence_status",
    "confidence_rank",
    "confidence",
    "rrf_status",
    "rrf_rank",
    "rrf_score",
    "ars_rrf_rank",
    "rrf_ranker_ranks",
    "rank_change",
    "status_change",
    "radiator_status",
    "radiator_rank",
    "error",
]


# --- RRF ---------------------------------------------------------------------


def result_key(result: Dict[str, Any]) -> Optional[frozenset]:
    """The ids a result binds: how the ARS matches a merged result to an ARA's.

    Same as Relay's ``ranker_fusion._result_key``, but reads TRAPI 2.0
    bindings as well as 1.x ones.
    """
    ids = set()
    for binding in (result.get("node_bindings") or {}).values():
        ids.update(binding_ids(binding))
    return frozenset(ids) if ids else None


def rank_map(results: Iterable[Dict[str, Any]]) -> Dict[frozenset, int]:
    """Each result's 1-based rank in a ranker's (already ordered) list; the
    first of several results binding the same ids keeps its rank."""
    ranks: Dict[frozenset, int] = {}
    for index, result in enumerate(results or [], start=1):
        key = result_key(result)
        if key is not None and key not in ranks:
            ranks[key] = index
    return ranks


def compute_rrf(
    results: List[Dict[str, Any]],
    ranker_results: Dict[str, List[Dict[str, Any]]],
    weights: Dict[str, float],
    c_value: float,
) -> Optional[List[Dict[str, Any]]]:
    """Weighted RRF, as the ARS applies it to a merged message.

    Returns copies of ``results`` ordered by RRF (ties keep their original
    order), each with ``rrf_score``, ``rrf_rank`` (tied scores share a rank)
    and ``rrf_ranker_ranks`` set; or None when no result matches any ranker,
    in which case the ARS leaves the message alone.
    """
    maps = {
        source: ranks
        for source, source_results in ranker_results.items()
        if source in weights and (ranks := rank_map(source_results))
    }
    if not maps:
        return None
    scored = []
    matched = 0
    for index, result in enumerate(results):
        key = result_key(result)
        score = 0.0
        source_ranks = {}
        if key is not None:
            for source, ranks in maps.items():
                rank = ranks.get(key)
                if rank is not None:
                    score += weights[source] / (c_value + rank)
                    source_ranks[source] = rank
        matched += bool(source_ranks)
        scored.append((score, index, result, source_ranks))
    if not matched:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    ordered = []
    previous_score = previous_rank = None
    for rank, (score, _, result, source_ranks) in enumerate(scored, start=1):
        if score != previous_score:
            previous_score, previous_rank = score, rank
        ordered.append(
            {
                **result,
                "rrf_score": score,
                "rrf_rank": previous_rank,
                "rrf_ranker_ranks": source_ranks,
            }
        )
    return ordered


def has_ars_rrf(results: List[Dict[str, Any]]) -> bool:
    return any(result.get("rrf_score") is not None for result in results)


def sort_by_rrf(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order results by the ARS's ``rrf_score``, highest first; results
    without one go last, and ties keep their order."""
    return sorted(
        results,
        key=lambda r: (r.get("rrf_score") is None, -(r.get("rrf_score") or 0)),
    )


# --- Scoring -----------------------------------------------------------------


def analyse(
    results: List[Dict[str, Any]], out_curies: List[str], expected: str
) -> AgentReport:
    """The harness's own pass/fail analysis of results in the order given."""
    report = {
        "ars": AgentReport(status=AgentStatus.SKIPPED, message=None, actual_output=None)
    }
    run_acceptance_pass_fail_analysis(
        report, "ars", results, out_curies, expected, sort_by_confidence=False
    )
    return report["ars"]


def first_position(results: List[Dict[str, Any]], curies: List[str]) -> Optional[int]:
    wanted = set(curies)
    for index, result in enumerate(results):
        if result_key(result) and wanted & result_key(result):
            return index
    return None


def compare_asset(
    asset: Dict[str, Any],
    merged_results: List[Dict[str, Any]],
    out_curies: List[str],
    ranker_results: Optional[Dict[str, List[Dict[str, Any]]]],
    weights: Dict[str, float],
    c_value: float,
    recompute: bool = False,
) -> Dict[str, Any]:
    """One CSV row comparing the two rankings of one asset's merged results.

    ``ranker_results`` (the ARAGORN and ARAX results, by ``infores:`` id) is
    only needed when the RRF has to be computed here.
    """
    expected = asset["expected_output"]
    row: Dict[str, Any] = {"n_results": len(merged_results)}

    by_confidence = sort_by_ars_confidence(merged_results)
    if not recompute and has_ars_rrf(merged_results):
        by_rrf, row["rrf_source"] = sort_by_rrf(merged_results), "ars"
    else:
        by_rrf = (
            compute_rrf(merged_results, ranker_results or {}, weights, c_value)
            if ranker_results
            else None
        )
        row["rrf_source"] = "computed" if by_rrf is not None else "unavailable"

    confidence = analyse(by_confidence, out_curies, expected)
    row["confidence_status"] = confidence.status.value
    output = confidence.actual_output or {}
    row["confidence_rank"] = output.get("ars_rank")
    row["confidence"] = output.get("ars_score")
    found = output.get("found_curies") or []
    row["found_curie"] = found[0] if found else None
    if confidence.message:
        row["error"] = confidence.message

    if by_rrf is None:
        return row

    rrf = analyse(by_rrf, out_curies, expected)
    row["rrf_status"] = rrf.status.value
    position = first_position(by_rrf, out_curies)
    if position is not None:
        best = by_rrf[position]
        row["rrf_rank"] = position + 1
        row["rrf_score"] = best.get("rrf_score")
        row["ars_rrf_rank"] = best.get("rrf_rank")
        row["rrf_ranker_ranks"] = ";".join(
            f"{source.removeprefix('infores:')}={rank}"
            for source, rank in sorted((best.get("rrf_ranker_ranks") or {}).items())
        )
    if row.get("confidence_rank") and row.get("rrf_rank"):
        # positive: RRF ranks the expected answer higher
        row["rank_change"] = row["confidence_rank"] - row["rrf_rank"]
    if row["confidence_status"] != row["rrf_status"]:
        row["status_change"] = f"{row['confidence_status']}->{row['rrf_status']}"
    return row


# --- Fetching ----------------------------------------------------------------


def _get_json(client: httpx.Client, url: str, **params) -> Any:
    response = client.get(url, params=params or None)
    response.raise_for_status()
    return response.json()


def find_run(client, radiator, run_id, suite, env, look_back=20) -> Dict[str, Any]:
    """The run to compare, with its results.

    ``run_id`` when given; otherwise the newest finished run in ``env`` (of
    ``suite``, if given) that has acceptance results with ARS pks.
    """
    if run_id:
        return _get_json(client, f"{radiator}/api/runs/{run_id}")
    if suite:
        return _get_json(
            client, f"{radiator}/api/runs/latest", suite=suite, env=env, results="true"
        )
    for run in _get_json(client, f"{radiator}/api/runs", env=env, limit=look_back):
        if not run.get("ended_at") or run.get("origin") == "zebrunner_import":
            continue
        full = _get_json(client, f"{radiator}/api/runs/{run['run_id']}")
        if any(ars_pk(asset) for asset in acceptance_assets(full)):
            return full
        logger.info(
            f"Skipping run {run['run_id']} ({run['suite']}): no ARS acceptance pks"
        )
    raise SystemExit(
        f"No finished acceptance run with ARS pks among the last {look_back} in {env}"
    )


def acceptance_assets(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        asset
        for asset in run.get("results") or []
        if asset.get("kind") == "acceptance"
        and asset.get("expected_output") in EXPECTED_OUTPUTS
    ]


def agent_pk(asset: Dict[str, Any], agent: str) -> Optional[str]:
    for row in asset.get("agents") or []:
        if row["agent"] == agent and row.get("pk") not in (None, "", "None"):
            return row["pk"]
    return None


def ars_pk(asset: Dict[str, Any]) -> Optional[str]:
    return agent_pk(asset, "ars")


def fetch_results(client: httpx.Client, ars: str, pk: str) -> Optional[List[dict]]:
    """A message's results, or None if the ARS has no finished message."""
    message = _get_json(client, f"{ars}/ars/api/messages/{pk}")
    fields = message.get("fields") or {}
    if fields.get("status") not in (None, "Done"):
        return None
    return ((fields.get("data") or {}).get("message") or {}).get("results") or []


def normalize(client: httpx.Client, env: str, curies: Iterable[str]) -> Dict[str, str]:
    """Each CURIE's preferred id, normalized as the harness does it."""
    curies = sorted(set(curies))
    normalized = {curie: curie for curie in curies}
    if not curies or env not in NODE_NORM_URL:
        return normalized
    try:
        response = client.post(
            NODE_NORM_URL[env] + "/get_normalized_nodes",
            json={"curies": curies, "conflate": True, "drug_chemical_conflate": True},
        )
        response.raise_for_status()
        for curie, attrs in response.json().items():
            if attrs:
                normalized[curie] = attrs["id"]["identifier"]
    except Exception as e:
        logger.error(f"Node normalization failed ({e}); using the CURIEs as given")
    return normalized


def out_ids(asset: Dict[str, Any], normalized: Dict[str, str]) -> List[str]:
    ids: List[str] = []
    for curie in asset.get("output_curies") or []:
        curie = normalized.get(curie, curie)
        if curie not in ids:
            ids.append(curie)
    return ids


def compare_run(
    run: Dict[str, Any],
    ars: str,
    env: str,
    weights: Dict[str, float],
    c_value: float,
    recompute: bool = False,
    workers: int = 4,
    client: Optional[httpx.Client] = None,
) -> List[Dict[str, Any]]:
    client = client or httpx.Client(timeout=120)
    assets = acceptance_assets(run)
    normalized = normalize(
        client, env, (c for asset in assets for c in asset.get("output_curies") or [])
    )

    def one(asset):
        radiator_ars = next(
            (a for a in asset.get("agents") or [] if a["agent"] == "ars"), {}
        )
        row = {
            "test_case_id": asset["test_case_id"],
            "asset_id": asset["asset_id"],
            "name": asset.get("name"),
            "expected_output": asset["expected_output"],
            "input_curie": asset.get("input_curie"),
            "output_curies": ";".join(asset.get("output_curies") or []),
            "parent_pk": asset.get("parent_pk"),
            "ars_pk": ars_pk(asset),
            "radiator_status": radiator_ars.get("status"),
            "radiator_rank": radiator_ars.get("rank"),
        }
        if not row["ars_pk"]:
            row["error"] = "no ARS merged pk in the radiator"
            return row
        try:
            results = fetch_results(client, ars, row["ars_pk"])
            if results is None:
                row["error"] = "ARS merged message isn't Done"
                return row
            ranker_results = None
            if recompute or not has_ars_rrf(results):
                ranker_results = {}
                for agent in RANKERS:
                    pk = agent_pk(asset, agent)
                    child = fetch_results(client, ars, pk) if pk else None
                    if child:
                        ranker_results[f"infores:{agent}"] = child
            row.update(
                compare_asset(
                    asset,
                    results,
                    out_ids(asset, normalized),
                    ranker_results,
                    weights,
                    c_value,
                    recompute,
                )
            )
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        return row

    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        return list(pool.map(one, assets))


# --- Report ------------------------------------------------------------------


def summary(rows: List[Dict[str, Any]]) -> str:
    compared = [r for r in rows if r.get("rrf_status")]
    lines = [
        f"{len(rows)} acceptance assets; {len(compared)} compared "
        f"(RRF from the ARS: {sum(r.get('rrf_source') == 'ars' for r in rows)}, "
        f"computed here: {sum(r.get('rrf_source') == 'computed' for r in rows)}, "
        f"unavailable: {sum(r.get('rrf_source') == 'unavailable' for r in rows)}, "
        f"errors: {sum(bool(r.get('error')) for r in rows)})",
    ]
    if not compared:
        return "\n".join(lines)
    lines.append(f"{'':24}{'confidence':>12}{'RRF':>10}")
    for expected in EXPECTED_OUTPUTS:
        of_kind = [r for r in compared if r["expected_output"] == expected]
        if of_kind:
            lines.append(
                f"{expected + ' passed':24}"
                f"{sum(r['confidence_status'] == 'PASSED' for r in of_kind):>8}/{len(of_kind):<3}"
                f"{sum(r['rrf_status'] == 'PASSED' for r in of_kind):>6}/{len(of_kind):<3}"
            )
    ranked = [r for r in compared if r.get("confidence_rank") and r.get("rrf_rank")]
    if ranked:
        for top in (1, 10, 30):
            lines.append(
                f"{'found in top ' + str(top):24}"
                f"{sum(r['confidence_rank'] <= top for r in ranked):>8}/{len(ranked):<3}"
                f"{sum(r['rrf_rank'] <= top for r in ranked):>6}/{len(ranked):<3}"
            )
        lines.append(
            f"{'median rank':24}"
            f"{statistics.median(r['confidence_rank'] for r in ranked):>12g}"
            f"{statistics.median(r['rrf_rank'] for r in ranked):>10g}"
        )
        better = sum(r["rank_change"] > 0 for r in ranked)
        worse = sum(r["rank_change"] < 0 for r in ranked)
        lines.append(
            f"RRF ranks the expected answer higher for {better}, lower for "
            f"{worse}, the same for {len(ranked) - better - worse}"
        )
    flips = [r for r in compared if r.get("status_change")]
    lines.append(f"{len(flips)} assets change status:")
    for r in flips:
        lines.append(
            f"  {r['test_case_id']}/{r['asset_id']} ({r['expected_output']}): "
            f"{r['status_change']}, rank {r.get('confidence_rank')} -> {r.get('rrf_rank')}"
        )
    return "\n".join(lines)


def write_csv(rows: List[Dict[str, Any]], path: str):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cli(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--radiator-url",
        default=os.getenv("RADIATOR_PUBLIC_URL") or os.getenv("RADIATOR_URL"),
        help="The Information Radiator (default: $RADIATOR_PUBLIC_URL or $RADIATOR_URL)",
    )
    parser.add_argument(
        "--env", default="ci", choices=sorted(ARS_URL), help="default: ci"
    )
    parser.add_argument(
        "--run-id", help="Compare this run (default: the latest in --env)"
    )
    parser.add_argument(
        "--suite",
        help="Take the latest run of this suite (default: the latest acceptance run of any)",
    )
    parser.add_argument("--ars-url", help="The ARS (default: the one for --env)")
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Compute the RRF here even when the ARS stored one",
    )
    parser.add_argument(
        "--aragorn-weight",
        type=float,
        default=DEFAULT_ARAGORN_WEIGHT,
        help="ARAGORN's RRF weight when computed here; ARAX gets the rest (default 0.9)",
    )
    parser.add_argument(
        "--c",
        type=float,
        default=DEFAULT_C,
        help="The RRF constant when computed here (default 40)",
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Parallel ARS fetches (default 4)"
    )
    parser.add_argument(
        "--out", help="CSV to write (default: rrf_compare_<run id>.csv)"
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")
    if not args.radiator_url:
        parser.error("--radiator-url (or $RADIATOR_PUBLIC_URL) is required")
    if not 0 <= args.aragorn_weight <= 1:
        parser.error("--aragorn-weight must be between 0 and 1")
    radiator = args.radiator_url.rstrip("/")
    weights = {
        "infores:shepherd-aragorn": args.aragorn_weight,
        "infores:shepherd-arax": round(1 - args.aragorn_weight, 8),
    }

    with httpx.Client(timeout=120) as client:
        run = find_run(client, radiator, args.run_id, args.suite, args.env)
        env = run.get("env") or args.env
        ars = (args.ars_url or ARS_URL.get(env) or ARS_URL[args.env]).rstrip("/")
        logger.info(
            f"Comparing run {run['run_id']} ({run['suite']}, {env}, started "
            f"{run['started_at']}) against {ars}"
        )
        rows = compare_run(
            run, ars, env, weights, args.c, args.recompute, args.workers, client
        )

    out = args.out or f"rrf_compare_{run['run_id']}.csv"
    write_csv(rows, out)
    print(summary(rows))
    print(f"Wrote {out}")


if __name__ == "__main__":
    sys.exit(cli())
