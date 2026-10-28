"""KP registry."""

import json
import logging
import re
from collections import defaultdict
from typing import List

import httpx

from test_harness.trapi import DEFAULT_TRAPI_VERSION

LOGGER = logging.getLogger(__name__)


SMARTAPI_URL = "https://smart-api.info/api/query?limit=1000&q=TRAPI"


def registered_trapi_versions(infores: str, maturity: str, hits=None) -> List[str]:
    """The TRAPI versions ``infores`` is registered with in SmartAPI for one
    maturity (eg "staging"), newest first. ``hits`` are SmartAPI's
    registrations, fetched when not given."""
    if hits is None:
        with httpx.Client(timeout=30) as client:
            response = client.get(SMARTAPI_URL)
            response.raise_for_status()
            hits = response.json().get("hits", [])
    versions = set()
    for hit in hits:
        info = hit.get("info") or {}
        if (info.get("x-translator") or {}).get("infores") != infores:
            continue
        version = (info.get("x-trapi") or {}).get("version")
        if not version:
            continue
        if any(
            server.get("x-maturity") == maturity for server in hit.get("servers") or []
        ):
            versions.add(version)

    def key(version):
        return tuple(int(part) for part in re.findall(r"\d+", version)[:3])

    return sorted(versions, key=key, reverse=True)


def retrieve_registry_from_smartapi(
    target_trapi_version=DEFAULT_TRAPI_VERSION,
):
    """Returns a dict of smart api service endpoints defined with a dict like
    {
            "_id": _id,
            "title": title,
            "url": url,
            "version": version,
    }
    """
    with httpx.Client(timeout=30) as client:
        try:
            response = client.get(SMARTAPI_URL)
            response.raise_for_status()
        except httpx.HTTPError as e:
            LOGGER.error("Failed to query smart api. Exiting...")
            raise e

    registrations = response.json()
    registry = defaultdict(lambda: defaultdict(list))
    for hit in registrations["hits"]:
        try:
            title = hit["info"]["title"]
        except KeyError:
            LOGGER.warning("No title for service. Cannot use.")
            continue
        # _id currently is missing on each "hit" (5/2/2022)
        # https://github.com/SmartAPI/smartapi_registry/issues/7#issuecomment-1115007211
        try:
            _id = hit["_id"]
        except KeyError:
            _id = title
        try:
            infores = hit["info"]["x-translator"]["infores"]
        except KeyError:
            LOGGER.warning(
                "No x-translator.infores for %s (https://smart-api.info/registry?q=%s)",
                title,
                _id,
            )
            infores = f"infores:{_id}"
        try:
            component = hit["info"]["x-translator"]["component"]
        except KeyError:
            LOGGER.warning(
                "No x-translator.component for %s (https://smart-api.info/registry?q=%s)",
                title,
                _id,
            )
            continue
        try:
            version = hit["info"]["x-trapi"]["version"]
        except KeyError:
            LOGGER.warning(
                "No x-trapi.version for %s (https://smart-api.info/registry?q=%s)",
                title,
                _id,
            )
            continue
        regex = re.compile("[0-9]\.[0-9]")
        trapi_version = regex.match(target_trapi_version)
        if trapi_version is None or not version.startswith(trapi_version.group() + "."):
            LOGGER.info(
                f"TRAPI version != {f'{trapi_version.group()}.x' if trapi_version is not None else target_trapi_version} for %s (https://smart-api.info/registry?q=%s)",
                title,
                _id,
            )
            continue
        try:
            for server in hit["servers"]:
                try:
                    maturity = server["x-maturity"]
                except KeyError:
                    LOGGER.warning(f"{infores} has no maturity")
                    continue
                try:
                    url = server["url"]
                    if url.endswith("/"):
                        url = url[:-1]
                except KeyError:
                    LOGGER.warning(
                        "No servers[0].url for %s (https://smart-api.info/registry?q=%s)",
                        title,
                        _id,
                    )
                    continue

                endpoint_title = title

                registry[maturity][component.lower()].append(
                    {
                        "_id": _id,
                        "title": endpoint_title,
                        "infores": infores,
                        "url": url,
                    }
                )
        except KeyError:
            LOGGER.warning(
                "No servers for %s (https://smart-api.info/registry?q=%s)",
                title,
                _id,
            )
            continue

    return registry


if __name__ == "__main__":
    registry = retrieve_registry_from_smartapi()
    print(json.dumps(registry))
