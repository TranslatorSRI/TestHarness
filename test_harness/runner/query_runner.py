"""Translator Test Query Runner."""

import logging
import time
from typing import Dict, List, Optional, Tuple, Union

import httpx
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestCase,
    TestCase,
)

from test_harness.runner.generate_query import generate_query
from test_harness.runner.smart_api_registry import retrieve_registry_from_smartapi
from test_harness.trapi import DEFAULT_TRAPI_VERSION, trapi_minor_version
from test_harness.utils import hash_test_asset, normalize_curies

MAX_QUERY_TIME = 600
MAX_ARA_TIME = 360

env_map = {
    "dev": "development",
    "ci": "staging",
    "test": "testing",
    "prod": "production",
}


class QueryRunner:
    """Translator Test Query Runner."""

    def __init__(
        self,
        logger: logging.Logger,
        target_url: Optional[str] = None,
        target: Optional[str] = None,
        trapi_version: str = DEFAULT_TRAPI_VERSION,
        query_parameters: Optional[Dict] = None,
    ):
        """Initialize the Query Runner.

        ``trapi_version`` is the TRAPI version queries are written in, and the
        version of the services picked from the SmartAPI registry.

        ``query_parameters`` go out with every query (see
        ``trapi.validate_query_parameters``).

        ``target_url`` and ``target`` override the target service specified in
        the tests themselves: when given, every query is sent to ``target_url``
        instead of the services registered for the test's components, and the
        service is identified as ``target`` (an infores curie, with or without
        the ``infores:`` prefix). This is how the harness is pointed at a
        locally running ARA/ARS before it is released and deployed.
        """
        self.registry = {}
        self.logger = logger
        self.target_url = target_url.rstrip("/") if target_url else None
        if target is not None and not target.startswith("infores:"):
            target = f"infores:{target}"
        self.target_infores = target
        # fail on an unsupported version now, not once per generated query
        trapi_minor_version(trapi_version)
        self.trapi_version = trapi_version
        self.query_parameters = query_parameters

    def retrieve_registry(self, trapi_version: str):
        if self.target_url is not None:
            # all queries go to the override target, so the registry of
            # deployed services is never consulted
            self.logger.info(
                "Target override in use; skipping SmartAPI registry retrieval."
            )
            return
        self.registry = retrieve_registry_from_smartapi(trapi_version)

    def run_query(
        self, query_hash, message, base_url, infores
    ) -> Tuple[int, Dict[str, dict], Dict[str, str]]:
        """Generate and run a single TRAPI query against a component."""
        # wait for opening in semaphore before sending next query
        responses = {}
        pks = {}
        # handle some outlier urls
        if infores == "infores:ars":
            url = base_url + "/ars/api/submit"
        elif infores == "infores:sri-answer-appraiser":
            url = base_url + "/get_appraisal"
        elif infores == "infores:sri-node-normalizer":
            url = base_url + "/get_normalized_nodes"
        elif "annotator" in base_url:
            url = base_url
            pass
        else:
            url = base_url + "/query"
        # send message
        response = {}
        status_code = 418
        elapsed_s = None
        with httpx.Client(timeout=600) as client:
            try:
                start_time = time.monotonic()
                res = client.post(url, json=message)
                elapsed_s = time.monotonic() - start_time
                status_code = res.status_code
                res.raise_for_status()
                response = res.json()
            except Exception as e:
                self.logger.error(f"Something went wrong: {e}")

        if infores == "infores:ars":
            parent_pk = response.get("pk", "")
            if status_code > 299 or not parent_pk:
                # The ARS never accepted the query (eg a 502 from submit), so
                # there's nothing to poll and no ARA ever saw it. Record the
                # failure against the ARS only; the ARAs are left out and get
                # reported as skipped.
                self.logger.error(
                    f"ARS query submission failed with status code {status_code}."
                )
                responses["ars"] = {
                    "response": response,
                    "status_code": status_code,
                    "submission_failed": True,
                }
                return query_hash, responses, pks
            # handle the ARS polling
            ars_responses, pks = self.get_ars_responses(parent_pk, base_url)
            responses.update(ars_responses)
        else:
            single_infores = infores.split("infores:")[1]
            # TODO: normalize this response
            responses[single_infores] = {
                "response": response,
                "status_code": status_code,
                # Only a direct query is timed: the ARS's own submit returns
                # straight away, and its ARAs are only seen through polling.
                "elapsed_s": elapsed_s,
            }

        return query_hash, responses, pks

    def get_ars_child_response(
        self,
        child_pk: str,
        base_url: str,
        infores: str,
    ):
        """Given a child pk, get response from ARS.

        Each call gets its own poll deadline so that one slow / timed-out ARA
        doesn't cause subsequently checked ARAs to be marked as timed out
        before they're even given a chance to respond.
        """
        self.logger.info(f"Getting response for {infores}...")

        start_time = time.time()
        current_time = start_time

        response = None
        status = 500
        try:
            # while we stay within the query max time
            while current_time - start_time <= MAX_ARA_TIME:
                # get query status of child query
                with httpx.Client(timeout=30) as client:
                    res = client.get(f"{base_url}/ars/api/messages/{child_pk}")
                    res.raise_for_status()
                    response = res.json()
                    status = response.get("fields", {}).get("status")
                    if status == "Done":
                        break
                    elif status == "Error" or status == "Unknown":
                        # query errored, need to capture
                        break
                    elif status == "Running":
                        self.logger.info(f"{infores} is still Running...")
                        current_time = time.time()
                        time.sleep(10)
                    else:
                        self.logger.info(f"Got unhandled status: {status}")
                        break
            else:
                self.logger.warning(
                    f"Timed out getting ARS child messages after {MAX_ARA_TIME / 60} minutes."
                )

            # add response to output
            if response is not None:
                status_code = response.get("fields", {}).get("code", 410)
                self.logger.info(
                    f"Got reponse for {infores} with status code {status_code}."
                )
                response = {
                    "response": response.get("fields", {}).get(
                        "data", {"message": {"results": []}}
                    ),
                    "status_code": status_code,
                }
            else:
                self.logger.warning(f"Got error from {infores}")
                response = {
                    "response": {"message": {"results": []}},
                    "status_code": status,
                }
        except Exception as e:
            self.logger.error(
                f"Getting ARS child response ({infores}) failed with: {e}"
            )
            response = {
                "response": {"message": {"results": []}},
                "status_code": status,
            }

        return infores, response

    def get_ars_responses(
        self, parent_pk: str, base_url: str
    ) -> Tuple[Dict[str, dict], Dict[str, str]]:
        """Given a parent pk, get responses for all ARS things."""
        responses = {}
        pks = {
            "parent_pk": parent_pk,
        }
        with httpx.Client(timeout=30) as client:
            # Get all children queries
            # TODO: race condition in the ARS that will hopefully get fixed
            time.sleep(10)
            res = client.get(f"{base_url}/ars/api/messages/{parent_pk}?trace=y")
            res.raise_for_status()
            response = res.json()

        start_time = time.time()
        child_responses = []
        for child in response.get("children", []):
            child_pk = child["message"]
            infores = child["actor"]["inforesid"].split("infores:")[1]
            # add child pk
            pks[infores] = child_pk
            child_responses.append(
                self.get_ars_child_response(child_pk, base_url, infores)
            )

        for child_response in child_responses:
            infores, response = child_response
            responses[infores] = response

        try:
            # After getting all individual ARA responses, get and save the merged version
            current_time = time.time()
            while current_time - start_time <= MAX_QUERY_TIME:
                with httpx.Client(timeout=30) as client:
                    res = client.get(f"{base_url}/ars/api/messages/{parent_pk}?trace=y")
                    res.raise_for_status()
                    response = res.json()
                    status = response.get("status")
                    if status == "Done" or status == "Error":
                        merged_pk = response.get("merged_version")
                        if merged_pk is None:
                            self.logger.error(
                                f"Failed to get the ARS merged message from pk: {parent_pk}."
                            )
                            pks["ars"] = "None"
                            responses["ars"] = {
                                "response": {"message": {"results": []}},
                                "status_code": 410,
                            }
                        else:
                            # add final ars pk
                            pks["ars"] = merged_pk
                            # get full merged pk
                            res = client.get(f"{base_url}/ars/api/messages/{merged_pk}")
                            res.raise_for_status()
                            merged_message = res.json()
                            responses["ars"] = {
                                "response": merged_message.get("fields", {}).get(
                                    "data", {"message": {"results": []}}
                                ),
                                "status_code": merged_message.get("fields", {}).get(
                                    "code", 410
                                ),
                            }
                            self.logger.info("Got ARS merged message!")
                        break
                    else:
                        self.logger.info("ARS merging not done, waiting...")
                        current_time = time.time()
                        time.sleep(10)
            else:
                self.logger.warning(
                    f"ARS merging took greater than {MAX_QUERY_TIME / 60} minutes."
                )
                pks["ars"] = "None"
                responses["ars"] = {
                    "response": {"message": {"results": []}},
                    "status_code": 598,
                }
        except Exception as e:
            self.logger.warning(f"Failed to get ARS merged message: {e}")
            pks["ars"] = "None"
            responses["ars"] = {
                "response": {"message": {"results": []}},
                "status_code": 500,
            }

        with httpx.Client(timeout=30) as client:
            # retain this response for testing
            res = client.post(f"{base_url}/ars/api/retain/{parent_pk}")
            res.raise_for_status()
            retain_response = res.json()
            if not retain_response.get("success"):
                self.logger.error(
                    f"Failed to retain the query response: {retain_response}"
                )

        return responses, pks

    def run_queries(
        self,
        test_case: Union[TestCase, PathfinderTestCase],
    ) -> Tuple[Dict[int, dict], Dict[str, str]]:
        """Run all queries specified in a Test Case."""
        # normalize all the curies in a test case
        normalized_curies = normalize_curies(test_case, self.logger)
        # TODO: figure out the right way to handle input category wrt normalization

        queries: Dict[int, dict] = {}
        for test_asset in test_case.test_assets:
            if isinstance(test_case, PathfinderTestCase):
                test_asset.source_input_id = normalized_curies[
                    test_asset.source_input_id
                ]
                test_asset.target_input_id = normalized_curies[
                    test_asset.target_input_id
                ]
            else:
                test_asset.input_id = normalized_curies[test_asset.input_id]
            # TODO: make this better
            asset_hash = hash_test_asset(test_asset)
            if asset_hash not in queries:
                # generate query
                try:
                    query = generate_query(
                        test_asset, self.trapi_version, self.query_parameters
                    )
                    queries[asset_hash] = {
                        "query": query,
                        "responses": {},
                        "pks": {},
                    }
                except Exception as e:
                    self.logger.warning(e)

        if self.target_url is not None:
            # ignore the components specified in the test case and send every
            # query straight to the override target
            self.logger.info(
                f"Overriding test-specified components; sending queries to {self.target_infores} at {self.target_url}"
            )
            self._send_queries(
                [{"url": self.target_url, "infores": self.target_infores}],
                queries,
            )
        else:
            # send queries to a single type of component at a time
            for component in test_case.components:
                # component = "ara"
                # loop over all specified components, i.e. ars, ara, kp, utilities
                self.logger.info(
                    f"Sending queries to {self.registry[env_map[test_case.test_env]][component]}"
                )
                self._send_queries(
                    self.registry[env_map[test_case.test_env]][component],
                    queries,
                )

        return queries, normalized_curies

    def _send_queries(self, services: List[Dict[str, str]], queries: Dict[int, dict]):
        """Send all generated queries to each of the given services."""
        try:
            all_responses = []
            for service in services:
                for query_hash, query in queries.items():
                    all_responses.append(
                        self.run_query(
                            query_hash,
                            query["query"],
                            service["url"],
                            service["infores"],
                        )
                    )
                for query_hash, responses, pks in all_responses:
                    queries[query_hash]["responses"].update(responses)
                    queries[query_hash]["pks"].update(pks)
        except Exception as e:
            self.logger.error(f"Something went wrong with the queries: {e}")
