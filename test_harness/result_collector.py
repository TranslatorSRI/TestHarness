"""The Collector of Results."""

import json
import logging
import re
from typing import Dict, Iterator, List, Optional, Tuple, Union
from urllib.parse import urlparse

from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    TestAsset,
    TestCase,
    TestEnvEnum,
)

from test_harness.performance_test_runner import checkpoint_verdicts
from test_harness.utils import AgentReport, AgentStatus, TestReport


def _fmt_ms(ms: Optional[float]) -> str:
    """Milliseconds as seconds, the unit every latency in the report uses."""
    if ms is None:
        return "n/a"
    return f"{ms / 1000.0:.1f}s"


def _slugify_host(host_url: str) -> str:
    """Make a filesystem/Slack-friendly slug for a host URL."""
    netloc = urlparse(host_url).netloc or host_url
    return re.sub(r"[^A-Za-z0-9._-]+", "_", netloc).strip("_") or "perf"


class ResultCollector:
    """Collect results for easy dissemination."""

    def __init__(
        self,
        test_env: Optional[TestEnvEnum],
        logger: logging.Logger,
        target: Optional[str] = None,
    ):
        """Initialize the Collector.

        ``target`` is the infores of an override target service (with or
        without the ``infores:`` prefix). A non-ARS override queries a single
        service directly, so results are collected for that one agent instead
        of the ARS + ARA roster; an ARS override still fans out to ARAs and
        keeps the usual roster.
        """
        self.logger = logger
        self.has_acceptance_results = False
        self.has_performance_results = False
        agents = [
            "ars",
            "shepherd-aragorn",
            "shepherd-arax",
            "shepherd-bte",
        ]
        if target is not None:
            target = target.split("infores:")[-1]
            if target != "ars":
                agents = [target]
        self.agents = agents
        self.query_types = ["TopAnswer", "Acceptable", "BadButForgivable", "NeverShow"]
        self.acceptance_report = {status_type.value: 0 for status_type in AgentStatus}
        self.acceptance_stats = {}
        for agent in self.agents:
            self.acceptance_stats[agent] = {}
            for query_type in self.query_types:
                self.acceptance_stats[agent][query_type] = {}
                for result_type in self.acceptance_report.keys():
                    self.acceptance_stats[agent][query_type][result_type] = 0

        # Each agent gets its status plus the detail the report JSON already
        # carries for it: whether the expected answer was anywhere in that
        # agent's response, and the rank/score it came back with.
        self.agent_columns = ["", "_found", "_rank", "_score"]
        self.columns = [
            "name",
            "url",
            "pk",
            "TestCase",
            "TestAsset",
            *[
                f"{agent}{suffix}"
                for agent in self.agents
                for suffix in self.agent_columns
            ],
        ]
        header = ",".join(self.columns)
        self.acceptance_csv = f"{header}\n"
        self.performance_stats = {}
        self.performance_report = {
            "stats": {},
            "failures": {},
        }

    def collect_acceptance_result(
        self,
        test: Union[TestCase, PathfinderTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
        report: TestReport,
        parent_pk: Union[str, None],
        url: str,
        force_skipped: bool = False,
    ):
        """Add a single report to the total output.

        ``force_skipped`` records every agent as SKIPPED regardless of what is
        in ``report``. It is used when the test as a whole was skipped (eg the
        query never ran) so the per-agent stats/CSV agree with the skipped
        test-level status instead of reporting incidental per-ARA errors.
        """
        self.has_acceptance_results = True
        # add result to stats
        agent_cells = []
        for agent in self.agents:
            query_type = asset.expected_output
            if not force_skipped and agent in report.result:
                agent_result = report.result[agent]
                self.acceptance_stats[agent][query_type][agent_result.status.value] += 1
                agent_cells.append(agent_result.status.value)
                agent_cells.extend(self._expected_answer_cells(agent_result))
            else:
                # Agent produced no response for this asset. Record it as
                # SKIPPED in the per-agent stats too, so the JSON summary
                # uploaded to Slack stays consistent with the CSV (which
                # already reports SKIPPED here) and every asset is accounted
                # for in each agent's totals.
                self.acceptance_stats[agent][query_type][AgentStatus.SKIPPED.value] += 1
                agent_cells.append(AgentStatus.SKIPPED.value)
                # Nothing came back, so there's nothing to say about where the
                # expected answer landed.
                agent_cells.extend([""] * (len(self.agent_columns) - 1))

        # add result to csv
        agent_results = ",".join(agent_cells)
        pk_url = (
            f"https://arax.ci.transltr.io/?r={parent_pk}"
            if parent_pk is not None
            else ""
        )
        self.acceptance_csv += (
            f""""{asset.name}",{url},{pk_url},{test.id},{asset.id},{agent_results}\n"""
        )

    @staticmethod
    def _expected_answer_cells(agent_report: AgentReport) -> List[str]:
        """CSV cells for where the expected answer landed for a single agent.

        Returns (found, rank, score), pulled from the same ``actual_output``
        that goes into the report JSON uploaded to the radiator. ARS results
        are scored/ranked by the ARS itself (sugeno), ARA results by their own
        analyses, so whichever of the two the analysis filled in is used.
        Cells are left blank when the agent never got far enough for the
        question to have an answer (eg it errored out).
        """
        actual_output = agent_report.actual_output or {}
        if not actual_output:
            # No results at all means the expected answer definitely wasn't in
            # the response; anything else (an error, a timeout) is unknown.
            found = "false" if agent_report.status == AgentStatus.NO_RESULTS else ""
            return [found, "", ""]

        rank = actual_output.get("ars_rank")
        if rank is None:
            rank = actual_output.get("ara_rank")
        score = actual_output.get("ars_score")
        if score is None:
            score = actual_output.get("ara_score")
        found = actual_output.get("found")
        if found is None:
            # Report without the explicit flag: a rank/score only exists for an
            # answer that was in the response.
            found = rank is not None or score is not None

        return [
            "true" if found else "false",
            "" if rank is None else str(rank),
            "" if score is None else str(score),
        ]

    def collect_performance_result(
        self,
        test: Union[TestCase, PathfinderTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
        url: str,
        host_url: str,
        results: Dict,
    ):
        """Add a single report for a performance test.

        ``results`` is what the HelmsDeep driver returned: the parsed
        ``summary.json``, the HTML report, the process exit code, and the paths
        of everything HelmsDeep wrote. The numbers are HelmsDeep's -- nothing is
        recomputed here, so what Slack shows and what ``summary.json`` says can
        never drift apart.
        """
        self.has_performance_results = True
        summary = results.get("summary") or {}
        config = summary.get("config") or {}
        checkpoints = checkpoint_verdicts(summary)

        run_key = f"{host_url} ({results.get('helmsdeep_target', 'unknown')})"
        self.performance_report["stats"][run_key] = {
            "host": host_url,
            "helmsdeep_target": results.get("helmsdeep_target"),
            "component": config.get("component") or results.get("component"),
            "profile": results.get("profile"),
            "protocol": config.get("protocol"),
            "exit_code": results.get("exit_code"),
            "error": results.get("error"),
            # < 1.0 means HelmsDeep compressed the run to a wall-clock budget.
            # The harness never asks for that, but a summary carries the field
            # either way and a compressed number must not be quoted as one.
            "time_scale": config.get("time_scale"),
            "p99_slo_ms": config.get("p99_slo_ms"),
            "max_error_rate": config.get("max_error_rate"),
            "knee": summary.get("knee"),
            "max_sustainable_concurrency": summary.get("max_sustainable_concurrency"),
            "knee_unsupported": summary.get("knee_unsupported"),
            "stage_warnings": summary.get("stage_warnings") or [],
            "checkpoints": checkpoints,
            "checkpoints_passed": summary.get("checkpoints_passed"),
            "red_flags": summary.get("red_flags") or [],
            "stages": summary.get("stages") or [],
            "summary": summary,
            "report_html": results.get("report_html"),
            "artifacts": results.get("artifacts") or {},
        }
        # A run that never produced a summary is a failure of the run itself,
        # not a measured one. Keep it out of the checkpoint tally so a crashed
        # host can't read as "everything passed".
        if results.get("error"):
            self.performance_report["failures"][run_key] = {
                "error": results["error"],
                "exit_code": results.get("exit_code"),
                "output": results.get("output", ""),
            }

        stats_id = f"{host_url}_case_{test.id}_asset_{asset.id}"
        self.performance_stats[stats_id] = {
            "information_radiator_url": url,
            "helmsdeep_target": results.get("helmsdeep_target"),
            "profile": results.get("profile"),
            "host": host_url,
            "exit_code": results.get("exit_code"),
            "error": results.get("error"),
            "summary": summary,
        }

    @property
    def performance_checkpoints_passed(self) -> Optional[bool]:
        """Overall checkpoint verdict across every performance run.

        ``None`` when no run configured any checkpoints -- a knee-finding run
        has nothing to pass or fail, and reporting it as a pass would be a
        claim the run never made. A run that failed to produce a summary at all
        counts as a failure.
        """
        verdicts = []
        for run_stats in self.performance_report["stats"].values():
            if run_stats.get("error"):
                verdicts.append(False)
            elif run_stats.get("checkpoints"):
                verdicts.append(bool(run_stats.get("checkpoints_passed")))
        if not verdicts:
            return None
        return all(verdicts)

    def render_performance_artifacts(self) -> Iterator[Tuple[str, bytes, str]]:
        """Yield ``(filename, content, comment)`` for each performance artifact.

        Two files per run -- HelmsDeep's ``summary.json`` (the authoritative
        numbers) and its ``report.html`` (locust's charts and request tables) --
        each carrying the run's checkpoint verdict as the comment they're
        uploaded with, so the pass/fail is attached to the file rather than
        buried in a separate message.
        """
        for run_key, run_stats in self.performance_report["stats"].items():
            slug = _slugify_host(run_stats.get("host") or run_key)
            target = run_stats.get("helmsdeep_target") or "perf"
            base = f"{slug}_{target}"
            comment = self._artifact_comment(run_key, run_stats)

            summary = run_stats.get("summary")
            if summary:
                yield (
                    f"{base}_summary.json",
                    json.dumps(summary, indent=2).encode("utf-8"),
                    comment,
                )
            else:
                self.logger.info(
                    f"Skipping summary.json for {run_key}: HelmsDeep produced none"
                )

            report_html = run_stats.get("report_html")
            if report_html:
                yield f"{base}_report.html", report_html.encode("utf-8"), comment
            else:
                self.logger.info(f"Skipping HTML report for {run_key}: not available")

    @staticmethod
    def _artifact_comment(run_key: str, run_stats: Dict) -> str:
        """One-line pass/fail headline to upload the artifacts with."""
        if run_stats.get("error"):
            return (
                f"Performance run FAILED TO COMPLETE - {run_key}: {run_stats['error']}"
            )
        checkpoints = run_stats.get("checkpoints") or []
        if not checkpoints:
            concurrency = run_stats.get("max_sustainable_concurrency")
            knee = (
                f"max sustainable concurrency {concurrency:.1f}"
                if concurrency is not None
                else "no stage met the SLO"
            )
            return f"Performance report - {run_key}: no checkpoints, {knee}"
        passed = sum(1 for c in checkpoints if c["verdict"] == "PASS")
        verdict = "PASS" if run_stats.get("checkpoints_passed") else "FAIL"
        return (
            f"Performance report - {run_key}: checkpoints {verdict} "
            f"({passed}/{len(checkpoints)} passed)"
        )

    def dump_result_summary(self):
        """Format test results summary for Slack."""
        results_formatted = ""
        if self.has_acceptance_results:
            results_formatted += f"""
> Acceptance Test Results:
> Passed: {self.acceptance_report['PASSED']},
> Failed: {self.acceptance_report['FAILED']},
> Skipped: {self.acceptance_report['SKIPPED']}
> No Results: {self.acceptance_report['NO_RESULTS']}
> Errors: {self.acceptance_report['ERROR']}
"""
        if self.has_performance_results:
            overall = self.performance_checkpoints_passed
            if overall is None:
                headline = "no checkpoints configured"
            else:
                headline = "*PASS*" if overall else "*FAIL*"
            results_formatted += (
                f"\n> Performance Test Results (HelmsDeep) - "
                f"checkpoints: {headline}"
            )
            for run_key, run_stats in self.performance_report["stats"].items():
                results_formatted += self._format_performance_run(run_key, run_stats)

        return results_formatted

    @staticmethod
    def _format_performance_run(run_key: str, run_stats: Dict) -> str:
        """Render one HelmsDeep run's section of the Slack summary.

        Leads with the checkpoint verdicts, because that is the pass/fail the
        run was asked for; the knee follows as the headline measurement every
        run produces whether or not it has checkpoints.
        """
        lines = [f"> {run_key}"]

        if run_stats.get("error"):
            lines.append(f"> - RUN FAILED: {run_stats['error']}")
            return "\n" + "\n".join(lines)

        profile = run_stats.get("profile") or "default"
        protocol = run_stats.get("protocol") or "unknown"
        lines.append(f"> - Profile: {profile} | protocol: {protocol}")

        checkpoints = run_stats.get("checkpoints") or []
        if checkpoints:
            verdict = "PASS" if run_stats.get("checkpoints_passed") else "FAIL"
            lines.append(f"> - Checkpoints: *{verdict}*")
            for cp in checkpoints:
                mark = {"PASS": ":white_check_mark:", "FAIL": ":x:"}.get(
                    cp["verdict"], ":grey_question:"
                )
                lines.append(
                    f">   * {mark} {cp['users']} users - {cp['goal']}: "
                    f"{cp['verdict']} ({cp['detail']})"
                )
                if cp.get("p99_ms") is not None:
                    lines.append(
                        f">     p99={_fmt_ms(cp['p99_ms'])}, "
                        f"errors={cp['error_rate'] * 100:.2f}%, "
                        f"requests={cp['requests']}"
                    )
        else:
            lines.append("> - Checkpoints: none configured for this run type")

        concurrency = run_stats.get("max_sustainable_concurrency")
        knee = run_stats.get("knee") or {}
        if concurrency is not None:
            caveat = (
                " (UNSUPPORTED - see stage warnings)"
                if run_stats.get("knee_unsupported")
                else ""
            )
            lines.append(f"> - Max sustainable concurrency: {concurrency:.1f}{caveat}")
            lines.append(
                f">   * Knee at stage {knee.get('stage')} "
                f"({knee.get('users')} users): "
                f"p99={_fmt_ms(knee.get('p99_ms'))}, "
                f"errors={(knee.get('error_rate') or 0) * 100:.2f}%, "
                f"rps={knee.get('rps', 0):.2f}"
            )
        else:
            lines.append(
                "> - Max sustainable concurrency: none - no stage met the "
                f"p99 SLO ({_fmt_ms(run_stats.get('p99_slo_ms'))}) and error cap"
            )

        warnings = run_stats.get("stage_warnings") or []
        if warnings:
            kinds = sorted({i["kind"] for w in warnings for i in w["issues"]})
            lines.append(
                f"> - Measurement quality: {len(warnings)} stage(s) flagged "
                f"({', '.join(kinds)})"
            )

        red_flags = run_stats.get("red_flags") or []
        if red_flags:
            lines.append(f"> - ARS red flags: {len(red_flags)}")
            for flag in red_flags[:3]:
                lines.append(f">   * {flag}")

        time_scale = run_stats.get("time_scale")
        if time_scale is not None and time_scale < 1.0:
            lines.append(
                f"> - WARNING: compressed run (time scale {time_scale:.2f}); "
                "treat these numbers as indicative, not a measurement"
            )

        return "\n" + "\n".join(lines)
