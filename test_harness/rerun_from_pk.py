"""Re-run acceptance analysis from a previous output CSV.

Given an acceptance-results CSV produced by the Test Harness (columns
``name, url, pk, TestCase, TestAsset, <agent columns...>``) this script, for each
row:

1. extracts the ARS ``parent_pk`` from the ``pk`` URL,
2. re-fetches the *merged* ARS message for that pk,
3. re-sorts the merged results by ``ordering_components.confidence`` (descending),
4. re-runs the existing acceptance pass/fail analysis on the re-sorted results,
5. writes the whole CSV back out in the same format with the ``ars`` column
   recomputed (all other agent columns are carried over unchanged), plus a
   summary JSON of ``{expected_output_category: {status: count}}`` for the
   recomputed ars column.

The expected-output category is read from the ``name`` column (which always starts
with ``"{expected_output}: "``); the expected-output CURIE (``output_id``) is
recovered by joining each row to the original test-suite JSON on
``TestCase`` id + ``TestAsset`` id, then normalized via ``normalize_curies``.
"""

import csv
import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from translator_testing_model.datamodel.pydanticmodel import TestAsset, TestSuite

from test_harness.acceptance_test_runner import run_acceptance_pass_fail_analysis
from test_harness.runner.query_runner import env_map
from test_harness.runner.smart_api_registry import retrieve_registry_from_smartapi
from test_harness.utils import AgentReport, AgentStatus, normalize_curies

# Mirrors ResultCollector.query_types / the categories the acceptance runner accepts.
VALID_EXPECTED_OUTPUTS = ("TopAnswer", "Acceptable", "BadButForgivable", "NeverShow")


def parse_pk(pk_url: str) -> Optional[str]:
    """Extract the ARS parent pk from a ``...?r=<pk>`` results URL."""
    if not pk_url:
        return None
    query = urlparse(pk_url).query
    values = parse_qs(query).get("r")
    if values and values[0]:
        return values[0]
    return None


def parse_expected_output(name: str) -> Optional[str]:
    """Parse the expected-output category from a ``"{expected_output}: ..."`` name."""
    if not name or ": " not in name:
        return None
    prefix = name.split(": ", 1)[0].strip()
    return prefix if prefix in VALID_EXPECTED_OUTPUTS else None


def _result_confidence(result: Dict[str, Any]) -> float:
    """Pull ``ordering_components.confidence`` from a single TRAPI result.

    Checks the result level first, then falls back to the max across the
    result's analyses. Missing/None sorts last.
    """
    oc = result.get("ordering_components")
    if isinstance(oc, dict) and oc.get("confidence") is not None:
        return float(oc["confidence"])
    confidences = []
    for analysis in result.get("analyses", []) or []:
        oc = analysis.get("ordering_components")
        if isinstance(oc, dict) and oc.get("confidence") is not None:
            confidences.append(float(oc["confidence"]))
    if confidences:
        return max(confidences)
    return float("-inf")


def sort_results_by_confidence(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return results sorted by ordering_components.confidence, descending (stable)."""
    return sorted(results, key=_result_confidence, reverse=True)


def fetch_merged_message(
    parent_pk: str,
    base_url: str,
    logger: logging.Logger,
) -> Tuple[List[Dict[str, Any]], int]:
    """Fetch the merged ARS message for a parent pk.

    Mirrors the merged-message extraction in
    ``QueryRunner.get_ars_responses``: the parent trace gives ``merged_version``,
    whose message carries the merged TRAPI data at ``fields.data.message.results``.
    Returns ``(results, status_code)``.
    """
    try:
        with httpx.Client(timeout=30) as client:
            res = client.get(f"{base_url}/ars/api/messages/{parent_pk}?trace=y")
            res.raise_for_status()
            parent = res.json()
            merged_pk = parent.get("merged_version")
            if not merged_pk:
                logger.warning(f"No merged_version for pk {parent_pk}.")
                return [], 410
            res = client.get(f"{base_url}/ars/api/messages/{merged_pk}")
            res.raise_for_status()
            merged = res.json()
    except Exception as e:
        logger.error(f"Failed to fetch merged message for pk {parent_pk}: {e}")
        return [], 500

    fields = merged.get("fields", {}) or {}
    data = fields.get("data") or {}
    message = data.get("message") or {}
    results = message.get("results") or []
    status_code = fields.get("code", 410)
    return results, status_code


def compute_ars_status(
    results: List[Dict[str, Any]],
    status_code: Any,
    out_curie: str,
    expected_output: str,
    logger: logging.Logger,
) -> str:
    """Recompute the ARS pass/fail status, mirroring run.py's per-agent guards."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        code = 500
    if code > 299:
        return AgentStatus.FAILED.value
    if not results:
        return AgentStatus.NO_RESULTS.value

    report = {
        "ars": AgentReport(status=AgentStatus.SKIPPED, message=None, actual_output=None)
    }
    try:
        run_acceptance_pass_fail_analysis(
            report, "ars", results, out_curie, expected_output
        )
    except Exception as e:
        logger.error(f"Acceptance analysis failed: {e}")
        return AgentStatus.FAILED.value
    return report["ars"].status.value


def load_test_suite(
    path: str,
) -> Tuple[Dict[Tuple[str, str], Tuple[Any, Any]], Optional[str]]:
    """Load a test-suite JSON.

    Returns a ``{(test_id, asset_id): (test, asset)}`` lookup plus the run's
    environment (the first test case's ``test_env``); the whole run shares a
    single env / ARS url.
    """
    with open(path) as f:
        suite = TestSuite.model_validate(json.load(f))
    lookup: Dict[Tuple[str, str], Tuple[Any, Any]] = {}
    run_env: Optional[str] = None
    for test in suite.test_cases.values():
        if run_env is None:
            run_env = test.test_env
        for asset in test.test_assets:
            lookup[(str(test.id), str(asset.id))] = (test, asset)
    return lookup, run_env


def resolve_ars_url(
    test_env: Optional[str],
    trapi_version: str,
    override: Optional[str],
    logger: logging.Logger,
) -> Optional[str]:
    """Resolve the ARS base url for the run's environment, like run.py does."""
    if override:
        return override
    registry = retrieve_registry_from_smartapi(trapi_version)
    services = registry.get(env_map.get(test_env, ""), {}).get("ars", [])
    if not services:
        logger.error(f"No ARS service found in registry for env '{test_env}'.")
        return None
    return services[0]["url"]


def build_acceptance_stats(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, int]]:
    """Summarize the (recomputed) ars column as ``{category: {status: count}}``.

    Categories come from the name-column prefix and statuses are the final ars
    values in the output rows, mirroring ``ResultCollector.acceptance_stats`` for
    the ars agent. The category/status key order matches ``VALID_EXPECTED_OUTPUTS``
    and the ``AgentStatus`` enum.
    """
    stats: Dict[str, Dict[str, int]] = {
        category: {status.value: 0 for status in AgentStatus}
        for category in VALID_EXPECTED_OUTPUTS
    }
    for row in rows:
        category = parse_expected_output(row.get("name", ""))
        if category is None:
            continue
        status = row.get("ars", "")
        if status in stats[category]:
            stats[category][status] += 1
    return stats


def _write_csv(output_csv: str, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_stats(
    stats_output: Optional[str],
    rows: List[Dict[str, str]],
    logger: logging.Logger,
) -> Dict[str, Dict[str, int]]:
    stats = build_acceptance_stats(rows)
    if stats_output:
        with open(stats_output, "w") as f:
            json.dump(stats, f, indent=2)
        logger.info(f"Wrote acceptance stats to {stats_output}")
    return stats


def rerun_csv(
    input_csv: str,
    test_suite_path: str,
    output_csv: str,
    ars_url: Optional[str],
    trapi_version: str,
    logger: logging.Logger,
    stats_output: Optional[str] = None,
) -> str:
    """Re-run acceptance analysis for every row of ``input_csv`` and write a new CSV.

    If ``stats_output`` is given, also write a per-category pass/fail summary JSON
    of the recomputed ars column.
    """
    asset_lookup, run_env = load_test_suite(test_suite_path)

    with open(input_csv, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    required = {"name", "pk", "TestCase", "TestAsset", "ars"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {sorted(missing)}")

    # The whole run shares one environment, so resolve the ARS url just once.
    ars_base_url = resolve_ars_url(run_env, trapi_version, ars_url, logger)
    if ars_base_url is None:
        logger.error(
            f"Could not resolve an ARS url for env '{run_env}'; writing rows unchanged."
        )
        _write_csv(output_csv, fieldnames, rows)
        _write_stats(stats_output, rows, logger)
        return output_csv

    normalized_cache: Dict[str, Dict[str, str]] = {}
    # Many assets share a single ARS response, and same-pk rows are contiguous,
    # so we hold one merged (already confidence-sorted) message at a time and
    # only re-fetch when we reach a row whose pk differs from the one we have.
    cached_pk: Optional[str] = None
    cached_results: List[Dict[str, Any]] = []
    cached_status: Any = None

    for row in rows:
        case_id = row.get("TestCase", "")
        asset_id = row.get("TestAsset", "")
        original_status = row.get("ars", "")

        lookup = asset_lookup.get((case_id, asset_id))
        if lookup is None:
            logger.warning(
                f"No matching asset in suite for TestCase={case_id} "
                f"TestAsset={asset_id}; keeping original ars value."
            )
            continue
        test, asset = lookup

        if not isinstance(asset, TestAsset):
            logger.warning(
                f"Asset {asset_id} is not a standard TestAsset; keeping original ars value."
            )
            continue

        parent_pk = parse_pk(row.get("pk", ""))
        if parent_pk is None:
            logger.warning(
                f"No pk found in row for TestCase={case_id} TestAsset={asset_id}; "
                "keeping original ars value."
            )
            continue

        # expected_output: from the CSV name column per spec, falling back to the asset.
        expected_output = parse_expected_output(row.get("name", ""))
        if expected_output is None:
            expected_output = asset.expected_output
            logger.info(
                f"Could not parse expected_output from name for {asset_id}; "
                f"falling back to asset value '{expected_output}'."
            )

        # out_curie: normalized output_id (cached per test case, like run.py).
        if test.id not in normalized_cache:
            normalized_cache[test.id] = normalize_curies(test, logger)
        out_curie = (
            normalized_cache[test.id].get(asset.output_id, "")
            if asset.output_id is not None
            else ""
        )

        # Only fetch the merged message when the pk changes from the one we hold.
        if parent_pk != cached_pk:
            results, cached_status = fetch_merged_message(
                parent_pk, ars_base_url, logger
            )
            cached_results = sort_results_by_confidence(results)
            cached_pk = parent_pk
            logger.info(f"Fetched merged ARS message for pk={parent_pk}")

        new_status = compute_ars_status(
            cached_results, cached_status, out_curie, expected_output, logger
        )
        logger.info(
            f"{case_id}/{asset_id}: ars {original_status} -> {new_status} "
            f"({len(cached_results)} results, pk={parent_pk})"
        )
        row["ars"] = new_status

    _write_csv(output_csv, fieldnames, rows)
    logger.info(f"Wrote re-run results to {output_csv}")
    _write_stats(stats_output, rows, logger)
    return output_csv


def _get_logger(log_level: str) -> logging.Logger:
    logger = logging.getLogger("harness.rerun_from_pk")
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(asctime)s: %(levelname)s/%(name)s]: %(message)s")
        )
        logger.addHandler(handler)
    return logger


def cli():
    """Parse args and re-run acceptance analysis from a previous output CSV."""
    parser = ArgumentParser(
        description=(
            "Re-fetch merged ARS messages from the pks in a previous acceptance "
            "CSV, re-sort results by ordering_components.confidence, re-run the "
            "acceptance analysis, and write a new CSV."
        )
    )
    parser.add_argument(
        "--input-csv",
        required=True,
        help="Path to a previous acceptance-results CSV (must include a 'pk' column).",
    )
    parser.add_argument(
        "--test-suite",
        required=True,
        help="Path to the original test-suite JSON (used to recover each asset's output_id).",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Where to write the re-run CSV. Defaults to '<input>_rerun.csv'.",
    )
    parser.add_argument(
        "--stats-output",
        default=None,
        help=(
            "Where to write the per-category pass/fail summary JSON. "
            "Defaults to the output CSV path with a '.json' extension."
        ),
    )
    parser.add_argument(
        "--ars-url",
        default=None,
        help="ARS base url to query. Defaults to the SmartAPI registry entry for the suite's env.",
    )
    parser.add_argument(
        "--trapi-version",
        default="1.6.0",
        help="TRAPI version used when looking up the ARS service in the registry.",
    )
    parser.add_argument(
        "--log-level",
        choices=["ERROR", "WARNING", "INFO", "DEBUG"],
        default="INFO",
        help="Logging level.",
    )
    args = parser.parse_args()

    logger = _get_logger(args.log_level)
    output_csv = args.output_csv
    if output_csv is None:
        input_path = Path(args.input_csv)
        output_csv = str(input_path.with_name(f"{input_path.stem}_rerun.csv"))

    stats_output = args.stats_output
    if stats_output is None:
        stats_output = str(Path(output_csv).with_suffix(".json"))

    rerun_csv(
        input_csv=args.input_csv,
        test_suite_path=args.test_suite,
        output_csv=output_csv,
        ars_url=args.ars_url,
        trapi_version=args.trapi_version,
        logger=logger,
        stats_output=stats_output,
    )


if __name__ == "__main__":
    cli()
