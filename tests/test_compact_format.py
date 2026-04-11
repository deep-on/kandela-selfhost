"""Tests for compact formatting functions — Lazy Retrieval output."""

import pytest

from memory_mcp.utils.formatting import (
    format_compact_result,
    format_compact_results,
    format_project_brief,
)


def _make_result(
    content: str = "Test memory content",
    mtype: str = "fact",
    importance: float = 5.0,
    created_at: str = "2026-02-28T12:00:00",
    result_id: str = "test-id-1",
    tags: str = "[]",
    distance: float = 0.1,
) -> dict:
    return {
        "id": result_id,
        "content": content,
        "distance": distance,
        "metadata": {
            "type": mtype,
            "importance": importance,
            "created_at": created_at,
            "tags": tags,
            "project": "test_project",
        },
    }


class TestFormatCompactResult:
    """Unit tests for format_compact_result()."""

    def test_basic_output_format(self) -> None:
        """기본 출력 포맷: [type] content... (imp, date)"""
        r = _make_result(content="Docker 배포 설정 완료", importance=9.0)
        output = format_compact_result(r)
        assert output.startswith("[fact]")
        assert "Docker 배포 설정 완료" in output
        assert "(9.0, 2026-02-28)" in output

    def test_content_truncation(self) -> None:
        """80자 초과 시 truncate + '...' 추가."""
        long_content = "A" * 100
        r = _make_result(content=long_content)
        output = format_compact_result(r, max_content_len=80)
        assert "..." in output
        # Content should be truncated
        assert "A" * 81 not in output

    def test_custom_max_content_len(self) -> None:
        """max_content_len 커스텀 지정."""
        r = _make_result(content="A" * 50)
        output = format_compact_result(r, max_content_len=20)
        assert "..." in output

    def test_no_truncation_for_short_content(self) -> None:
        """짧은 내용은 truncate하지 않음."""
        r = _make_result(content="Short")
        output = format_compact_result(r, max_content_len=80)
        assert "..." not in output
        assert "Short" in output

    def test_multiline_content_flattened(self) -> None:
        """여러 줄 내용이 한 줄로 합쳐짐."""
        r = _make_result(content="Line1\nLine2\nLine3")
        output = format_compact_result(r)
        assert "\n" not in output
        assert "Line1 Line2 Line3" in output

    def test_include_content_false(self) -> None:
        """include_content=False면 내용 없이 메타만 출력."""
        r = _make_result(content="Should not appear")
        output = format_compact_result(r, include_content=False)
        assert "Should not appear" not in output
        assert "[fact]" in output
        assert "5.0" in output

    def test_different_types(self) -> None:
        """다양한 타입 표시."""
        for mtype in ("fact", "decision", "summary", "snippet"):
            r = _make_result(mtype=mtype)
            output = format_compact_result(r)
            assert f"[{mtype}]" in output

    def test_priority_fallback(self) -> None:
        """importance 없이 priority만 있는 레거시 데이터."""
        r = _make_result()
        del r["metadata"]["importance"]
        r["metadata"]["priority"] = "critical"
        output = format_compact_result(r)
        assert "9.0" in output

    def test_date_only_10_chars(self) -> None:
        """날짜는 YYYY-MM-DD (10자)만 표시."""
        r = _make_result(created_at="2026-02-28T14:30:00.123456")
        output = format_compact_result(r)
        assert "2026-02-28" in output
        assert "14:30" not in output


class TestFormatCompactResults:
    """Unit tests for format_compact_results()."""

    def test_empty_results(self) -> None:
        assert format_compact_results([]) == "No memories found."

    def test_header_and_count(self) -> None:
        results = [_make_result(result_id=f"id-{i}") for i in range(3)]
        output = format_compact_results(results)
        assert "Found 3 memory(ies):" in output

    def test_numbered_list(self) -> None:
        results = [_make_result(result_id=f"id-{i}") for i in range(3)]
        output = format_compact_results(results)
        assert "1. [fact]" in output
        assert "2. [fact]" in output
        assert "3. [fact]" in output

    def test_include_content_passthrough(self) -> None:
        results = [_make_result(content="visible content")]
        output = format_compact_results(results, include_content=False)
        assert "visible content" not in output


class TestFormatProjectBrief:
    """Unit tests for format_project_brief()."""

    def test_basic_brief(self) -> None:
        brief = {
            "memory_count": 42,
            "critical_count": 7,
            "type_counts": {"fact": 20, "decision": 10, "summary": 7, "snippet": 5},
            "topic_keywords": ["docker", "deployment", "auth"],
            "last_session_date": "2026-02-27",
            "last_summary_snippet": "API key auth 구현 완료",
        }
        output = format_project_brief(brief, "my_project")
        assert "my_project" in output
        assert "42 memories" in output
        assert "7 critical" in output
        assert "docker" in output
        assert "2026-02-27" in output
        assert "API key auth" in output

    def test_with_critical_memories(self) -> None:
        brief = {
            "memory_count": 10,
            "critical_count": 2,
            "type_counts": {"fact": 5, "decision": 5},
            "topic_keywords": [],
            "last_session_date": None,
            "last_summary_snippet": None,
        }
        crits = [
            _make_result(content="Critical memory 1", importance=9.5, result_id="c1"),
            _make_result(content="Critical memory 2", importance=9.0, result_id="c2"),
        ]
        output = format_project_brief(brief, "test_proj", critical_memories=crits)
        assert "Critical (2)" in output
        assert "Critical memory 1" in output
        assert "Critical memory 2" in output

    def test_no_last_session(self) -> None:
        brief = {
            "memory_count": 5,
            "critical_count": 0,
            "type_counts": {"fact": 5},
            "topic_keywords": [],
            "last_session_date": None,
            "last_summary_snippet": None,
        }
        output = format_project_brief(brief, "new_project")
        assert "new_project" in output
        assert "5 memories" in output
        # Should not crash with None date/snippet

    def test_type_counts_display(self) -> None:
        brief = {
            "memory_count": 15,
            "critical_count": 0,
            "type_counts": {"fact": 10, "snippet": 5},
            "topic_keywords": [],
            "last_session_date": None,
            "last_summary_snippet": None,
        }
        output = format_project_brief(brief, "proj")
        assert "10 fact" in output
        assert "5 snippet" in output
