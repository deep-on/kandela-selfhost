"""JSON Schema utilities for cross-client MCP compatibility.

Gemini API rejects JSON Schema with $defs/$ref references.
FastMCP generates $defs/$ref when Pydantic models are used as tool parameters.
This module provides a function to inline those references so the schema
works with all MCP clients (Claude, ChatGPT, Gemini, Cursor, etc.).
"""

from __future__ import annotations

import json
from typing import Any


def dereference_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline all $defs/$ref in a JSON Schema for Gemini API compatibility.

    Recursively resolves ``$ref`` pointers to their definitions in ``$defs``,
    producing a self-contained schema without any ``$ref`` or ``$defs``.

    Args:
        schema: A JSON Schema dict (potentially containing $defs/$ref).

    Returns:
        A new schema dict with all references inlined.
    """
    schema = json.loads(json.dumps(schema))  # deep copy
    defs = schema.pop("$defs", {})

    if not defs:
        return schema

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_path = obj["$ref"]  # e.g. "#/$defs/MemoryType"
                ref_name = ref_path.rsplit("/", 1)[-1]
                if ref_name in defs:
                    # Replace $ref with the resolved definition
                    resolved = dict(_resolve(defs[ref_name]))
                    # Preserve sibling fields (default, description, etc.)
                    for k, v in obj.items():
                        if k != "$ref":
                            resolved[k] = v
                    return resolved
                return obj
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(item) for item in obj]
        return obj

    return _resolve(schema)


def dereference_tool_schemas(tool_manager: Any) -> None:
    """Inline $defs/$ref in all registered MCP tool schemas.

    Call this after all tools are registered on the FastMCP server
    to ensure compatibility with Gemini and other MCP clients that
    reject JSON Schema references.

    Args:
        tool_manager: FastMCP's ToolManager (``mcp._tool_manager``).
    """
    for tool in tool_manager._tools.values():
        schema_str = json.dumps(tool.parameters)
        if "$defs" in schema_str or "$ref" in schema_str:
            tool.parameters = dereference_schema(tool.parameters)
