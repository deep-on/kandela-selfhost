"""Tests for memory_update feature."""

from pathlib import Path

import pytest

from memory_mcp.constants import IMPORTANCE_DEFAULT, IMPORTANCE_MAX, MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestMemoryUpdate:
    """Test store.update() method."""

    def test_update_content_only(self, store: MemoryStore) -> None:
        """content만 수정 시 re-embed + created_at 보존."""
        doc_id = store.store("proj", "원래 내용입니다")
        col = store._get_collection("proj")
        original_meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        original_created = original_meta["created_at"]

        result = store.update("proj", doc_id, content="수정된 내용입니다")

        assert "content" in result["updated_fields"]
        updated = col.get(ids=[doc_id], include=["documents", "metadatas"])
        assert updated["documents"][0] == "수정된 내용입니다"
        assert updated["metadatas"][0]["created_at"] == original_created

    def test_update_memory_type_only(self, store: MemoryStore) -> None:
        """memory_type만 수정."""
        doc_id = store.store("proj", "설계 결정 사항")
        result = store.update("proj", doc_id, memory_type=MemoryType.DECISION)

        assert "memory_type" in result["updated_fields"]
        col = store._get_collection("proj")
        meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        assert meta["type"] == "decision"

    def test_update_importance_only(self, store: MemoryStore) -> None:
        """importance만 수정."""
        doc_id = store.store("proj", "일반 메모", importance=5.0)
        result = store.update("proj", doc_id, importance=8.0)

        assert "importance" in result["updated_fields"]
        assert result["importance"] == 8.0

    def test_update_tags_only(self, store: MemoryStore) -> None:
        """tags 수정."""
        doc_id = store.store("proj", "태그 테스트", tags=["old"])
        result = store.update("proj", doc_id, tags=["new", "updated"])

        assert "tags" in result["updated_fields"]
        col = store._get_collection("proj")
        meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        import json
        assert json.loads(meta["tags"]) == ["new", "updated"]

    def test_update_tags_to_empty(self, store: MemoryStore) -> None:
        """tags를 빈 리스트로 지우기."""
        doc_id = store.store("proj", "태그 삭제 테스트", tags=["a", "b"])
        result = store.update("proj", doc_id, tags=[])

        assert "tags" in result["updated_fields"]
        col = store._get_collection("proj")
        meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        import json
        assert json.loads(meta["tags"]) == []

    def test_update_multiple_fields(self, store: MemoryStore) -> None:
        """복합 수정 (content + importance)."""
        doc_id = store.store("proj", "원래 내용", importance=5.0)
        result = store.update(
            "proj", doc_id, content="새 내용", importance=7.0,
        )
        assert "content" in result["updated_fields"]
        assert "importance" in result["updated_fields"]

    def test_update_content_re_embeds(self, store: MemoryStore) -> None:
        """content 변경 후 검색 결과에 반영."""
        doc_id = store.store("proj", "파이썬 프로그래밍 언어")

        store.update("proj", doc_id, content="자바스크립트 웹 개발")

        # 새 내용으로 검색
        results = store.search("자바스크립트", project="proj")
        assert len(results) >= 1
        assert results[0]["id"] == doc_id
        assert "자바스크립트" in results[0]["content"]

    def test_update_preserves_created_at(self, store: MemoryStore) -> None:
        """created_at과 created_ts 보존."""
        doc_id = store.store("proj", "타임스탬프 테스트")
        col = store._get_collection("proj")
        original = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]

        store.update("proj", doc_id, content="수정됨")

        updated = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        assert updated["created_at"] == original["created_at"]
        assert updated["created_ts"] == original["created_ts"]

    def test_update_preserves_usage_counters(self, store: MemoryStore) -> None:
        """recall_count, search_count 보존."""
        doc_id = store.store("proj", "카운터 테스트")
        store.update_usage_counters("proj", [doc_id], "recall_count")
        store.update_usage_counters("proj", [doc_id], "search_count")
        store.update_usage_counters("proj", [doc_id], "search_count")

        store.update("proj", doc_id, content="수정된 카운터 테스트")

        col = store._get_collection("proj")
        meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        assert meta["recall_count"] == 1
        assert meta["search_count"] == 2

    def test_update_adds_updated_at(self, store: MemoryStore) -> None:
        """updated_at 타임스탬프 추가."""
        doc_id = store.store("proj", "업데이트 테스트")
        store.update("proj", doc_id, importance=7.0)

        col = store._get_collection("proj")
        meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        assert "updated_at" in meta
        assert len(meta["updated_at"]) > 0

    def test_update_nonexistent_id_raises(self, store: MemoryStore) -> None:
        """존재하지 않는 ID → ValueError."""
        with pytest.raises(ValueError, match="not found"):
            store.update("proj", "nonexistent_id_12345", content="test")

    def test_update_applies_importance_rules_on_content_change(
        self, store: MemoryStore,
    ) -> None:
        """content 변경 시 importance rules 재적용."""
        doc_id = store.store("proj", "일반적인 메모", importance=5.0)

        # SSH 패턴이 포함된 내용으로 변경 → 보너스 적용
        result = store.update(
            "proj", doc_id,
            content="ssh testuser@test-server docker compose up",
        )
        # SSH(+2.0) + docker(+1.5) 보너스가 base importance(5.0)에 적용
        assert result["importance"] > 5.0

    def test_update_content_changes_search_results(
        self, store: MemoryStore,
    ) -> None:
        """수정 후 이전 내용으로는 검색 안 되고 새 내용으로 검색됨."""
        doc_id = store.store("proj", "오래된 서버 설정 정보")

        store.update("proj", doc_id, content="새로운 데이터베이스 스키마")

        # 새 내용으로 검색
        new_results = store.search("데이터베이스 스키마", project="proj")
        assert any(r["id"] == doc_id for r in new_results)


class TestMemoryUpdateInputModel:
    """Test MemoryUpdateInput Pydantic model."""

    def test_at_least_one_field_required(self) -> None:
        """수정 필드 없이 호출 시 ValidationError."""
        from memory_mcp.tools.models import MemoryUpdateInput

        with pytest.raises(Exception):  # noqa: B017
            MemoryUpdateInput(project="proj", memory_id="id123")

    def test_content_only_valid(self) -> None:
        from memory_mcp.tools.models import MemoryUpdateInput

        inp = MemoryUpdateInput(
            project="proj", memory_id="id123", content="new content",
        )
        assert inp.content == "new content"
        assert inp.memory_type is None
        assert inp.importance is None
        assert inp.tags is None

    def test_importance_only_valid(self) -> None:
        from memory_mcp.tools.models import MemoryUpdateInput

        inp = MemoryUpdateInput(
            project="proj", memory_id="id123", importance=8.0,
        )
        assert inp.importance == 8.0

    def test_tags_empty_list_valid(self) -> None:
        from memory_mcp.tools.models import MemoryUpdateInput

        inp = MemoryUpdateInput(
            project="proj", memory_id="id123", tags=[],
        )
        assert inp.tags == []

    def test_importance_validation(self) -> None:
        from memory_mcp.tools.models import MemoryUpdateInput

        with pytest.raises(Exception):  # noqa: B017
            MemoryUpdateInput(
                project="proj", memory_id="id123", importance=11.0,
            )
