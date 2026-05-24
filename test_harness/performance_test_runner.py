"""Translator Performance Test Runner."""

import logging
import time
from typing import Dict, List

import gevent
from gevent import GreenletExit
from locust import HttpUser, LoadTestShape, task
from locust.env import Environment
from locust.html import get_html_report
from locust.stats import stats_history, stats_printer
from translator_testing_model.datamodel.pydanticmodel import (
    AcceptanceTestAsset,
    ComponentEnum,
    PerformanceTestCase,
    TestEnvEnum,
    TestObjectiveEnum,
)

from test_harness.runner.generate_query import generate_query
from test_harness.runner.query_runner import QueryRunner, env_map


# Custom request_type values used to distinguish layers of the test in stats.
# Locust groups stats by (method, name); using these as the "method" lets us
# pull each layer out cleanly in the result collector.
SUBMIT_TYPE = "POST"
POLL_TYPE = "GET"
QUERY_TYPE = "QUERY"

# Single names per layer so stats aggregate across all queries instead of
# one row per parent_pk.
SUBMIT_NAME = "submit_query"
POLL_NAME = "poll_status"
# The /trace poll only returns status metadata; the final TRAPI message lives
# at the merged_version PK. Fetch it on completion so we can record the size
# of the actual response.
MERGED_FETCH_NAME = "fetch_merged"

# Per-outcome names for the end-to-end QUERY event. Distinct names give us
# a count for each outcome directly out of Locust's stats serialization.
OUTCOME_COMPLETED = "ars_query_completed"
OUTCOME_ERRORED = "ars_query_errored"
OUTCOME_POLLING_FAILED = "ars_query_polling_failed"
OUTCOME_TIMED_OUT = "ars_query_timed_out"
OUTCOME_ABANDONED = "ars_query_abandoned"

ARA_QUERY_COMPLETED = "ara_query_completed"
ARA_QUERY_FAILED = "ara_query_failed"

POLL_INTERVAL_SECONDS = 5


def run_locust_tests(
    host: str,
    test_query: Dict,
    test_run_time: int,
    spawn_rate: float,
    target: str,
):
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
                user_count = round(run_time, -1) * self.user_spawn_rate
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
                        failure_reason = (
                            f"Got a bad response: {response.status_code}"
                        )
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
            fallback = (
                len(trace_response.content) if trace_response.content else 0
            )
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
                        failure_reason = (
                            f"Test ended while polling {parent_pk}"
                        )
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
                            failure_reason = (
                                f"Non-JSON poll body for {parent_pk}"
                            )
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
                        failure_reason = (
                            f"Test ended while polling {parent_pk}"
                        )
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

    # Create environment
    USER_TYPE_MAP = {
        "ars": ARSUser,
        "aragorn": ARAUser,
        "arax": ARAUser,
        "bte": ARAUser,
    }
    user_class = USER_TYPE_MAP.get(target, ARAUser)
    env = Environment(user_classes=[user_class], host=host, shape_class=TestShape())
    runner = env.create_local_runner()

    # Capture per-query response sizes (one entry per query, keyed by
    # outcome name) so the report can flag cases where queries reported the
    # same status but came back with different payload sizes.
    query_response_sizes: Dict[str, List[int]] = {}

    def _record_query_size(
        request_type, name, response_time, response_length, exception, context, **kwargs
    ):
        if request_type != QUERY_TYPE:
            return
        query_response_sizes.setdefault(name, []).append(response_length or 0)

    env.events.request.add_listener(_record_query_size)

    # Start stats printer
    gevent.spawn(stats_printer(env.stats))
    gevent.spawn(stats_history, runner)

    # Start test
    runner.start_shape()

    # Run for specified duration
    gevent.spawn_later(test_run_time, runner.quit)

    # Wait for completion
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
        "query_response_sizes": query_response_sizes,
        "stats_history": list(env.runner.stats.history),
        "summary_html": summary_html,
    }


def run_performance_test(test: PerformanceTestCase, test_query: Dict, host: str):
    """Wrapper function to run load tests with custom parameters"""
    target = test.components[0]

    results = run_locust_tests(
        host,
        test_query,
        test.test_run_time,
        test.spawn_rate,
        target,
    )

    return results


def initialize():
    test_asset = AcceptanceTestAsset.model_validate(
        {
            "id": "Asset_1",
            "name": "NeverShow: Iron (PUBCHEM) treats Aceruloplasminemia",
            "description": "NeverShow: Iron (PUBCHEM) treats Aceruloplasminemia",
            "tags": [],
            "test_runner_settings": ["inferred"],
            "input_id": "MONDO:0011426",
            "input_name": "Aceruloplasminemia",
            "input_category": "biolink:Disease",
            "predicate_id": "biolink:treats",
            "predicate_name": "treats",
            "output_id": "PUBCHEM.COMPOUND:23925",
            "output_name": "Iron (PUBCHEM)",
            "output_category": "biolink:ChemicalEntity",
            "association": None,
            "qualifiers": [
                {"parameter": "biolink_qualified_predicate", "value": "biolink:treats"},
                {"parameter": "biolink_object_aspect_qualifier", "value": ""},
                {"parameter": "biolink_object_direction_qualifier", "value": ""},
            ],
            "expected_output": "NeverShow",
            "test_issue": None,
            "semantic_severity": None,
            "in_v1": None,
            "well_known": False,
            "test_reference": None,
            "test_metadata": {
                "id": "1",
                "name": None,
                "description": None,
                "tags": [],
                "test_runner_settings": [],
                "test_source": "SMURF",
                "test_reference": "https://github.com/NCATSTranslator/Feedback/issues/506",
                "test_objective": "AcceptanceTest",
                "test_annotations": [],
            },
        }
    )
    test = PerformanceTestCase(
        id="1",
        name="ExamplePerformanceTest",
        description="Iron treats Aceruloplasminemia",
        tags=[],
        test_runner_settings=["inferred"],
        test_run_time=20,
        spawn_rate=0.1,
        query_type=None,
        test_assets=[test_asset],
        preconditions=[],
        trapi_template=None,
        test_case_objective=TestObjectiveEnum.QuantitativeTest,
        test_case_source=None,
        test_case_predicate_name="treats",
        test_case_predicate_id="biolink_treats",
        test_case_input_id="MONDO:0011426",
        qualifiers=[],
        input_category="biolink:Disease",
        output_category=None,
        components=[ComponentEnum.ars],
        test_env=TestEnvEnum.ci,
    )
    logger = logging.getLogger(__name__)
    query_runner = QueryRunner(logger)
    query_runner.retrieve_registry("1.6.0")
    # print(query_runner.registry)

    host = query_runner.registry[env_map[test.test_env]][test.components[0]][0]["url"]

    test_query = generate_query(test.test_assets[0])

    results = run_performance_test(
        test,
        test_query,
        host,
    )

    print(results)


if __name__ == "__main__":
    initialize()
