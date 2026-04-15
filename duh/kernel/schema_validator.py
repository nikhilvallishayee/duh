"""Validate tool input_schema at registration time.

Catches common issues:
- Missing "type": "object" at root
- Missing "properties" dict
- Invalid property types
- Missing "required" list referencing non-existent properties

ADR-068 P0: Early detection of malformed tool schemas prevents
cryptic API errors downstream.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# JSON Schema primitive types (the set the Anthropic API accepts).
_VALID_TYPES = frozenset({
    "string", "number", "integer", "boolean", "array", "object", "null",
})


class SchemaValidationError(ValueError):
    """Raised when a tool schema is invalid."""


def validate_tool_schema(name: str, schema: dict[str, Any]) -> list[str]:
    """Validate a tool's input_schema.  Returns list of warnings (empty = valid).

    Raises :class:`SchemaValidationError` for critical issues that would
    definitely cause an API error.

    Warnings are returned (but not raised) for issues that are suboptimal
    but won't necessarily break at runtime.

    Args:
        name: Tool name (for error messages).
        schema: The ``input_schema`` dict to validate.

    Returns:
        A list of human-readable warning strings.  Empty means no warnings.
    """
    warnings: list[str] = []

    # --- Empty schema is valid (tool takes no parameters) ---
    if not schema:
        return warnings

    # --- Root must be a dict ---
    if not isinstance(schema, dict):
        raise SchemaValidationError(
            f"Tool '{name}': input_schema must be a dict, got {type(schema).__name__}"
        )

    # --- Root "type" must be "object" ---
    root_type = schema.get("type")
    if root_type != "object":
        raise SchemaValidationError(
            f"Tool '{name}': input_schema root must have "
            f'"type": "object", got "type": {root_type!r}'
        )

    # --- "properties" must be a dict if present ---
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise SchemaValidationError(
            f"Tool '{name}': \"properties\" must be a dict, "
            f"got {type(properties).__name__}"
        )

    # --- Validate each property ---
    if isinstance(properties, dict):
        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                warnings.append(
                    f"Tool '{name}': property '{prop_name}' schema "
                    f"should be a dict, got {type(prop_schema).__name__}"
                )
                continue

            prop_type = prop_schema.get("type")
            if prop_type is None:
                warnings.append(
                    f"Tool '{name}': property '{prop_name}' is missing "
                    f"a \"type\" field"
                )
            elif isinstance(prop_type, str) and prop_type not in _VALID_TYPES:
                warnings.append(
                    f"Tool '{name}': property '{prop_name}' has invalid "
                    f"type \"{prop_type}\""
                )
            elif isinstance(prop_type, list):
                # Union types like ["string", "null"] -- check each element
                for t in prop_type:
                    if t not in _VALID_TYPES:
                        warnings.append(
                            f"Tool '{name}': property '{prop_name}' has "
                            f"invalid type \"{t}\" in union"
                        )

            # Warn on missing description
            if "description" not in prop_schema:
                warnings.append(
                    f"Tool '{name}': property '{prop_name}' is missing "
                    f"a \"description\" field"
                )

    # --- "required" entries must reference existing properties ---
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list):
            warnings.append(
                f"Tool '{name}': \"required\" should be a list, "
                f"got {type(required).__name__}"
            )
        else:
            prop_names = set(properties.keys()) if isinstance(properties, dict) else set()
            for req in required:
                if req not in prop_names:
                    warnings.append(
                        f"Tool '{name}': required field \"{req}\" "
                        f"does not exist in properties"
                    )

    return warnings
