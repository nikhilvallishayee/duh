"""Tests for duh.kernel.schema_validator (ADR-068 P0)."""

from __future__ import annotations

import logging

import pytest

from duh.kernel.schema_validator import SchemaValidationError, validate_tool_schema


# ---------------------------------------------------------------------------
# Valid schemas
# ---------------------------------------------------------------------------


class TestValidSchemas:
    """Schemas that should pass without errors or warnings."""

    def test_empty_schema_is_valid(self) -> None:
        """An empty dict means the tool takes no parameters."""
        warnings = validate_tool_schema("NoParams", {})
        assert warnings == []

    def test_minimal_valid_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path.",
                },
            },
            "required": ["path"],
        }
        warnings = validate_tool_schema("Read", schema)
        assert warnings == []

    def test_schema_with_no_required(self) -> None:
        """Properties but no required list is fine."""
        schema = {
            "type": "object",
            "properties": {
                "verbose": {
                    "type": "boolean",
                    "description": "Enable verbose output.",
                },
            },
        }
        warnings = validate_tool_schema("MyTool", schema)
        assert warnings == []

    def test_schema_with_no_properties(self) -> None:
        """type: object with no properties is valid."""
        schema = {"type": "object"}
        warnings = validate_tool_schema("Empty", schema)
        assert warnings == []

    def test_schema_with_array_property(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of items.",
                },
            },
        }
        warnings = validate_tool_schema("ListTool", schema)
        assert warnings == []

    def test_schema_with_union_type(self) -> None:
        """Union types like ["string", "null"] should be accepted."""
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "description": "Optional string.",
                },
            },
        }
        warnings = validate_tool_schema("Union", schema)
        assert warnings == []


# ---------------------------------------------------------------------------
# Critical errors (SchemaValidationError)
# ---------------------------------------------------------------------------


class TestCriticalErrors:
    """Schemas with critical issues that raise SchemaValidationError."""

    def test_missing_type_at_root(self) -> None:
        schema = {"properties": {"x": {"type": "string", "description": "x"}}}
        with pytest.raises(SchemaValidationError, match="root must have"):
            validate_tool_schema("Bad", schema)

    def test_wrong_type_at_root(self) -> None:
        schema = {"type": "array"}
        with pytest.raises(SchemaValidationError, match="root must have"):
            validate_tool_schema("Bad", schema)

    def test_non_dict_schema(self) -> None:
        with pytest.raises(SchemaValidationError, match="must be a dict"):
            validate_tool_schema("Bad", "not a dict")  # type: ignore[arg-type]

    def test_properties_not_a_dict(self) -> None:
        schema = {"type": "object", "properties": "wrong"}
        with pytest.raises(SchemaValidationError, match="\"properties\" must be a dict"):
            validate_tool_schema("Bad", schema)


# ---------------------------------------------------------------------------
# Warnings (returned, not raised)
# ---------------------------------------------------------------------------


class TestWarnings:
    """Schemas that produce warnings but don't raise errors."""

    def test_property_missing_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"description": "The name."},
            },
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any("missing" in w and '"type"' in w for w in warnings)

    def test_property_invalid_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "datetime", "description": "A datetime."},
            },
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any("invalid type" in w and "datetime" in w for w in warnings)

    def test_property_invalid_type_in_union(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": ["string", "datetime"], "description": "Bad union."},
            },
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any("invalid type" in w and "datetime" in w for w in warnings)

    def test_property_missing_description(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any('"description"' in w for w in warnings)

    def test_required_references_nonexistent_property(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "A."},
            },
            "required": ["a", "b"],
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any('"b"' in w and "does not exist" in w for w in warnings)

    def test_required_not_a_list(self) -> None:
        schema = {
            "type": "object",
            "properties": {},
            "required": "oops",
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any('"required" should be a list' in w for w in warnings)

    def test_property_schema_not_a_dict(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "bad": "not-a-dict",
            },
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any("should be a dict" in w for w in warnings)

    def test_required_with_no_properties(self) -> None:
        """Required references checked against empty property set."""
        schema = {
            "type": "object",
            "required": ["ghost"],
        }
        warnings = validate_tool_schema("Warn", schema)
        assert any('"ghost"' in w and "does not exist" in w for w in warnings)


# ---------------------------------------------------------------------------
# Built-in tools all pass validation
# ---------------------------------------------------------------------------


class TestBuiltinToolsPassValidation:
    """Every tool returned by get_all_tools() must have a valid schema."""

    def test_all_builtin_tools_pass(self) -> None:
        from duh.tools.registry import get_all_tools

        tools = get_all_tools()
        assert len(tools) > 0, "Expected at least one built-in tool"

        for tool in tools:
            name = getattr(tool, "name", "<unknown>")
            schema = getattr(tool, "input_schema", None)
            if schema is None:
                continue
            # Should NOT raise SchemaValidationError
            warnings = validate_tool_schema(name, schema)
            # Warnings are acceptable (e.g., missing descriptions),
            # but we log them for visibility
            for w in warnings:
                logging.getLogger(__name__).info("Built-in tool warning: %s", w)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """Verify that schema validation is wired into the registry."""

    def test_validate_registered_tool_logs_warnings(self, caplog: pytest.LogCaptureFixture) -> None:
        """_validate_registered_tool should log warnings."""
        from duh.tools.registry import _validate_registered_tool

        class FakeTool:
            name = "Fake"
            input_schema = {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},  # missing description
                },
            }

        with caplog.at_level(logging.WARNING):
            _validate_registered_tool(FakeTool())  # type: ignore[arg-type]

        assert any("description" in record.message for record in caplog.records)

    def test_validate_registered_tool_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """_validate_registered_tool should log (not raise) critical errors."""
        from duh.tools.registry import _validate_registered_tool

        class BadTool:
            name = "Bad"
            input_schema = {"type": "array"}  # wrong root type

        with caplog.at_level(logging.ERROR):
            # Should NOT raise -- errors are caught and logged
            _validate_registered_tool(BadTool())  # type: ignore[arg-type]

        assert any("Schema validation error" in record.message for record in caplog.records)

    def test_validate_registered_tool_no_schema(self) -> None:
        """Tools without input_schema should be silently skipped."""
        from duh.tools.registry import _validate_registered_tool

        class NoSchema:
            name = "NoSchema"

        # Should not raise
        _validate_registered_tool(NoSchema())  # type: ignore[arg-type]
