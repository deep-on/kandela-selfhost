"""Tests for inbox (unreviewed memo) functionality."""

import json
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestGetByTag:
    """Test store.get_by_tag() method."""

    def test_empty_project(self, store: MemoryStore) -> None:
        """빈 프로젝트에서는 빈 리스트 반환."""
        results = store.get_by_tag("nonexistent", "unreviewed")
        assert results == []

    def test_finds_tagged_memories(self, store: MemoryStore) -> None:
        """태그가 있는 메모리만 반환."""
        project = "test_inbox"
        store.store(project, "메모 1", MemoryType.FACT, tags=["telegram", "unreviewed"])
        store.store(project, "메모 2", MemoryType.FACT, tags=["telegram", "unreviewed"])
        store.store(project, "메모 3", MemoryType.FACT, tags=["manual"])

        results = store.get_by_tag(project, "unreviewed")
        assert len(results) == 2
        contents = {r["content"] for r in results}
        assert "메모 1" in contents
        assert "메모 2" in contents

    def test_respects_n_results(self, store: MemoryStore) -> None:
        """n_results로 결과 수 제한."""
        project = "test_limit"
        for i in range(5):
            store.store(project, f"메모 {i}", MemoryType.FACT, tags=["unreviewed"])

        results = store.get_by_tag(project, "unreviewed", n_results=3)
        assert len(results) == 3

    def test_sorted_by_created_ts_desc(self, store: MemoryStore) -> None:
        """created_ts 내림차순 정렬 (최근 것이 먼저)."""
        project = "test_sort"
        store.store(project, "먼저 저장", MemoryType.FACT, tags=["unreviewed"])
        store.store(project, "나중 저장", MemoryType.FACT, tags=["unreviewed"])

        results = store.get_by_tag(project, "unreviewed")
        assert len(results) == 2
        # Verify descending order by created_ts
        ts0 = results[0]["metadata"].get("created_ts", 0)
        ts1 = results[1]["metadata"].get("created_ts", 0)
        assert ts0 >= ts1, f"Expected descending order: {ts0} >= {ts1}"

    def test_no_match_returns_empty(self, store: MemoryStore) -> None:
        """매칭되는 태그가 없으면 빈 리스트."""
        project = "test_nomatch"
        store.store(project, "일반 메모", MemoryType.FACT, tags=["docker"])

        results = store.get_by_tag(project, "unreviewed")
        assert results == []


class TestBriefUnreviewedCount:
    """Test unreviewed_count in get_project_brief()."""

    def test_zero_when_no_unreviewed(self, store: MemoryStore) -> None:
        """unreviewed 태그 없으면 0."""
        project = "test_no_unreviewed"
        store.store(project, "일반 메모", MemoryType.FACT, tags=["docker"])

        brief = store.get_project_brief(project)
        assert brief["unreviewed_count"] == 0

    def test_counts_unreviewed(self, store: MemoryStore) -> None:
        """unreviewed 태그가 있는 메모리 수 정확히 카운트."""
        project = "test_count"
        store.store(project, "텔레그램 메모 1", MemoryType.FACT, tags=["telegram", "unreviewed"])
        store.store(project, "텔레그램 메모 2", MemoryType.FACT, tags=["telegram", "unreviewed"])
        store.store(project, "Claude Code 메모", MemoryType.FACT, tags=["manual"])

        brief = store.get_project_brief(project)
        assert brief["unreviewed_count"] == 2

    def test_empty_project_has_zero(self, store: MemoryStore) -> None:
        """빈 프로젝트 brief에도 unreviewed_count 포함."""
        brief = store.get_project_brief("empty_proj")
        assert brief["unreviewed_count"] == 0


class TestUnreviewedTagRemoval:
    """Test removing unreviewed tag via store.update()."""

    def test_remove_unreviewed_tag(self, store: MemoryStore) -> None:
        """unreviewed 태그 제거 후 get_by_tag에서 안 나와야 한다."""
        project = "test_review"
        mem_id = store.store(
            project, "리뷰할 메모", MemoryType.FACT,
            tags=["telegram", "unreviewed"],
        )

        # Before review
        results = store.get_by_tag(project, "unreviewed")
        assert len(results) == 1

        # Remove unreviewed tag
        store.update(project, mem_id, tags=["telegram"])

        # After review
        results = store.get_by_tag(project, "unreviewed")
        assert len(results) == 0

        # Memory still exists with telegram tag
        results = store.get_by_tag(project, "telegram")
        assert len(results) == 1


class TestInboxFormatting:
    """Test that inbox shows full content without truncation."""

    def test_long_memo_not_truncated(self, store: MemoryStore) -> None:
        """80자 이상 긴 메모가 짤리지 않아야 한다."""
        long_content = (
            "벤치마킹에서 context길이를 최대로 했는데 이로 인해 "
            "hallucination이 줄어든 측면이 있는지 검토하고 "
            "context길이를 적정사이즈로 해서 벤치마킹을 추가로 진행할 필요성을 검토하자."
        )
        assert len(long_content) > 80  # Exceeds compact limit
        project = "test_long"
        store.store(project, long_content, MemoryType.DECISION, tags=["unreviewed"])

        results = store.get_by_tag(project, "unreviewed")
        assert len(results) == 1
        # The full content should be retrievable
        assert results[0]["content"] == long_content

    def test_multiline_memo_preserved(self, store: MemoryStore) -> None:
        """여러 줄 메모 내용이 저장 후 그대로 반환되어야 한다."""
        content = "첫째 줄\n둘째 줄\n셋째 줄"
        project = "test_multiline"
        store.store(project, content, MemoryType.FACT, tags=["unreviewed"])

        results = store.get_by_tag(project, "unreviewed")
        assert results[0]["content"] == content


class TestInboxInputModel:
    """Test InboxInput pydantic model."""

    def test_defaults(self) -> None:
        """기본값 검증."""
        from memory_mcp.tools.models import InboxInput

        inp = InboxInput(project="test")
        assert inp.project == "test"
        assert inp.n_results == 20
        assert inp.mark_reviewed is False

    def test_mark_reviewed(self) -> None:
        """mark_reviewed=True 설정."""
        from memory_mcp.tools.models import InboxInput

        inp = InboxInput(project="test", mark_reviewed=True)
        assert inp.mark_reviewed is True

    def test_n_results_range(self) -> None:
        """n_results 범위 검증."""
        from memory_mcp.tools.models import InboxInput

        inp = InboxInput(project="test", n_results=50)
        assert inp.n_results == 50

        inp2 = InboxInput(project="test", n_results=2500)
        assert inp2.n_results == 2500

        with pytest.raises(Exception):
            InboxInput(project="test", n_results=2501)

        with pytest.raises(Exception):
            InboxInput(project="test", n_results=0)
