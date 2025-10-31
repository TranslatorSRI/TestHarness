"""Translator Performance Test Runner."""
import logging
import time
from typing import Dict

import gevent
from locust import HttpUser, LoadTestShape, task
from locust.env import Environment
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


def run_locust_tests(
    host: str,
    test_query: Dict,
    test_run_time: int,
    spawn_rate: float,
    target: str,
):
    print("Starting locust testing")
    class RetryPoll(Exception):
        pass

    class QueryCompleted(Exception):
        pass

    class TestShape(LoadTestShape):
        time_limit = test_run_time
        user_spawn_rate = spawn_rate

        def tick(self):
            run_time = self.get_run_time()
            if run_time < self.time_limit:
                user_count = round(run_time, -1) * self.user_spawn_rate
                return (user_count, self.user_spawn_rate)

            return None

    class ARAUser(HttpUser):
        @task
        def send_query(self):
            with self.client.post("/query", json=test_query, catch_response=True) as response:
                # do stuff with the response
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Got a bad response: {response.status_code}")

    class ARSUser(HttpUser):
        @task
        def send_query(self):
            parent_pk = ""
            try:
                print("Sending query to ARS")
                with self.client.post("/ars/api/submit", json=test_query, catch_response=True) as response:
                    # do stuff with the response
                    if response.status_code != 201:
                        response.failure(f"Failed to start a query: {response.content}, {response.status_code}")
                        return

                    parent_pk = response.json().get("pk", "")
                    raise QueryCompleted()
            except QueryCompleted:
                pass

            start_time = time.time()
            now = time.time()
            total_time = 0
            try:
                while now - start_time <= 3600:
                    now = time.time()
                    try:
                        with self.client.get(f"/ars/api/messages/{parent_pk}?trace=y", catch_response=True, name=parent_pk) as response:
                            total_time = (now - start_time) * 1000
                            if response.status_code != 200:
                                self.environment.events.request.fire(
                                    request_type="GET_RESPONSE",
                                    name="/ars/api/messages",
                                    response_time=total_time,
                                    response_length=0,
                                    exception=f"Failed to poll the query: {response.content}, {response.status_code}",
                                    context=self.context(),
                                )
                            res = response.json()
                            status = res.get("status")
                            if status == "Error":
                                self.environment.events.request.fire(
                                    request_type="GET_RESPONSE",
                                    name="/ars/api/messages",
                                    response_time=total_time,
                                    response_length=0,
                                    exception=f"ARS had an error: {parent_pk}",
                                    context=self.context(),
                                )
                            elif status == "Done":
                                self.environment.events.request.fire(
                                    request_type="GET_RESPONSE",
                                    name="/ars/api/messages",
                                    response_time=total_time,
                                    response_length=len(response.content) if response.content else 0,
                                    exception=None,
                                    context=self.context(),
                                )
                                raise QueryCompleted()
                            time.sleep(5)
                            raise RetryPoll("Polling again")
                    except RetryPoll:
                        continue

                self.environment.events.request.fire(
                    request_type="GET_RESPONSE",
                    name="/ars/api/messages",
                    response_time=total_time,
                    response_length=0,
                    exception=f"ARS timed out: {parent_pk}",
                    context=self.context(),
                )
            except QueryCompleted:
                print("Query complete!")
                pass

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

    return {
        "stats": env.stats.serialize_stats(),
        "failures": env.stats.serialize_errors(),
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
    test_asset = AcceptanceTestAsset.model_validate({
        "id": "Asset_1",
        "name": "NeverShow: Iron (PUBCHEM) treats Aceruloplasminemia",
        "description": "NeverShow: Iron (PUBCHEM) treats Aceruloplasminemia",
        "tags": [],
        "test_runner_settings": [
            "inferred"
        ],
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
            {
                "parameter": "biolink_qualified_predicate",
                "value": "biolink:treats"
            },
            {
                "parameter": "biolink_object_aspect_qualifier",
                "value": ""
            },
            {
                "parameter": "biolink_object_direction_qualifier",
                "value": ""
            }
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
            "test_annotations": []
        }
    })
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
