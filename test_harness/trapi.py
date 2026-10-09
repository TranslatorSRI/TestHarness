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

import json
import re
from typing import Any, Dict, List, Optional

import translator_tom.v2_0 as trapi_2_0
from pydantic import ValidationError

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


# --- query parameters ----------------------------------------------------------

# The query parameters TRAPI 1.6 has, as top-level fields of the Query. It has
# no "parameters" object, and no timeout.
TRAPI_1_6_PARAMETERS = ("log_level", "bypass_cache")


def parse_query_parameters(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read query parameters given on the command line or in the environment.

    ``text`` is a JSON object, eg ``{"timeout": 300, "bypass_cache": true}``,
    or ``@path`` to a file holding one. Empty means none.
    """
    if text is None or not text.strip():
        return None
    text = text.strip()
    if text.startswith("@"):
        with open(text[1:]) as f:
            text = f.read()
    try:
        params = json.loads(text)
    except ValueError as e:
        raise ValueError(f"Query parameters aren't valid JSON: {e}") from None
    if not isinstance(params, dict):
        raise ValueError('Query parameters must be a JSON object, eg {"timeout": 300}')
    return params


def validate_query_parameters(
    params: Optional[Dict[str, Any]], trapi_version: str
) -> Optional[Dict[str, Any]]:
    """Check query parameters against the TRAPI version's schema.

    Returns them as they'll be sent, or raises ValueError saying what's wrong,
    so a typo stops the run before any query goes out rather than every query
    failing. TRAPI 2.0's ``parameters`` takes service-specific keys besides
    the standard ones; TRAPI 1.6 only has ``log_level`` and ``bypass_cache``.
    """
    if not params:
        return None
    # the standard parameters' types are the same in both versions
    try:
        checked = trapi_2_0.QueryParameters.model_validate(params)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise ValueError(f"Invalid query parameters: {problems}") from None
    normalized = checked.model_dump(mode="json", exclude_unset=True)
    if not is_trapi_2(trapi_version):
        unsupported = sorted(set(normalized) - set(TRAPI_1_6_PARAMETERS))
        if unsupported:
            raise ValueError(
                f"TRAPI {trapi_minor_version(trapi_version)} queries can't carry "
                f"{', '.join(unsupported)}: only "
                f"{' and '.join(TRAPI_1_6_PARAMETERS)} exist before TRAPI 2.0"
            )
    return normalized


def apply_query_parameters(
    query: Dict[str, Any], params: Optional[Dict[str, Any]], trapi_version: str
) -> Dict[str, Any]:
    """Add already-validated query parameters to a query dict.

    TRAPI 2.0 carries them in the query's ``parameters`` object; TRAPI 1.6 as
    top-level fields of the query.
    """
    if not params:
        return query
    if is_trapi_2(trapi_version):
        query["parameters"] = {**(query.get("parameters") or {}), **params}
    else:
        query.update(params)
    return query
