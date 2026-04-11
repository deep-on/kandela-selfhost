"""Tests for JSON Schema cross-client compatibility (Gemini, ChatGPT, etc.).

Ensures that all MCP tool schemas are free of $defs/$ref after
dereference_tool_schemas() is applied, making them compatible with
Gemini API and other clients that reject JSON Schema references.
"""

import json

import pytest

from memory_mcp.utils.schema import dereference_schema, dereference_tool_schemas


# ── dereference_schema unit tests ────────────────────────────────


class TestDereferenceSchema:
    """Unit tests for the dereference_schema function."""

    def test_no_defs_passthrough(self):
        """Schema without $defs passes through unchanged."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = dereference_schema(schema)
        assert result == schema

    def test_simple_ref_inlined(self):
        """A single $ref is resolved to the definition."""
        schema = {
            "$defs": {
                "MyEnum": {
                    "enum": ["a", "b", "c"],
                    "type": "string",
                }
            },
            "properties": {
                "field": {"$ref": "#/$defs/MyEnum"},
            },
        }
        result = dereference_schema(schema)
        assert "$defs" not in result
        assert "$ref" not in json.dumps(result)
        assert result["properties"]["field"]["enum"] == ["a", "b", "c"]
        assert result["properties"]["field"]["type"] == "string"

    def test_ref_with_sibling_fields_preserved(self):
        """Sibling fields (default, description) next to $ref are preserved."""
        schema = {
            "$defs": {
                "MyType": {"enum": ["x", "y"], "type": "string"}
            },
            "properties": {
                "field": {
                    "$ref": "#/$defs/MyType",
                    "default": "x",
                    "description": "Pick one",
                },
            },
        }
        result = dereference_schema(schema)
        field = result["properties"]["field"]
        assert field["enum"] == ["x", "y"]
        assert field["default"] == "x"
        assert field["description"] == "Pick one"

    def test_anyof_with_ref_resolved(self):
        """$ref inside anyOf is resolved."""
        schema = {
            "$defs": {
                "Priority": {"enum": ["high", "low"], "type": "string"}
            },
            "properties": {
                "priority": {
                    "anyOf": [
                        {"$ref": "#/$defs/Priority"},
                        {"type": "null"},
                    ],
                    "default": None,
                },
            },
        }
        result = dereference_schema(schema)
        assert "$ref" not in json.dumps(result)
        anyof = result["properties"]["priority"]["anyOf"]
        assert anyof[0]["enum"] == ["high", "low"]
        assert anyof[1]["type"] == "null"

    def test_nested_ref_resolved(self):
        """Nested objects with $ref are resolved recursively."""
        schema = {
            "$defs": {
                "Inner": {
                    "type": "object",
                    "properties": {"val": {"type": "integer"}},
                },
                "Outer": {
                    "type": "object",
                    "properties": {
                        "inner": {"$ref": "#/$defs/Inner"},
                    },
                },
            },
            "properties": {
                "data": {"$ref": "#/$defs/Outer"},
            },
        }
        result = dereference_schema(schema)
        assert "$ref" not in json.dumps(result)
        assert result["properties"]["data"]["properties"]["inner"]["properties"]["val"]["type"] == "integer"

    def test_multiple_refs_to_same_def(self):
        """Multiple fields referencing the same $def are all inlined."""
        schema = {
            "$defs": {
                "Status": {"enum": ["on", "off"], "type": "string"}
            },
            "properties": {
                "current": {"$ref": "#/$defs/Status"},
                "previous": {"$ref": "#/$defs/Status"},
            },
        }
        result = dereference_schema(schema)
        assert "$ref" not in json.dumps(result)
        assert result["properties"]["current"]["enum"] == ["on", "off"]
        assert result["properties"]["previous"]["enum"] == ["on", "off"]

    def test_original_not_mutated(self):
        """The original schema dict is not modified."""
        schema = {
            "$defs": {"X": {"type": "string"}},
            "properties": {"f": {"$ref": "#/$defs/X"}},
        }
        original_str = json.dumps(schema)
        dereference_schema(schema)
        assert json.dumps(schema) == original_str

    def test_empty_schema(self):
        """Empty schema returns empty dict."""
        assert dereference_schema({}) == {}

    def test_defs_without_refs(self):
        """$defs present but no $ref — $defs is still removed."""
        schema = {
            "$defs": {"Unused": {"type": "string"}},
            "properties": {"name": {"type": "string"}},
        }
        result = dereference_schema(schema)
        assert "$defs" not in result
        assert result["properties"]["name"]["type"] == "string"


# ── Pydantic model schema tests ──────────────────────────────────


class TestPydanticModelSchemas:
    """Test that actual Pydantic models produce Gemini-compatible schemas."""

    def test_memory_store_input_no_refs(self):
        from memory_mcp.tools.models import MemoryStoreInput

        schema = MemoryStoreInput.model_json_schema()
        result = dereference_schema(schema)
        result_str = json.dumps(result)
        assert "$defs" not in result_str
        assert "$ref" not in result_str
        # Enum values should be inlined
        mt = result["properties"]["memory_type"]
        assert "enum" in mt or ("anyOf" in mt and any("enum" in x for x in mt["anyOf"]))

    def test_memory_search_input_no_refs(self):
        from memory_mcp.tools.models import MemorySearchInput

        schema = MemorySearchInput.model_json_schema()
        result = dereference_schema(schema)
        assert "$ref" not in json.dumps(result)

    def test_memory_update_input_no_refs(self):
        from memory_mcp.tools.models import MemoryUpdateInput

        schema = MemoryUpdateInput.model_json_schema()
        result = dereference_schema(schema)
        assert "$ref" not in json.dumps(result)


# ── Full server integration test ─────────────────────────────────


class TestServerToolSchemas:
    """Integration test: all tools on the actual MCP server have clean schemas."""

    @pytest.fixture(scope="class")
    def mcp_server(self):
        from memory_mcp.server import create_server

        return create_server()

    def test_all_tools_no_defs_or_refs(self, mcp_server):
        """Every registered tool must have $defs/$ref-free schemas."""
        tools = mcp_server._tool_manager._tools
        assert len(tools) >= 10, f"Expected ≥10 tools, got {len(tools)}"

        for name, tool in tools.items():
            schema_str = json.dumps(tool.parameters)
            assert "$defs" not in schema_str, f"Tool '{name}' still has $defs"
            assert "$ref" not in schema_str, f"Tool '{name}' still has $ref"

    def test_enum_values_inlined(self, mcp_server):
        """Enum fields (MemoryType, MemoryPriority) should be inlined."""
        store_tool = mcp_server._tool_manager._tools["store"]
        schema_str = json.dumps(store_tool.parameters)

        # MemoryType enum values should appear directly
        assert '"fact"' in schema_str
        assert '"decision"' in schema_str
        assert '"summary"' in schema_str
        assert '"snippet"' in schema_str

    def test_tool_parameters_still_valid(self, mcp_server):
        """Inlined schemas should still be valid JSON Schema structure."""
        for name, tool in mcp_server._tool_manager._tools.items():
            schema = tool.parameters
            # Must be a valid JSON Schema object
            assert isinstance(schema, dict), f"Tool '{name}' schema is not a dict"
            assert "properties" in schema, f"Tool '{name}' schema has no properties"
            assert "type" in schema, f"Tool '{name}' schema has no type"
            assert schema["type"] == "object"
