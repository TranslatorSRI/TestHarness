"""Translator Test Query Runner."""

import asyncio
import httpx
import logging
import time
from typing import Tuple, Dict

from translator_testing_model.datamodel.pydanticmodel import TestCase
from test_harness.runner.smart_api_registry import retrieve_registry_from_smartapi
from test_harness.runner.generate_query import generate_query
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

    def __init__(self, logger: logging.Logger):
        self.registry = {}
        self.logger = logger

    async def retrieve_registry(self, trapi_version: str):
        self.registry = await retrieve_registry_from_smartapi(trapi_version)

    async def run_query(
        self, query_hash, semaphore, message, base_url, infores
    ) -> Tuple[int, Dict[str, dict], Dict[str, str]]:
        """Generate and run a single TRAPI query against a component."""
        # wait for opening in semaphore before sending next query
        responses = {}
        pks = {}
        async with semaphore:
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
            async with httpx.AsyncClient(timeout=600) as client:
                try:
                    res = await client.post(url, json=message)
                    status_code = res.status_code
                    res.raise_for_status()
                    response = res.json()
                except Exception as e:
                    self.logger.error(f"Something went wrong: {e}")

            if infores == "infores:ars":
                # handle the ARS polling
                parent_pk = response.get("pk", "")
                ars_responses, pks = await self.get_ars_responses(parent_pk, base_url)
                responses.update(ars_responses)
            else:
                single_infores = infores.split("infores:")[1]
                # TODO: normalize this response
                responses[single_infores] = {
                    "response": response,
                    "status_code": status_code,
                }

        return query_hash, responses, pks

    async def run_queries(
        self,
        test_case: TestCase,
        concurrency: int = 1,  # for performance testing
    ) -> Tuple[Dict[int, dict], Dict[str, str]]:
        """Run all queries specified in a Test Case."""
        # normalize all the curies in a test case
        normalized_curies = await normalize_curies(test_case, self.logger)
        # TODO: figure out the right way to handle input category wrt normalization

        queries: Dict[int, dict] = {}
        for test_asset in test_case.test_assets:
            test_asset.input_id = normalized_curies[test_asset.input_id]
            # TODO: make this better
            asset_hash = hash_test_asset(test_asset)
            if asset_hash not in queries:
                # generate query
                query = generate_query(test_asset)
                queries[asset_hash] = {
                    "query": query,
                    "responses": {},
                    "pks": {},
                }

        # send queries to a single type of component at a time
        for component in test_case.components:
            # component = "ara"
            # loop over all specified components, i.e. ars, ara, kp, utilities
            semaphore = asyncio.Semaphore(concurrency)
            self.logger.info(
                f"Sending queries to {self.registry[env_map[test_case.test_env]][component]}"
            )
            tasks = [
                asyncio.create_task(
                    self.run_query(
                        query_hash,
                        semaphore,
                        query["query"],
                        service["url"],
                        service["infores"],
                    )
                )
                for service in self.registry[env_map[test_case.test_env]][component]
                for query_hash, query in queries.items()
            ]
            try:
                all_responses = await asyncio.gather(*tasks, return_exceptions=True)
                for query_hash, responses, pks in all_responses:
                    queries[query_hash]["responses"].update(responses)
                    queries[query_hash]["pks"].update(pks)
            except Exception as e:
                self.logger.error(f"Something went wrong with the queries: {e}")

        return queries, normalized_curies

    async def get_ars_child_response(
        self, child_pk: str, base_url: str, infores: str, start_time: float,
    ):
        """Given a child pk, get response from ARS."""
        self.logger.info(f"Getting response for {infores}...")

        current_time = time.time()

        response = None
        status = 500
        try:
            # while we stay within the query max time
            while current_time - start_time <= MAX_ARA_TIME:
                # get query status of child query
                async with httpx.AsyncClient(timeout=30) as client:
                    res = await client.get(f"{base_url}/ars/api/messages/{child_pk}")
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
                        await asyncio.sleep(10)
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
            self.logger.error(f"Getting ARS child response ({infores}) failed with: {e}")
            response = {
                "response": {"message": {"results": []}},
                "status_code": status,
            }
        
        return infores, response

    async def get_ars_responses(
        self, parent_pk: str, base_url: str
    ) -> Tuple[Dict[str, dict], Dict[str, str]]:
        """Given a parent pk, get responses for all ARS things."""
        responses = {}
        pks = {
            "parent_pk": parent_pk,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            # retain this response for testing
            # res = await client.post(f"{base_url}/ars/api/retain/{parent_pk}")
            # res.raise_for_status()
            # Get all children queries
            res = await client.get(f"{base_url}/ars/api/messages/{parent_pk}?trace=y")
            res.raise_for_status()
            response = res.json()

        start_time = time.time()
        child_tasks = []
        for child in response.get("children", []):
            child_pk = child["message"]
            infores = child["actor"]["inforesid"].split("infores:")[1]
            # add child pk
            pks[infores] = child_pk
            child_tasks.append(self.get_ars_child_response(child_pk, base_url, infores, start_time))
            
        child_responses = await asyncio.gather(*child_tasks)

        for child_response in child_responses:
            infores, response = child_response
            responses[infores] = response

        # After getting all individual ARA responses, get and save the merged version
        current_time = time.time()
        while current_time - start_time <= MAX_QUERY_TIME:
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.get(
                    f"{base_url}/ars/api/messages/{parent_pk}?trace=y"
                )
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
                        res = await client.get(
                            f"{base_url}/ars/api/messages/{merged_pk}"
                        )
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
                    await asyncio.sleep(10)
        else:
            self.logger.warning(
                f"ARS merging took greater than {MAX_QUERY_TIME / 60} minutes."
            )
            pks["ars"] = "None"
            responses["ars"] = {
                "response": {"message": {"results": []}},
                "status_code": 410,
            }

        return responses, pks
