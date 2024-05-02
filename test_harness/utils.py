"""General utilities for the Test Harness."""
import httpx
import logging
from typing import Dict, Union, List

from translator_testing_model.datamodel.pydanticmodel import TestCase

NODE_NORM_URL = {
    "dev": "https://nodenormalization-sri.renci.org/1.4",
    "ci": "https://nodenorm.ci.transltr.io",
    "test": "https://nodenorm.test.transltr.io/1.4",
    "prod": "https://nodenorm.transltr.io/1.4",
}

async def normalize_curies(
    test: TestCase,
    logger: logging.Logger = logging.getLogger(__name__),
) -> Dict[str, Dict[str, Union[Dict[str, str], List[str]]]]:
    """Normalize a list of curies."""
    node_norm = NODE_NORM_URL[test.test_env]
    # collect all curies from test
    curies = [asset.output_id for asset in test.test_assets]
    curies.append(test.test_case_input_id)

    async with httpx.AsyncClient() as client:
        normalized_curies = {}
        try:
            response = await client.post(
                node_norm + "/get_normalized_nodes",
                json={
                    "curies": curies,
                    "conflate": True,
                    "drug_chemical_conflate": True,
                },
            )
            response.raise_for_status()
            response = response.json()
            for curie, attrs in response.items():
                if attrs is None:
                    normalized_curies[curie] = {
                        "id": {
                            "identifier": "Unknown",
                        },
                        "type": [
                            "Unknown",
                        ],
                    }
                else:
                    normalized_curies[curie] = attrs
        except Exception as e:
            logger.error(f"Node norm failed with: {e}")
            logger.error("Using original curies.")
            for curie in curies:
                normalized_curies[curie] = {
                    "id": {
                        "identifier": curie,
                    },
                    # intentionally doesn't have a type so we can default to the original
                }
        return normalized_curies
