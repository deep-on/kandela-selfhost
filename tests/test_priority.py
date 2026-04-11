"""Tests for priority-based memory system (Phase 5)."""

import time
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryPriority, MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestPriorityStore:
    """Test that priority is correctly stored and retrieved."""

    def test_store_with_default_priority(self, store: MemoryStore) -> None:
        """기본 priority는 NORMAL이어야 한다."""
        doc_id = store.store("proj", "some fact", MemoryType.FACT)
        # Retrieve and check metadata
        col = store._get_collection("proj")
        result = col.get(ids=[doc_id], include=["metadatas"])
        assert result["metadatas"][0]["priority"] == "normal"

    def test_store_with_critical_priority(self, store: MemoryStore) -> None:
        """CRITICAL priority가 올바르게 저장되어야 한다."""
        doc_id = store.store(
            "proj", "배포 대상: MacBook, testbox WSL, test-server container",
            MemoryType.FACT, priority=MemoryPriority.CRITICAL,
        )
        col = store._get_collection("proj")
        result = col.get(ids=[doc_id], include=["metadatas"])
        assert result["metadatas"][0]["priority"] == "critical"

    def test_store_with_low_priority(self, store: MemoryStore) -> None:
        """LOW priority가 올바르게 저장되어야 한다."""
        doc_id = store.store(
            "proj", "auto-saved raw response content",
            MemoryType.FACT, tags=["auto-saved"], priority=MemoryPriority.LOW,
        )
        col = store._get_collection("proj")
        result = col.get(ids=[doc_id], include=["metadatas"])
        assert result["metadatas"][0]["priority"] == "low"

    def test_all_three_priorities_coexist(self, store: MemoryStore) -> None:
        """3가지 priority가 하나의 프로젝트에 공존할 수 있어야 한다."""
        store.store("proj", "critical info", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        store.store("proj", "normal info", MemoryType.FACT, priority=MemoryPriority.NORMAL)
        store.store("proj", "low info", MemoryType.FACT, priority=MemoryPriority.LOW)

        col = store._get_collection("proj")
        all_data = col.get(include=["metadatas"])
        priorities = {m["priority"] for m in all_data["metadatas"]}
        assert priorities == {"critical", "normal", "low"}


class TestGetByPriority:
    """Test get_by_priority() method."""

    def test_get_critical_only(self, store: MemoryStore) -> None:
        """CRITICAL만 필터링하여 반환해야 한다."""
        store.store("proj", "critical: 배포 경로", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        store.store("proj", "normal: 작업 완료", MemoryType.FACT, priority=MemoryPriority.NORMAL)
        store.store("proj", "low: auto-saved", MemoryType.FACT, priority=MemoryPriority.LOW)

        critical = store.get_by_priority("proj", MemoryPriority.CRITICAL)
        assert len(critical) == 1
        assert "배포 경로" in critical[0]["content"]
        assert critical[0]["metadata"]["priority"] == "critical"

    def test_get_low_only(self, store: MemoryStore) -> None:
        """LOW만 필터링하여 반환해야 한다."""
        store.store("proj", "critical: 배포", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        store.store("proj", "low: auto-saved 1", MemoryType.FACT, priority=MemoryPriority.LOW)
        time.sleep(0.02)
        store.store("proj", "low: auto-saved 2", MemoryType.FACT, priority=MemoryPriority.LOW)

        low = store.get_by_priority("proj", MemoryPriority.LOW)
        assert len(low) == 2
        # 최신이 먼저
        assert "auto-saved 2" in low[0]["content"]
        assert "auto-saved 1" in low[1]["content"]

    def test_get_by_priority_empty_project(self, store: MemoryStore) -> None:
        """빈 프로젝트에서는 빈 리스트를 반환해야 한다."""
        result = store.get_by_priority("empty", MemoryPriority.CRITICAL)
        assert result == []

    def test_get_by_priority_no_matching(self, store: MemoryStore) -> None:
        """해당 priority가 없으면 빈 리스트를 반환해야 한다."""
        store.store("proj", "normal info", MemoryType.FACT, priority=MemoryPriority.NORMAL)
        result = store.get_by_priority("proj", MemoryPriority.CRITICAL)
        assert result == []

    def test_get_multiple_critical_sorted_by_time(self, store: MemoryStore) -> None:
        """여러 CRITICAL 기억이 시간순(최신 먼저)으로 정렬되어야 한다."""
        store.store("proj", "critical 1: SSH 접속 방법", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        time.sleep(0.02)
        store.store("proj", "critical 2: WSL 주의사항", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        time.sleep(0.02)
        store.store("proj", "critical 3: hook stdout 미처리", MemoryType.FACT, priority=MemoryPriority.CRITICAL)

        critical = store.get_by_priority("proj", MemoryPriority.CRITICAL)
        assert len(critical) == 3
        # 최신이 첫 번째
        assert "hook stdout" in critical[0]["content"]
        assert "WSL" in critical[1]["content"]
        assert "SSH" in critical[2]["content"]


class TestPriorityInSearch:
    """Test that priority metadata is preserved in search results."""

    def test_search_returns_priority_metadata(self, store: MemoryStore) -> None:
        """검색 결과에 priority 메타데이터가 포함되어야 한다."""
        store.store(
            "proj", "testbox은 SSH 후 wsl -d Ubuntu-22.04 필요",
            MemoryType.FACT, priority=MemoryPriority.CRITICAL,
        )

        results = store.search("testbox SSH 접속", project="proj")
        assert len(results) > 0
        assert results[0]["metadata"]["priority"] == "critical"

    def test_get_recent_returns_priority_metadata(self, store: MemoryStore) -> None:
        """get_recent 결과에도 priority 메타데이터가 포함되어야 한다."""
        store.store("proj", "최근 작업", MemoryType.FACT, priority=MemoryPriority.NORMAL)
        recent = store.get_recent("proj", n_results=5)
        assert len(recent) > 0
        assert recent[0]["metadata"]["priority"] == "normal"


class TestAutoRecallPriority:
    """Test the priority-based auto_recall logic at the store level.

    The full auto_recall is in server.py, but we test the building blocks here.
    """

    def test_critical_always_available(self, store: MemoryStore) -> None:
        """CRITICAL 기억은 항상 전부 조회 가능해야 한다."""
        project = "recall_test"
        # 다양한 priority의 기억 저장
        for i in range(5):
            store.store(project, f"critical fact {i}", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        for i in range(10):
            store.store(project, f"normal fact {i}", MemoryType.FACT, priority=MemoryPriority.NORMAL)
        for i in range(20):
            store.store(project, f"low auto-saved {i}", MemoryType.FACT, priority=MemoryPriority.LOW)

        # CRITICAL 전부 로드
        critical = store.get_by_priority(project, MemoryPriority.CRITICAL)
        assert len(critical) == 5

        # NORMAL 조회
        normal = store.get_by_priority(project, MemoryPriority.NORMAL)
        assert len(normal) == 10

        # LOW 조회
        low = store.get_by_priority(project, MemoryPriority.LOW)
        assert len(low) == 20

    def test_low_priority_excluded_from_general_recall(self, store: MemoryStore) -> None:
        """LOW priority는 일반 get_recent에서 포함되지만 필터링 가능해야 한다."""
        project = "filter_test"
        store.store(project, "critical: 절대 잊으면 안됨", MemoryType.FACT, priority=MemoryPriority.CRITICAL)
        time.sleep(0.02)
        store.store(project, "normal: 있으면 좋음", MemoryType.FACT, priority=MemoryPriority.NORMAL)
        time.sleep(0.02)
        store.store(project, "low: 자동 저장 원문", MemoryType.FACT, priority=MemoryPriority.LOW)

        # get_recent는 모든 priority 반환 (필터는 서버 레이어에서)
        all_recent = store.get_recent(project, n_results=10)
        assert len(all_recent) == 3

        # get_by_priority로 LOW 제외 가능
        non_low = store.get_by_priority(project, MemoryPriority.CRITICAL) + \
                  store.get_by_priority(project, MemoryPriority.NORMAL)
        assert len(non_low) == 2
        assert all(r["metadata"]["priority"] != "low" for r in non_low)


class TestGetByPriorityStillWorks:
    """Verify get_by_priority still functions after Phase 9 changes."""

    def test_get_by_priority_backward_compat(self, store: MemoryStore) -> None:
        """get_by_priority should still return results based on priority string."""
        store.store("proj", "critical SSH info", MemoryType.FACT,
                     priority=MemoryPriority.CRITICAL)
        store.store("proj", "normal work note", MemoryType.FACT,
                     priority=MemoryPriority.NORMAL)
        store.store("proj", "low auto-save", MemoryType.FACT,
                     priority=MemoryPriority.LOW)

        critical = store.get_by_priority("proj", MemoryPriority.CRITICAL)
        assert len(critical) == 1
        assert critical[0]["metadata"]["priority"] == "critical"

    def test_importance_based_retrieval(self, store: MemoryStore) -> None:
        """get_by_importance should work with importance float."""
        store.store("proj", "high importance via float", MemoryType.FACT,
                     importance=9.5)
        store.store("proj", "normal importance", MemoryType.FACT,
                     importance=5.0)

        high = store.get_by_importance("proj", min_importance=9.0)
        assert len(high) == 1
        assert "high importance" in high[0]["content"]


class TestPriorityBackwardCompatibility:
    """Test backward compatibility — memories without priority field."""

    def test_old_memories_treated_as_normal(self, store: MemoryStore) -> None:
        """priority 필드가 없는 기존 메모리는 NORMAL로 취급되어야 한다.

        기존 데이터에는 priority 메타데이터가 없을 수 있다.
        get_by_priority에서 where 필터가 이를 자연스럽게 제외하므로,
        기존 데이터는 CRITICAL 로드에 포함되지 않는다 (안전한 동작).
        """
        # 직접 ChromaDB에 priority 없는 데이터 삽입 (기존 형식 시뮬레이션)
        col = store._get_collection("legacy_proj")
        embedding = store._embed("기존 형식의 기억")
        col.add(
            ids=["legacy_1"],
            documents=["기존 형식의 기억"],
            embeddings=[embedding],
            metadatas=[{
                "project": "legacy_proj",
                "type": "fact",
                "tags": "[]",
                "created_at": "2026-01-01T00:00:00+00:00",
                "deleted_ts": 0,
                # priority 필드 없음!
            }],
        )

        # CRITICAL 조회 시 포함되지 않아야 함 (안전)
        critical = store.get_by_priority("legacy_proj", MemoryPriority.CRITICAL)
        assert len(critical) == 0

        # 하지만 get_recent에서는 반환되어야 함
        recent = store.get_recent("legacy_proj", n_results=5)
        assert len(recent) == 1
        assert "기존 형식" in recent[0]["content"]

        # search에서도 반환되어야 함
        results = store.search("기존 형식", project="legacy_proj")
        assert len(results) > 0
