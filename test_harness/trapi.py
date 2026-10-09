"""TRAPI version handling.

The harness speaks TRAPI 2.0 by default and can still speak 1.6 for services
that have not moved yet. Queries are built with ``translator_tom`` (TOM), the
Translator-wide TRAPI object model, using the model package for the version
being tested, so a query is validated against that version's schema before it
is ever sent.

Responses are read as plain dicts rather than parsed into TOM models: an ARS
merged message can be very large, and the harness only reads the bindings out
of it. What does differ between versions on that path is the shape of a
binding, which ``binding_ids`` reads either way.
"""

import re
from typing import Any, List

DEFAULT_TRAPI_VERSION = "2.0.0"

# The TRAPI minor versions there is a TOM model package for, keyed by the
# "major.minor" prefix of a SemVer version.
SUPPORTED_TRAPI_VERSIONS = ("2.0", "1.6")


def trapi_minor_version(trapi_version: str) -> str:
    """Return the ``major.minor`` of a TRAPI SemVer version, if it is supported.

    Examples:
        >>> trapi_minor_version("2.0.0")
        '2.0'
        >>> trapi_minor_version("1.6.0-beta")
        '1.6'
    """
    match = re.match(r"(\d+)\.(\d+)", trapi_version or "")
    minor = f"{match.group(1)}.{match.group(2)}" if match else None
    if minor not in SUPPORTED_TRAPI_VERSIONS:
        raise ValueError(
            f"Unsupported TRAPI version '{trapi_version}'. "
            f"Expected one of: {', '.join(v + '.x' for v in SUPPORTED_TRAPI_VERSIONS)}."
        )
    return minor


def is_trapi_2(trapi_version: str) -> bool:
    """Whether ``trapi_version`` is a TRAPI 2.x version."""
    return trapi_minor_version(trapi_version).startswith("2.")


def binding_ids(binding: Any) -> List[str]:
    """Return the ids bound by one node, edge or path binding.

    TRAPI 2.0 binds each QNode / QEdge / QPath to a single object carrying an
    ``ids`` list; TRAPI 1.x binds it to a list of objects each carrying an
    ``id``. Both are read here, since the ARS can hand back either.

    Examples:
        >>> binding_ids({"ids": ["MONDO:1", "MONDO:2"]})
        ['MONDO:1', 'MONDO:2']
        >>> binding_ids([{"id": "MONDO:1"}, {"id": "MONDO:2", "attributes": []}])
        ['MONDO:1', 'MONDO:2']
    """
    if isinstance(binding, dict):
        return [str(binding_id) for binding_id in binding.get("ids") or []]
    return [str(entry["id"]) for entry in binding or [] if "id" in entry]
