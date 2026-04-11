"""Tests for memory_progress_update/get tools."""

from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore

PROJECT = "test_project"
PROGRESS_TAG = "project-progress"


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestProgressStorage:
    """진행상황 문서 저장 구조 테스트."""

    def test_create_progress(self, store: MemoryStore) -> None:
        """진행상황 문서 최초 생성."""
        content = "## 현재 Phase: Phase 1\n## 완료: -\n## 다음: Phase 2"
        mem_id = store.store(PROJECT, content, MemoryType.FACT,
                             tags=[PROGRESS_TAG], importance=9.0)

        results = store.get_by_tag(PROJECT, PROGRESS_TAG)
        assert len(results) == 1
        assert results[0]["content"] == content
        assert results[0]["id"] == mem_id

    def test_update_progress_overwrites(self, store: MemoryStore) -> None:
        """업데이트는 기존 내용을 덮어씀."""
        old = "## 현재 Phase: Phase 1"
        mem_id = store.store(PROJECT, old, MemoryType.FACT,
                             tags=[PROGRESS_TAG], importance=9.0)

        new = "## 현재 Phase: Phase 2\n## 완료: Phase 1"
        store.update(PROJECT, mem_id, content=new, importance=9.0)

        results = store.get_by_tag(PROJECT, PROGRESS_TAG)
        assert len(results) == 1
        assert results[0]["content"] == new

    def test_only_one_progress_document(self, store: MemoryStore) -> None:
        """프로젝트당 진행상황 문서는 하나만 존재해야 함."""
        store.store(PROJECT, "Phase 1", MemoryType.FACT,
                    tags=[PROGRESS_TAG], importance=9.0)

        results = store.get_by_tag(PROJECT, PROGRESS_TAG)
        assert len(results) == 1

    def test_progress_importance_is_critical(self, store: MemoryStore) -> None:
        """진행상황은 importance 9.0 (critical) 으로 저장."""
        store.store(PROJECT, "Phase 1 진행 중", MemoryType.FACT,
                    tags=[PROGRESS_TAG], importance=9.0)

        results = store.get_by_tag(PROJECT, PROGRESS_TAG)
        imp = float(results[0]["metadata"].get("importance", 0))
        assert imp >= 9.0

    def test_progress_isolated_per_project(self, store: MemoryStore) -> None:
        """프로젝트별 진행상황은 독립."""
        store.store("project_a", "A: Phase 1", MemoryType.FACT,
                    tags=[PROGRESS_TAG], importance=9.0)
        store.store("project_b", "B: Phase 3", MemoryType.FACT,
                    tags=[PROGRESS_TAG], importance=9.0)

        results_a = store.get_by_tag("project_a", PROGRESS_TAG)
        results_b = store.get_by_tag("project_b", PROGRESS_TAG)

        assert len(results_a) == 1
        assert len(results_b) == 1
        assert "A: Phase 1" in results_a[0]["content"]
        assert "B: Phase 3" in results_b[0]["content"]

    def test_no_progress_returns_empty(self, store: MemoryStore) -> None:
        """진행상황 없으면 빈 리스트 반환."""
        results = store.get_by_tag("nonexistent_project", PROGRESS_TAG)
        assert results == []

    def test_progress_not_mixed_with_checklist(self, store: MemoryStore) -> None:
        """project-progress 태그가 checklist 태그와 혼용되지 않음."""
        store.store(PROJECT, "Phase 1 진행 중", MemoryType.FACT,
                    tags=[PROGRESS_TAG], importance=9.0)
        store.store(PROJECT, "## Checklist: 배포\n- [ ] 항목", MemoryType.FACT,
                    tags=["checklist", "checklist:배포"], importance=7.0)

        progress = store.get_by_tag(PROJECT, PROGRESS_TAG)
        checklists = store.get_by_tag(PROJECT, "checklist")

        assert len(progress) == 1
        assert len(checklists) == 1
        assert "Phase" in progress[0]["content"]
        assert "Checklist" in checklists[0]["content"]
