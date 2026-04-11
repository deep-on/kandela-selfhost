"""Tests for project management: rename, delete, list with stats, exists."""

import time
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


def _store_samples(store: MemoryStore, project: str, count: int = 3) -> list[str]:
    """Store sample memories and return their IDs."""
    ids = []
    contents = [
        ("Python 3.11 환경 확인", MemoryType.FACT),
        ("FastAPI 대신 FastMCP 사용 결정", MemoryType.DECISION),
        ("docker compose up -d", MemoryType.SNIPPET),
        ("세션 요약: 서버 배포 완료", MemoryType.SUMMARY),
        ("ChromaDB 벡터DB 사용", MemoryType.FACT),
    ]
    for content, mtype in contents[:count]:
        doc_id = store.store(project, content, mtype)
        ids.append(doc_id)
        time.sleep(0.01)
    return ids


class TestProjectExists:
    def test_exists_true(self, store: MemoryStore) -> None:
        store.store("alpha", "test content", MemoryType.FACT)
        assert store.project_exists("alpha") is True

    def test_exists_false(self, store: MemoryStore) -> None:
        assert store.project_exists("nonexistent") is False

    def test_exists_after_delete(self, store: MemoryStore) -> None:
        store.store("temp", "test content", MemoryType.FACT)
        assert store.project_exists("temp") is True
        store.delete_project("temp")
        assert store.project_exists("temp") is False


class TestListProjectsWithStats:
    def test_empty(self, store: MemoryStore) -> None:
        assert store.list_projects_with_stats() == []

    def test_basic(self, store: MemoryStore) -> None:
        _store_samples(store, "alpha", 3)
        _store_samples(store, "beta", 2)

        stats = store.list_projects_with_stats()
        assert len(stats) == 2

        # Sorted by name
        assert stats[0]["name"] == "alpha"
        assert stats[0]["memory_count"] == 3
        assert stats[1]["name"] == "beta"
        assert stats[1]["memory_count"] == 2

    def test_single_project(self, store: MemoryStore) -> None:
        _store_samples(store, "solo", 5)
        stats = store.list_projects_with_stats()
        assert len(stats) == 1
        assert stats[0]["name"] == "solo"
        assert stats[0]["memory_count"] == 5


class TestRenameProject:
    def test_rename_basic(self, store: MemoryStore) -> None:
        """3개 메모리 저장 → 이름 변경 → 새 이름에서 3개 확인."""
        _store_samples(store, "old_proj", 3)

        result = store.rename_project("old_proj", "new_proj")
        assert result["old_name"] == "old_proj"
        assert result["new_name"] == "new_proj"
        assert result["memories_moved"] == 3

        # Old project should not exist
        assert store.project_exists("old_proj") is False
        # New project should exist with 3 memories
        assert store.project_exists("new_proj") is True
        assert "new_proj" in store.list_projects()
        assert "old_proj" not in store.list_projects()

    def test_rename_preserves_search(self, store: MemoryStore) -> None:
        """Rename 후에도 시맨틱 검색이 동작해야 한다."""
        store.store("proj_a", "AXI4-Stream FPGA 인터페이스 설계", MemoryType.DECISION)
        time.sleep(0.01)

        # Search before rename
        before = store.search("FPGA 인터페이스", project="proj_a", n_results=1)
        assert len(before) == 1
        assert "AXI4" in before[0]["content"]

        store.rename_project("proj_a", "proj_b")

        # Search after rename
        after = store.search("FPGA 인터페이스", project="proj_b", n_results=1)
        assert len(after) == 1
        assert "AXI4" in after[0]["content"]

    def test_rename_updates_metadata(self, store: MemoryStore) -> None:
        """Rename 후 metadata의 project 필드가 갱신되어야 한다."""
        store.store("old_name", "test content", MemoryType.FACT)
        store.rename_project("old_name", "new_name")

        recent = store.get_recent("new_name", n_results=1)
        assert len(recent) == 1
        assert recent[0]["metadata"]["project"] == "new_name"

    def test_rename_nonexistent_source(self, store: MemoryStore) -> None:
        """없는 프로젝트 rename → ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            store.rename_project("ghost", "anything")

    def test_rename_target_exists(self, store: MemoryStore) -> None:
        """타겟이 이미 존재하면 ValueError."""
        store.store("proj_a", "content a", MemoryType.FACT)
        store.store("proj_b", "content b", MemoryType.FACT)
        with pytest.raises(ValueError, match="already exists"):
            store.rename_project("proj_a", "proj_b")

    def test_rename_same_name(self, store: MemoryStore) -> None:
        """같은 이름으로 rename → ValueError."""
        store.store("same", "content", MemoryType.FACT)
        with pytest.raises(ValueError, match="same"):
            store.rename_project("same", "same")

    def test_rename_empty_project(self, store: MemoryStore) -> None:
        """빈 프로젝트도 rename 가능해야 한다."""
        # Create empty collection by accessing it
        store._get_collection("empty_proj")
        # Actually, get_or_create won't show in project_exists unless we store something
        # So let's store and delete
        doc_id = store.store("empty_proj", "temp", MemoryType.FACT)
        store.delete("empty_proj", doc_id)

        result = store.rename_project("empty_proj", "renamed_empty")
        # soft-deleted memory still exists in ChromaDB, gets moved with rename
        assert result["memories_moved"] == 1


class TestDeleteProject:
    def test_delete_basic(self, store: MemoryStore) -> None:
        """메모리 저장 후 삭제 → list에서 소멸."""
        _store_samples(store, "doomed", 3)
        assert store.project_exists("doomed") is True

        result = store.delete_project("doomed")
        assert result["project"] == "doomed"
        assert result["memories_deleted"] == 3
        assert store.project_exists("doomed") is False
        assert "doomed" not in store.list_projects()

    def test_delete_nonexistent(self, store: MemoryStore) -> None:
        """없는 프로젝트 삭제 → ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            store.delete_project("ghost")

    def test_delete_then_recreate(self, store: MemoryStore) -> None:
        """삭제 후 같은 이름으로 재생성 가능."""
        store.store("recyclable", "first life", MemoryType.FACT)
        store.delete_project("recyclable")

        # Should be able to create new memories with same name
        doc_id = store.store("recyclable", "second life", MemoryType.FACT)
        assert doc_id.startswith("recyclable_")

        recent = store.get_recent("recyclable", n_results=1)
        assert "second life" in recent[0]["content"]
