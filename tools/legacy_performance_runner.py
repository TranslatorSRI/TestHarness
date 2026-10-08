"""One-off local runner for the pre-HelmsDeep performance test.

This is the in-harness locust runner that ``performance_test_runner.py`` used
before it was replaced by HelmsDeep (commit 9aee8c5), lifted out as a
standalone script so it can be run by hand against a single service. It does
not touch the Reporter, Slack, or the test suites, and it only needs locust::

    pip install locust==2.38.1

The load shape is the old one: a linear ramp where the user count at time ``t``
is ``round(t, -1) * spawn_rate``, running for ``run_time`` seconds. With the
defaults (1200s, 0.1 users/s) that is 20 minutes, climbing by one user every
10 seconds to ~120 concurrent users at the end. Every user sends the same TRAPI
query in a loop -- by default the one the scheduled run used (see
``DEFAULT_QUERY``).

Examples::

    # ARS (async submit / poll / fetch merged message)
    python tools/legacy_performance_runner.py --host https://ars.ci.transltr.io

    # Any ARA (blocking POST /query), with your own query
    python tools/legacy_performance_runner.py --host http://localhost:8080 \\
        --target ara --query my_query.json

Results land in ``--output_dir``: ``<prefix>_results.json`` (locust stats,
failures, per-outcome response sizes and stats history) and
``<prefix>_report.html`` (locust's own HTML report).
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, List

import gevent
from gevent import GreenletExit
from locust import HttpUser, LoadTestShape, task
from locust.env import Environment
from locust.html import get_html_report
from locust.stats import stats_history, stats_printer

# Custom request_type values used to distinguish layers of the test in stats.
SUBMIT_TYPE = "POST"
POLL_TYPE = "GET"
QUERY_TYPE = "QUERY"

# Single names per layer so stats aggregate across all queries instead of
# one row per parent_pk.
SUBMIT_NAME = "submit_query"
POLL_NAME = "poll_status"
MERGED_FETCH_NAME = "fetch_merged"

# Per-outcome names for the end-to-end QUERY event.
OUTCOME_COMPLETED = "ars_query_completed"
OUTCOME_ERRORED = "ars_query_errored"
OUTCOME_POLLING_FAILED = "ars_query_polling_failed"
OUTCOME_TIMED_OUT = "ars_query_timed_out"
OUTCOME_ABANDONED = "ars_query_abandoned"

ARA_QUERY_COMPLETED = "ara_query_completed"
ARA_QUERY_FAILED = "ara_query_failed"

POLL_INTERVAL_SECONDS = 5

# The query the scheduled run actually sent: NCATSTranslator/Tests
# test_suites/performance_tests.json, TestCase_87 / Asset_668 -- an inferred
# MVP1 "what treats Ehlers-Danlos Syndrome?" (MONDO:0017314) against the CI ARS.
# That suite only ever had this one asset, so it is the whole curie list. This
# is generate_query()'s output for that asset.
DEFAULT_QUERY = {
    "message": {
        "query_graph": {
            "nodes": {
                "ON": {
                    "categories": ["biolink:Disease"],
                    "ids": ["MONDO:0017314"],
                },
                "SN": {"categories": ["biolink:ChemicalEntity"]},
            },
            "edges": {
                "t_edge": {
                    "object": "ON",
                    "subject": "SN",
                    "predicates": ["biolink:treats"],
                    "knowledge_type": "inferred",
                }
            },
        }
    }
}


def run_locust_tests(
    host: str,
    test_query: Dict,
    test_run_time: int,
    spawn_rate: float,
    target: str,
) -> Dict:
    print("Starting locust testing")

    test_started_at = time.time()

    def remaining_test_time() -> float:
        """Seconds left in the test window; clamped at 0."""
        return max(0.0, test_run_time - (time.time() - test_started_at))

    class TestShape(LoadTestShape):
        time_limit = test_run_time
        user_spawn_rate = spawn_rate

        def tick(self):
            run_time = self.get_run_time()
            if run_time < self.time_limit:
                # round() rather than int(): 60 * 0.1 is 6.000000000000001 but
                # 30 * 0.1 is 3.0000000000000004 and others land just under.
                user_count = round(round(run_time, -1) * self.user_spawn_rate)
                return (user_count, self.user_spawn_rate)

            return None

    def fire_query_event(env, name, response_time_ms, exception=None, length=0):
        env.events.request.fire(
            request_type=QUERY_TYPE,
            name=name,
            response_time=response_time_ms,
            response_length=length,
            exception=exception,
            context={},
        )

    class ARAUser(HttpUser):
        @task
        def send_query(self):
            query_started = time.time()
            outcome = ARA_QUERY_FAILED
            failure_reason = "ARA query did not complete"
            response_length = 0
            try:
                with self.client.post(
                    "/query",
                    json=test_query,
                    catch_response=True,
                    name=SUBMIT_NAME,
                ) as response:
                    if response.status_code == 200:
                        response.success()
                        outcome = ARA_QUERY_COMPLETED
                        failure_reason = None
                        response_length = (
                            len(response.content) if response.content else 0
                        )
                    else:
                        failure_reason = f"Got a bad response: {response.status_code}"
                        response.failure(failure_reason)
            except GreenletExit:
                outcome = ARA_QUERY_FAILED
                failure_reason = "Test stopped before ARA query finished"
                raise
            finally:
                elapsed_ms = (time.time() - query_started) * 1000
                fire_query_event(
                    self.environment,
                    outcome,
                    elapsed_ms,
                    exception=failure_reason,
                    length=response_length,
                )

    class ARSUser(HttpUser):
        def _fetch_merged_size(self, merged_pk, trace_response) -> int:
            """Pull the actual final-response byte size for a completed query.

            Falls back to the trace response's content length if the merged
            message can't be fetched, so the QUERY event still records a size.
            """
            fallback = len(trace_response.content) if trace_response.content else 0
            if not merged_pk:
                return fallback
            with self.client.get(
                f"/ars/api/messages/{merged_pk}",
                catch_response=True,
                name=MERGED_FETCH_NAME,
            ) as merged_res:
                if merged_res.status_code != 200:
                    merged_res.failure(
                        f"Failed to fetch merged {merged_pk}: "
                        f"{merged_res.status_code}"
                    )
                    return fallback
                merged_res.success()
                return len(merged_res.content) if merged_res.content else 0

        @task
        def send_query(self):
            query_started = time.time()
            outcome = OUTCOME_ABANDONED
            failure_reason = "Test ended before query reached a terminal state"
            response_length = 0
            parent_pk = ""

            try:
                # Submit the query.
                with self.client.post(
                    "/ars/api/submit",
                    json=test_query,
                    catch_response=True,
                    name=SUBMIT_NAME,
                ) as response:
                    if response.status_code != 201:
                        failure_reason = (
                            f"Failed to start a query: "
                            f"{response.status_code} {response.content!r}"
                        )
                        response.failure(failure_reason)
                        outcome = OUTCOME_POLLING_FAILED
                        return
                    response.success()
                    parent_pk = response.json().get("pk", "")

                if not parent_pk:
                    failure_reason = "ARS submit returned no parent_pk"
                    outcome = OUTCOME_POLLING_FAILED
                    return

                # Poll until terminal state, the test window closes, or the
                # greenlet is killed.
                while True:
                    if remaining_test_time() <= 0:
                        outcome = OUTCOME_ABANDONED
                        failure_reason = f"Test ended while polling {parent_pk}"
                        return

                    with self.client.get(
                        f"/ars/api/messages/{parent_pk}?trace=y",
                        catch_response=True,
                        name=POLL_NAME,
                    ) as response:
                        if response.status_code != 200:
                            failure_reason = (
                                f"Failed to poll {parent_pk}: "
                                f"{response.status_code} {response.content!r}"
                            )
                            response.failure(failure_reason)
                            outcome = OUTCOME_POLLING_FAILED
                            return
                        response.success()

                        try:
                            res = response.json()
                        except ValueError:
                            failure_reason = f"Non-JSON poll body for {parent_pk}"
                            outcome = OUTCOME_POLLING_FAILED
                            return

                        status = res.get("status")
                        if status == "Done":
                            outcome = OUTCOME_COMPLETED
                            failure_reason = None
                            response_length = self._fetch_merged_size(
                                res.get("merged_version"),
                                response,
                            )
                            return
                        if status == "Error":
                            failure_reason = f"ARS reported Error for {parent_pk}"
                            outcome = OUTCOME_ERRORED
                            return

                    # Don't sleep past the end of the test window.
                    sleep_for = min(POLL_INTERVAL_SECONDS, remaining_test_time())
                    if sleep_for <= 0:
                        outcome = OUTCOME_ABANDONED
                        failure_reason = f"Test ended while polling {parent_pk}"
                        return
                    time.sleep(sleep_for)
            except GreenletExit:
                # Runner is shutting down. Record the in-flight query and
                # re-raise so locust stops the user cleanly.
                outcome = OUTCOME_ABANDONED
                failure_reason = (
                    f"Greenlet killed while query {parent_pk} was in flight"
                )
                raise
            finally:
                elapsed_ms = (time.time() - query_started) * 1000
                fire_query_event(
                    self.environment,
                    outcome,
                    elapsed_ms,
                    exception=failure_reason,
                    length=response_length,
                )

    user_class = ARSUser if target == "ars" else ARAUser
    env = Environment(user_classes=[user_class], host=host, shape_class=TestShape())
    runner = env.create_local_runner()

    # Capture per-query response sizes (one entry per query, keyed by
    # outcome name) so cases where queries reported the same status but came
    # back with different payload sizes stand out.
    query_response_sizes: Dict[str, List[int]] = {}

    def _record_query_size(
        request_type, name, response_time, response_length, exception, context, **kwargs
    ):
        if request_type != QUERY_TYPE:
            return
        query_response_sizes.setdefault(name, []).append(response_length or 0)

    env.events.request.add_listener(_record_query_size)

    gevent.spawn(stats_printer(env.stats))
    gevent.spawn(stats_history, runner)

    runner.start_shape()
    gevent.spawn_later(test_run_time, runner.quit)
    runner.greenlet.join()
    runner.quit()

    print("Done with locust testing!")

    try:
        summary_html = get_html_report(env, show_download_link=False)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to render Locust HTML report: %s", e
        )
        summary_html = None

    return {
        "stats": env.stats.serialize_stats(),
        "failures": env.stats.serialize_errors(),
        "test_run_time": test_run_time,
        "spawn_rate": spawn_rate,
        "target": target,
        "host": host,
        "query_response_sizes": query_response_sizes,
        "stats_history": list(env.runner.stats.history),
        "summary_html": summary_html,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run the old linear-ramp locust performance test against one service."
        )
    )
    parser.add_argument(
        "--host",
        required=True,
        help="Base URL of the service, eg https://ars.ci.transltr.io",
    )
    parser.add_argument(
        "--target",
        choices=("ars", "ara"),
        default="ars",
        help=(
            "ars: async submit/poll/fetch-merged protocol. "
            "ara: blocking POST /query. (default: ars)"
        ),
    )
    parser.add_argument(
        "--query",
        help=(
            "Path to a TRAPI query JSON file. Defaults to the old performance "
            "suite's query (inferred treats MONDO:0017314)."
        ),
    )
    parser.add_argument(
        "--run_time",
        type=int,
        default=1200,
        help="Test duration in seconds. (default: 1200, ie 20 minutes)",
    )
    parser.add_argument(
        "--spawn_rate",
        type=float,
        default=0.1,
        help="Users added per second of the ramp. (default: 0.1)",
    )
    parser.add_argument(
        "--output_dir",
        default="test_results",
        help="Where to write the results JSON and HTML report.",
    )
    args = parser.parse_args()

    if args.query:
        with open(args.query, encoding="utf-8") as f:
            test_query = json.load(f)
    else:
        test_query = DEFAULT_QUERY

    peak_users = round(round(args.run_time, -1) * args.spawn_rate)
    print(
        f"Ramping {args.target} at {args.host} for {args.run_time}s, "
        f"{args.spawn_rate} users/s, up to ~{peak_users} users."
    )

    results = run_locust_tests(
        args.host.rstrip("/"),
        test_query,
        args.run_time,
        args.spawn_rate,
        args.target,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    prefix = os.path.join(
        args.output_dir,
        f"legacy_perf_{args.target}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    summary_html = results.pop("summary_html")
    with open(f"{prefix}_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Wrote {prefix}_results.json")
    if summary_html:
        with open(f"{prefix}_report.html", "w", encoding="utf-8") as f:
            f.write(summary_html)
        print(f"Wrote {prefix}_report.html")


if __name__ == "__main__":
    main()
