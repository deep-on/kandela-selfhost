"""Tests for JSON string→dict parameter parsing resilience.

Ensures that tool parameters sent as JSON strings (instead of dicts)
are correctly handled at multiple defense layers.
"""

import json

import pytest

from memory_mcp.tools.models import (
    AutoRecallInput,
    ContextSearchInput,
    GuideInput,
    InboxInput,
    MemoryDeleteInput,
    MemorySearchInput,
    MemoryStoreInput,
    MemoryUpdateInput,
    ProjectDeleteInput,
    ProjectRenameInput,
    ProjectStatsInput,
    ReportFailureInput,
    SessionSummarizeInput,
)


class TestFlexibleInputJsonString:
    """_FlexibleInput base class should parse JSON strings to dicts."""

    def test_store_from_json_string(self) -> None:
        data = json.dumps({
            "project": "test",
            "content": "hello world",
            "memory_type": "fact",
        })
        result = MemoryStoreInput.model_validate(data)
        assert result.project == "test"
        assert result.content == "hello world"

    def test_store_from_dict(self) -> None:
        result = MemoryStoreInput.model_validate({
            "project": "test",
            "content": "hello world",
            "memory_type": "fact",
        })
        assert result.project == "test"

    def test_search_from_json_string(self) -> None:
        data = json.dumps({"query": "test query", "project": "proj"})
        result = MemorySearchInput.model_validate(data)
        assert result.query == "test query"

    def test_delete_from_json_string(self) -> None:
        data = json.dumps({"project": "p", "memory_id": "abc123"})
        result = MemoryDeleteInput.model_validate(data)
        assert result.memory_id == "abc123"

    def test_update_from_json_string(self) -> None:
        data = json.dumps({
            "project": "p",
            "memory_id": "abc",
            "content": "new content",
        })
        result = MemoryUpdateInput.model_validate(data)
        assert result.content == "new content"

    def test_summarize_from_json_string(self) -> None:
        data = json.dumps({
            "project": "p",
            "summary": "Did some work on X and Y today",
        })
        result = SessionSummarizeInput.model_validate(data)
        assert "work on X" in result.summary

    def test_auto_recall_from_json_string(self) -> None:
        data = json.dumps({"project": "p", "mode": "brief"})
        result = AutoRecallInput.model_validate(data)
        assert result.mode == "brief"

    def test_context_search_from_json_string(self) -> None:
        data = json.dumps({"query": "deployment gotcha", "project": "p"})
        result = ContextSearchInput.model_validate(data)
        assert result.query == "deployment gotcha"

    def test_stats_from_json_string(self) -> None:
        data = json.dumps({"project": "p"})
        result = ProjectStatsInput.model_validate(data)
        assert result.project == "p"

    def test_rename_from_json_string(self) -> None:
        data = json.dumps({"old_name": "a", "new_name": "b"})
        result = ProjectRenameInput.model_validate(data)
        assert result.new_name == "b"

    def test_project_delete_from_json_string(self) -> None:
        data = json.dumps({"project": "p", "confirm": True})
        result = ProjectDeleteInput.model_validate(data)
        assert result.confirm is True

    def test_inbox_from_json_string(self) -> None:
        data = json.dumps({"project": "p", "mark_reviewed": True})
        result = InboxInput.model_validate(data)
        assert result.mark_reviewed is True

    def test_report_failure_from_json_string(self) -> None:
        data = json.dumps({
            "project": "p",
            "tool_name": "docker exec",
            "error_summary": "permission denied",
            "attempt": 2,
        })
        result = ReportFailureInput.model_validate(data)
        assert result.attempt == 2

    def test_guide_from_json_string(self) -> None:
        data = json.dumps({"project_id": "my_proj"})
        result = GuideInput.model_validate(data)
        assert result.project_id == "my_proj"


class TestSafeCallToolPreProcessing:
    """Test the pre-processing logic used by _safe_call_tool."""

    @staticmethod
    def _preprocess(arguments: dict) -> dict:
        """Simulate _safe_call_tool's pre-processing."""
        for key, value in list(arguments.items()):
            if isinstance(value, str) and value.startswith("{"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        arguments[key] = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
        return arguments

    def test_json_string_to_dict(self) -> None:
        args = {"params": '{"project": "test", "content": "hello"}'}
        result = self._preprocess(args)
        assert isinstance(result["params"], dict)
        assert result["params"]["project"] == "test"

    def test_dict_unchanged(self) -> None:
        args = {"params": {"project": "test"}}
        result = self._preprocess(args)
        assert isinstance(result["params"], dict)

    def test_plain_string_unchanged(self) -> None:
        args = {"query": "search term"}
        result = self._preprocess(args)
        assert result["query"] == "search term"

    def test_non_dict_json_unchanged(self) -> None:
        """JSON arrays or primitives should not be converted."""
        args = {"params": '["a", "b", "c"]'}
        result = self._preprocess(args)
        # Starts with '[' not '{', so skipped
        assert isinstance(result["params"], str)

    def test_invalid_json_unchanged(self) -> None:
        args = {"params": "{invalid json"}
        result = self._preprocess(args)
        assert result["params"] == "{invalid json"

    def test_unicode_content_parsed(self) -> None:
        """Korean/unicode content should be parsed correctly."""
        content = {"project": "test", "content": "한국어 내용 테스트"}
        args = {"params": json.dumps(content, ensure_ascii=False)}
        result = self._preprocess(args)
        assert isinstance(result["params"], dict)
        assert result["params"]["content"] == "한국어 내용 테스트"

    def test_escaped_unicode_parsed(self) -> None:
        """Escaped unicode (\\uXXXX) should also work."""
        args = {"params": json.dumps({"project": "test", "content": "테스트"})}
        result = self._preprocess(args)
        assert isinstance(result["params"], dict)
        assert result["params"]["content"] == "테스트"

    def test_nested_json_parsed(self) -> None:
        """Nested structures (like tags array) should be preserved."""
        data = {
            "project": "test",
            "content": "hello",
            "memory_type": "fact",
            "tags": ["a", "b"],
            "importance": 7.0,
        }
        args = {"params": json.dumps(data)}
        result = self._preprocess(args)
        assert isinstance(result["params"], dict)
        assert result["params"]["tags"] == ["a", "b"]
        assert result["params"]["importance"] == 7.0

    def test_multiple_fields_processed(self) -> None:
        """Multiple string fields should all be processed."""
        args = {
            "params": '{"project": "a"}',
            "other": '{"key": "val"}',
            "plain": "not json",
        }
        result = self._preprocess(args)
        assert isinstance(result["params"], dict)
        assert isinstance(result["other"], dict)
        assert result["plain"] == "not json"
