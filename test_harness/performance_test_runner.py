"""Translator Performance Test Runner.

This is a thin driver for **HelmsDeep**, which owns the measurement: it holds
the per-layer step-load ramps, the TRAPI corpuses, the ARS submit/poll/merge
protocol, and the knee/checkpoint arithmetic. The harness's job here is only to
(a) pick the right HelmsDeep run type for the test case, (b) run it, and
(c) hand the two artifacts it produces -- ``summary.json`` and ``report.html``
-- back to the collector for reporting.

Two things the old in-harness locust runner did are deliberately NOT done here,
because HelmsDeep owns them:

* **The ramp.** HelmsDeep's ``LoadTestShape`` drives users, spawn rate, and
  duration from the per-target ``stages`` table in its own config, so a test
  case's ``test_run_time`` and ``spawn_rate`` are not used. A run takes as long
  as the target's ramp says it takes (tens of minutes), and that is the point:
  HelmsDeep's own compression mode exists but explicitly does not produce a
  quotable measurement, so the harness never asks for it.
* **The query.** HelmsDeep sends its own corpus, varying the pinned entity per
  request so the numbers cover a real cost surface instead of warming one
  cache. The test case's asset is not turned into the query under load.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import deque
from typing import Dict, List, Optional
from urllib.parse import urlparse

from translator_testing_model.datamodel.pydanticmodel import PerformanceTestCase

# HelmsDeep is installed as a dependency (see requirements-runners.txt) and run
# as a subprocess: it launches locust itself, and locust is emphatically not
# safe to drive twice in one interpreter.
HELMSDEEP_MODULE = "helmsdeep.cli"

# HelmsDeep names one run type per (layer, query profile). The layer is fixed by
# which component the test targets -- the stack cascades ARS -> ARAs -> KPs, so
# exactly one layer is loaded per run.
ARS_COMPONENT = "ars"
LAYER_ARS = "ars"
LAYER_ARA = "aras"

# The query profile within that layer. "default" is the layer's own single-class
# corpus (lookup for KPs, inferred for ARAs/ARS) and answers the open question
# "how far can we go?" -- it is what a test case gets unless it asks otherwise.
# "mixed" and "pathfinder" are HelmsDeep's heavier run types; "mixed" is the one
# that carries pass/fail checkpoints.
PROFILE_DEFAULT = "default"
PROFILES = (PROFILE_DEFAULT, "mixed", "pathfinder")

# Artifacts HelmsDeep writes next to the CSV prefix. The first two are the
# reportable deliverables; the rest are kept on disk for anyone digging in.
SUMMARY_SUFFIX = "_summary.json"
REPORT_SUFFIX = "_report.html"
EXTRA_SUFFIXES = (
    "_stages.csv",
    "_by_qtype.csv",
    "_checkpoints.csv",
    "_ars_health.csv",
    "_ars_queries.csv",
    "_ars_completion.csv",
)

# How much of HelmsDeep's output to keep when it fails, so the failure reaches
# the report with its reason attached instead of just an exit code.
OUTPUT_TAIL_CHARS = 4000
OUTPUT_TAIL_LINES = 200

# HelmsDeep's output goes to the harness log line by line, under this tag.
LOG_TAG = "[helmsdeep]"

# How the run's TRAPI version reaches HelmsDeep (0.3.0 and later).
TRAPI_VERSION_ENV = "HELMSDEEP_TRAPI_VERSION"


def _slugify(text: str) -> str:
    """Make ``text`` safe to use inside a filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_") or "perf"


def resolve_profile(
    test: PerformanceTestCase,
    override: Optional[str] = None,
    logger: logging.Logger = logging.getLogger(__name__),
) -> str:
    """Pick the HelmsDeep query profile for ``test``.

    An explicit ``override`` (the ``--performance_profile`` CLI flag) wins;
    otherwise a ``mixed`` or ``pathfinder`` entry in the test case's
    ``test_runner_settings`` selects that profile. With neither, the layer's own
    single-class corpus is used.
    """
    if override:
        if override not in PROFILES:
            raise ValueError(
                f"Unknown performance profile {override!r}; "
                f"expected one of {', '.join(PROFILES)}"
            )
        return override
    settings = {str(s).strip().lower() for s in (test.test_runner_settings or [])}
    selected = [p for p in PROFILES if p != PROFILE_DEFAULT and p in settings]
    if len(selected) > 1:
        logger.warning(
            f"Test {test.id} asks for more than one performance profile "
            f"({', '.join(selected)}); using {selected[0]}."
        )
    return selected[0] if selected else PROFILE_DEFAULT


def helmsdeep_target(component: str, profile: str = PROFILE_DEFAULT) -> str:
    """Map a Translator component + query profile onto a HelmsDeep run type.

    Only the ARS speaks the async submit/poll/merge protocol; every other
    component is queried as an ARA (a blocking ``POST /query``).
    """
    component = str(component).split("infores:")[-1].lower()
    layer = LAYER_ARS if component == ARS_COMPONENT else LAYER_ARA
    return layer if profile == PROFILE_DEFAULT else f"{layer}_{profile}"


def _artifact_paths(prefix: str) -> Dict[str, str]:
    """Every HelmsDeep output that actually got written, keyed by suffix."""
    paths = {}
    for suffix in (SUMMARY_SUFFIX, REPORT_SUFFIX, *EXTRA_SUFFIXES):
        path = f"{prefix}{suffix}"
        if os.path.exists(path):
            paths[suffix.lstrip("_")] = path
    return paths


def _read_text(path: Optional[str], logger: logging.Logger) -> Optional[str]:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.warning(f"Failed to read HelmsDeep artifact {path}: {e}")
        return None


def _tail(text: Optional[str]) -> str:
    text = (text or "").strip()
    return text[-OUTPUT_TAIL_CHARS:]


def request_url(run_type: str, host: str) -> Optional[str]:
    """The URL HelmsDeep will send its requests to, or None if unknown.

    HelmsDeep appends its run type's endpoint (eg ``/ars/api/submit``) to
    ``--host``, so the host must be the service's base URL.
    """
    try:
        from helmsdeep import config as helmsdeep_config

        endpoint = helmsdeep_config.TARGETS[run_type]["endpoint"]
    except Exception:  # HelmsDeep missing or its registry changed shape.
        return None
    return f"{host.rstrip('/')}{endpoint}"


def check_host(
    run_type: str,
    host: str,
    logger: logging.Logger = logging.getLogger(__name__),
) -> None:
    """Say where the load will go, and warn when the host looks like it
    already carries the endpoint's path (eg ``https://example.org/ars`` for an
    ARS run, which would be sent to ``.../ars/ars/api/submit``)."""
    url = request_url(run_type, host)
    if url is None:
        return
    logger.info(f"HelmsDeep will send its requests to {url}")
    path = urlparse(host).path.rstrip("/")
    endpoint = url[len(host.rstrip("/")) :]
    if path and endpoint.startswith(path + "/"):
        parsed = urlparse(host)
        logger.warning(
            f"The host {host} ends in {path}, which HelmsDeep's endpoint "
            f"{endpoint} starts with again, so requests go to {url}. The host "
            f"should be the service's base URL, eg {parsed.scheme}://{parsed.netloc}."
        )


def _stream(cmd: List[str], logger: logging.Logger, extra_env: Optional[Dict] = None):
    """Run ``cmd``, logging its output as it comes. Returns the exit code and
    the last lines of output."""
    env = {**os.environ, **(extra_env or {})}
    # Python block-buffers a pipe, which would hold HelmsDeep's (and the locust
    # it starts') lines back for minutes at a time.
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_COLOR"] = "1"
    tail: deque = deque(maxlen=OUTPUT_TAIL_LINES)
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
        env=env,
    ) as proc:
        for line in proc.stdout:
            line = line.rstrip()
            if not line.strip():
                continue
            logger.info(f"{LOG_TAG} {line}")
            tail.append(line)
        exit_code = proc.wait()
    return exit_code, "\n".join(tail)


def _log_outcome(
    summary: Optional[Dict],
    exit_code: Optional[int],
    elapsed_s: float,
    prefix: str,
    logger: logging.Logger,
) -> None:
    """One line on how the run went, and a warning if nothing got through."""
    if not summary:
        return
    minutes = elapsed_s / 60
    stages = [s for s in summary.get("stages") or [] if isinstance(s, dict)]
    requests = sum(s.get("requests") or 0 for s in stages)
    errors = sum(s.get("errors") or 0 for s in stages)
    msc = summary.get("max_sustainable_concurrency")
    checkpoints = summary.get("checkpoints") or []
    verdict = "no checkpoints configured"
    if checkpoints:
        met = sum(1 for c in checkpoints if c.get("verdict") == "PASS")
        verdict = f"checkpoints {met}/{len(checkpoints)} met"
    logger.info(
        f"HelmsDeep finished in {minutes:.0f}m (exit {exit_code}): "
        f"{requests} requests, {errors} errors, max sustainable concurrency "
        f"{msc if msc is not None else 'none (no stage met the SLO)'}, "
        f"{verdict}. Outputs: {prefix}_*"
    )
    if requests and errors == requests:
        logger.warning(
            "Every request HelmsDeep sent failed: check the host it was "
            "pointed at, and the errors in its output above."
        )
    elif not requests:
        logger.warning("HelmsDeep completed no requests at all.")


def run_helmsdeep(
    helmsdeep_run_type: str,
    host: str,
    prefix: str,
    logger: logging.Logger = logging.getLogger(__name__),
    trapi_version: Optional[str] = None,
) -> Dict:
    """Run one HelmsDeep load test and collect what it wrote.

    ``trapi_version`` is the version HelmsDeep writes its queries in: TRAPI 2.0
    carries ``bypass_cache`` under the query's ``parameters``, 1.x at the top
    level. It goes over as ``HELMSDEEP_TRAPI_VERSION``, which releases before
    HelmsDeep 0.3.0 ignore (they always write the 1.x shape).

    Returns the parsed ``summary.json``, the HTML report, and the process exit
    code.

    The exit code is recorded but is NOT the pass/fail signal. HelmsDeep sets it
    to 1 on a missed checkpoint, but locust -- which HelmsDeep runs -- also exits
    1 whenever any single request failed, so a run with every checkpoint met and
    one 500 along the way still exits 1. The verdicts live in the summary
    (``checkpoints`` / ``checkpoints_passed``); whether the run happened at all
    is told by whether ``summary`` came back.
    """
    os.makedirs(os.path.dirname(prefix) or ".", exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        HELMSDEEP_MODULE,
        "--targets",
        helmsdeep_run_type,
        "--host",
        host,
        "--csv-prefix",
        prefix,
        # The harness is not a terminal session: HelmsDeep's sticky live footer
        # would fight the harness's own logging. It falls back to a periodic
        # plain status line, which is what we want in a log.
        "--no-live",
    ]
    logger.info(f"Running HelmsDeep: {' '.join(cmd)}")
    check_host(helmsdeep_run_type, host, logger)
    extra_env = {}
    if trapi_version:
        extra_env[TRAPI_VERSION_ENV] = trapi_version
        logger.info(f"HelmsDeep will write its queries in TRAPI {trapi_version}")

    error = None
    output = ""
    started = time.monotonic()
    try:
        exit_code, output = _stream(cmd, logger, extra_env)
    except OSError as e:
        exit_code = None
        error = f"Failed to launch HelmsDeep: {e}"
        logger.error(error)

    artifacts = _artifact_paths(prefix)
    summary_text = _read_text(artifacts.get("summary.json"), logger)
    summary = None
    if summary_text:
        try:
            summary = json.loads(summary_text)
        except ValueError as e:
            error = error or f"HelmsDeep wrote an unparseable summary.json: {e}"
            logger.error(error)
    elif error is None:
        error = (
            f"HelmsDeep exited {exit_code} without writing " f"{prefix}{SUMMARY_SUFFIX}"
        )
        logger.error(f"{error}; its output is above, tagged {LOG_TAG}")
    _log_outcome(summary, exit_code, time.monotonic() - started, prefix, logger)

    return {
        "runner": "helmsdeep",
        "helmsdeep_target": helmsdeep_run_type,
        "host": host,
        "prefix": prefix,
        "exit_code": exit_code,
        "summary": summary,
        "report_html": _read_text(artifacts.get("report.html"), logger),
        "artifacts": artifacts,
        "error": error,
        # Only kept when something went wrong; a successful run's stdout is a
        # long progress narration nobody needs in the report JSON.
        "output": _tail(output) if error else "",
    }


def run_performance_test(
    test: PerformanceTestCase,
    host: str,
    target: Optional[str] = None,
    output_dir: str = "test_results",
    profile: Optional[str] = None,
    logger: logging.Logger = logging.getLogger(__name__),
    trapi_version: Optional[str] = None,
) -> Dict:
    """Run a performance test case through HelmsDeep.

    ``target`` overrides the component specified in the test case, so the load
    test can be pointed at eg a locally running ARA regardless of what the test
    says. Any target that isn't the ARS is queried as an ARA.

    ``profile`` overrides the HelmsDeep query profile the test case asks for.

    ``trapi_version`` is the run's TRAPI version, for HelmsDeep to write its
    queries in.
    """
    component = str(target or (test.components or [ARS_COMPONENT])[0])
    component = component.split("infores:")[-1].lower()
    resolved_profile = resolve_profile(test, profile, logger)
    run_type = helmsdeep_target(component, resolved_profile)

    prefix = os.path.join(
        output_dir, f"helmsdeep_{_slugify(run_type)}_case_{_slugify(test.id)}"
    )
    results = run_helmsdeep(run_type, host, prefix, logger, trapi_version)
    results["component"] = component
    results["profile"] = resolved_profile
    return results


def describe_run(
    test: PerformanceTestCase,
    host: str,
    run_type: str,
    profile: str,
) -> str:
    """A human-readable plan for the run, for the Information Radiator log.

    The asset's TRAPI query is *not* what gets sent (HelmsDeep sends its own
    varied corpus), so logging the asset query would be misleading. Log what
    will actually happen instead.
    """
    try:
        from helmsdeep import config as helmsdeep_config

        cfg = helmsdeep_config.TARGETS[run_type]
        stages = [
            {"users": users, "spawn_rate": rate, "hold_s": hold}
            for users, rate, hold in cfg["stages"]
        ]
        plan: Dict = {
            "component": cfg["label"],
            "protocol": cfg["protocol"],
            "endpoint": f"{host}{cfg['endpoint']}",
            "corpus": cfg["corpus"],
            "p99_slo_ms": cfg["p99_slo_ms"],
            "cooldown_s": cfg.get("cooldown_s", 0),
            "stages": stages,
            "estimated_duration_s": helmsdeep_config.natural_duration_s(cfg),
            "checkpoints": cfg.get("checkpoints", []),
        }
    except Exception as e:  # HelmsDeep missing or its registry changed shape.
        plan = {"error": f"Could not describe the HelmsDeep plan: {e}"}

    return json.dumps(
        {
            "runner": "helmsdeep",
            "test_case": test.id,
            "helmsdeep_target": run_type,
            "profile": profile,
            "host": host,
            "plan": plan,
            "note": (
                "HelmsDeep owns the ramp and the corpus: the test case's "
                "test_run_time, spawn_rate, and assets do not shape this run."
            ),
        },
        indent=2,
    )


def checkpoint_verdicts(summary: Optional[Dict]) -> List[Dict]:
    """The checkpoint rows from a HelmsDeep summary, or an empty list.

    Only HelmsDeep's ``*_mixed`` run types define checkpoints; every other run
    type reports the knee and nothing to pass or fail.
    """
    if not summary:
        return []
    return list(summary.get("checkpoints") or [])
