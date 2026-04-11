"""Tests for memory_checklist_add/get/done tools."""

from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


PROJECT = "test_project"
NAME = "배포 전 확인"
TAG = f"checklist:{NAME}"


class TestChecklistStorage:
    """체크리스트 저장 및 태그 구조 테스트."""

    def test_store_and_find_by_tag(self, store: MemoryStore) -> None:
        """checklist:{name} 태그로 저장 및 조회."""
        content = f"## Checklist: {NAME}\n- [ ] Docker 빌드"
        store.store(PROJECT, content, MemoryType.FACT, tags=["checklist", TAG], importance=7.0)

        results = store.get_by_tag(PROJECT, TAG)
        assert len(results) == 1
        assert NAME in results[0]["content"]
        assert "- [ ] Docker 빌드" in results[0]["content"]

    def test_add_item_via_update(self, store: MemoryStore) -> None:
        """기존 체크리스트에 항목 추가 (update)."""
        content = f"## Checklist: {NAME}\n- [ ] 항목1"
        mem_id = store.store(PROJECT, content, MemoryType.FACT, tags=["checklist", TAG], importance=7.0)

        new_content = content + "\n- [ ] 항목2"
        store.update(PROJECT, mem_id, content=new_content)

        results = store.get_by_tag(PROJECT, TAG)
        assert "- [ ] 항목2" in results[0]["content"]

    def test_mark_done(self, store: MemoryStore) -> None:
        """항목을 완료([ ] → [x]) 처리."""
        content = f"## Checklist: {NAME}\n- [ ] 항목1\n- [ ] 항목2"
        mem_id = store.store(PROJECT, content, MemoryType.FACT, tags=["checklist", TAG], importance=7.0)

        # 첫 번째 항목 완료 처리
        lines = content.split("\n")
        item_lines = [i for i, l in enumerate(lines) if l.startswith("- [")]
        lines[item_lines[0]] = lines[item_lines[0]].replace("- [ ]", "- [x]", 1)
        new_content = "\n".join(lines)
        store.update(PROJECT, mem_id, content=new_content)

        results = store.get_by_tag(PROJECT, TAG)
        updated = results[0]["content"]
        assert "- [x] 항목1" in updated
        assert "- [ ] 항목2" in updated

    def test_multiple_checklists_isolated(self, store: MemoryStore) -> None:
        """다른 이름의 체크리스트는 서로 독립."""
        name_a = "체크리스트A"
        name_b = "체크리스트B"
        store.store(PROJECT, f"## Checklist: {name_a}\n- [ ] A 항목", MemoryType.FACT,
                    tags=["checklist", f"checklist:{name_a}"], importance=7.0)
        store.store(PROJECT, f"## Checklist: {name_b}\n- [ ] B 항목", MemoryType.FACT,
                    tags=["checklist", f"checklist:{name_b}"], importance=7.0)

        results_a = store.get_by_tag(PROJECT, f"checklist:{name_a}")
        results_b = store.get_by_tag(PROJECT, f"checklist:{name_b}")

        assert len(results_a) == 1
        assert len(results_b) == 1
        assert "A 항목" in results_a[0]["content"]
        assert "B 항목" in results_b[0]["content"]

    def test_checklist_category_tag(self, store: MemoryStore) -> None:
        """'checklist' 카테고리 태그로 모든 체크리스트 조회."""
        store.store(PROJECT, "## Checklist: A\n- [ ] 항목", MemoryType.FACT,
                    tags=["checklist", "checklist:A"], importance=7.0)
        store.store(PROJECT, "## Checklist: B\n- [ ] 항목", MemoryType.FACT,
                    tags=["checklist", "checklist:B"], importance=7.0)
        store.store(PROJECT, "일반 메모", MemoryType.FACT, tags=["other"], importance=5.0)

        all_checklists = store.get_by_tag(PROJECT, "checklist", n_results=10)
        assert len(all_checklists) == 2
