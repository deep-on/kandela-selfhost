"""Tests for importance-based memory system (Phase 9)."""

from pathlib import Path

import pytest

from memory_mcp.constants import (
    IMPORTANCE_CRITICAL_THRESHOLD,
    IMPORTANCE_DEFAULT,
    IMPORTANCE_LOW_THRESHOLD,
    IMPORTANCE_MAX,
    IMPORTANCE_MIN,
    PRIORITY_TO_IMPORTANCE,
    MemoryPriority,
    MemoryType,
)
from memory_mcp.db.store import MemoryStore
from memory_mcp.importance.rules import (
    IMPORTANCE_RULES,
    ImportanceRule,
    apply_rule_bonus,
)
from memory_mcp.importance.scorer import (
    compute_effective_importance,
    compute_retrieval_score,
    compute_usage_bonus,
    importance_to_decay_rate,
)


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


# ── Constants Tests ──────────────────────────────────────────────


class TestImportanceConstants:
    """Test importance system constants."""

    def test_importance_range(self) -> None:
        assert IMPORTANCE_MIN == 1.0
        assert IMPORTANCE_MAX == 10.0
        assert IMPORTANCE_MIN < IMPORTANCE_DEFAULT < IMPORTANCE_MAX

    def test_priority_to_importance_mapping(self) -> None:
        assert PRIORITY_TO_IMPORTANCE["critical"] == 9.0
        assert PRIORITY_TO_IMPORTANCE["normal"] == 5.0
        assert PRIORITY_TO_IMPORTANCE["low"] == 2.0

    def test_thresholds_consistent(self) -> None:
        assert IMPORTANCE_CRITICAL_THRESHOLD >= 9.0
        assert IMPORTANCE_LOW_THRESHOLD <= 3.0
        assert IMPORTANCE_LOW_THRESHOLD < IMPORTANCE_CRITICAL_THRESHOLD


# ── Rule Engine Tests ────────────────────────────────────────────


class TestImportanceRules:
    """Test server-side importance rule engine."""

    def test_ssh_pattern_bonus(self) -> None:
        """SSH 패턴이 포함된 콘텐츠에 보너스."""
        result = apply_rule_bonus("ssh user@prod-server:/app", [], 5.0)
        assert result > 5.0

    def test_ssh_with_port(self) -> None:
        """SSH + IP:PORT 패턴 동시 매칭."""
        result = apply_rule_bonus(
            "ssh testuser@10.0.0.1:22 에 접속", [], 5.0,
        )
        assert result > 6.5  # SSH(+2) + IP:PORT(+1.5)

    def test_docker_command_bonus(self) -> None:
        result = apply_rule_bonus("docker compose up -d", [], 5.0)
        assert result == 6.5  # +1.5

    def test_docker_exec_bonus(self) -> None:
        result = apply_rule_bonus("docker exec -it container bash", [], 5.0)
        assert result == 6.5

    def test_auto_saved_tag_penalty(self) -> None:
        """auto-saved 태그는 페널티."""
        result = apply_rule_bonus("some content", ["auto-saved"], 5.0)
        assert result == 2.0  # 5.0 - 3.0

    def test_multiple_rules_stack(self) -> None:
        """여러 규칙이 동시에 적용 (additive)."""
        result = apply_rule_bonus(
            "ssh deploy@server docker compose up", [], 5.0,
        )
        # SSH(+2.0) + Docker(+1.5) = 8.5
        assert result == 8.5

    def test_gotcha_bonus(self) -> None:
        result = apply_rule_bonus("주의: 이 설정은 특수하다", [], 5.0)
        assert result > 5.0

    def test_do_not_pattern(self) -> None:
        result = apply_rule_bonus("절대 이 파일을 삭제하지 마", [], 5.0)
        assert result > 5.0

    def test_not_supported_pattern(self) -> None:
        result = apply_rule_bonus(
            "ChromaDB local은 BM25를 지원하지 않는다", [], 5.0,
        )
        assert result > 5.0

    def test_clamp_to_max(self) -> None:
        """결과가 10.0을 초과하면 클램프."""
        result = apply_rule_bonus(
            "ssh user@host docker run 주의사항 절대금지",
            [], 9.0,
        )
        assert result == IMPORTANCE_MAX

    def test_clamp_to_min(self) -> None:
        """결과가 1.0 미만이면 클램프."""
        result = apply_rule_bonus("simple note", ["auto-saved"], 2.0)
        assert result == IMPORTANCE_MIN  # 2.0 - 3.0 → clamped to 1.0

    def test_no_matching_rules(self) -> None:
        """매칭 규칙 없으면 base 그대로."""
        result = apply_rule_bonus("ordinary content", [], 5.0)
        assert result == 5.0

    def test_rules_list_not_empty(self) -> None:
        assert len(IMPORTANCE_RULES) > 0

    def test_all_rules_have_names(self) -> None:
        for rule in IMPORTANCE_RULES:
            assert rule.name
            assert rule.pattern is not None or rule.tag_match is not None

    # ── User emphasis / semantic urgency tests ──

    def test_emphasis_remember_korean(self) -> None:
        """'꼭 기억해' 패턴 감지."""
        result = apply_rule_bonus("이건 꼭 기억해. DB 포트는 5432야.", [], 5.0)
        assert result >= 7.5  # +2.5

    def test_emphasis_dont_forget_korean(self) -> None:
        """'잊으면 안돼' 패턴 감지."""
        result = apply_rule_bonus("잊으면 안돼, 배포 전에 테스트 필수", [], 5.0)
        assert result >= 7.5

    def test_emphasis_never_forget_korean(self) -> None:
        """'잊지 마' 패턴 감지."""
        result = apply_rule_bonus("이 설정값 잊지 마", [], 5.0)
        assert result >= 7.5

    def test_emphasis_very_important_korean(self) -> None:
        """'매우 중요' 패턴 감지."""
        result = apply_rule_bonus("매우 중요한 설정: timeout=30", [], 5.0)
        assert result >= 7.0  # +2.0

    def test_emphasis_essential_korean(self) -> None:
        """'명심' 패턴 감지."""
        result = apply_rule_bonus("명심할 것: 이 API는 rate limit 있음", [], 5.0)
        assert result >= 6.5  # +1.5

    def test_emphasis_remember_english(self) -> None:
        """'must remember' 패턴 감지."""
        result = apply_rule_bonus("Must remember: the API key rotates weekly", [], 5.0)
        assert result >= 7.5

    def test_emphasis_dont_forget_english(self) -> None:
        """'don't forget' 패턴 감지."""
        result = apply_rule_bonus("Don't forget to run migrations before deploy", [], 5.0)
        assert result >= 7.5

    def test_emphasis_very_important_english(self) -> None:
        """'very important' 패턴 감지."""
        result = apply_rule_bonus("Very important: always use UTC timestamps", [], 5.0)
        assert result >= 7.0

    def test_emphasis_stacks_with_other_rules(self) -> None:
        """강조 패턴이 다른 규칙과 중첩 적용."""
        result = apply_rule_bonus(
            "꼭 기억해: ssh testuser@test-server docker compose up", [], 5.0,
        )
        # remember(+2.5) + SSH(+2.0) + docker(+1.5) = 11.0 → clamped to 10.0
        assert result == IMPORTANCE_MAX

    def test_emphasis_no_false_positive(self) -> None:
        """일반 텍스트에서는 강조 보너스 없음."""
        result = apply_rule_bonus("오늘 회의에서 결정된 사항 정리", [], 5.0)
        assert result == 5.0

    def test_emphasis_important_colon_pattern(self) -> None:
        """'중요:' 접두어 패턴 감지."""
        result = apply_rule_bonus("중요: 이 모듈은 Python 3.11+ 필수", [], 5.0)
        assert result >= 6.5

    # ── Command fix / gotcha / unfinished tag tests (P5/P4) ──

    def test_command_fix_pattern_korean(self) -> None:
        """'올바른 명령' 패턴 감지 (+1.5)."""
        result = apply_rule_bonus("올바른 명령: docker compose up -d --no-deps", [], 5.0)
        assert result >= 6.5

    def test_command_fix_pattern_english(self) -> None:
        """'instead use' 패턴 감지 (+1.5)."""
        result = apply_rule_bonus("Use --no-deps instead use full up -d", [], 5.0)
        assert result >= 6.5

    def test_error_lesson_pattern_korean(self) -> None:
        """'실패 이유' 패턴 감지 (+1.5)."""
        result = apply_rule_bonus("실패 이유: --no-deps 없이 up하면 다른 컨테이너 중단", [], 5.0)
        assert result >= 6.5

    def test_error_lesson_pattern_english(self) -> None:
        """'failed because' 패턴 감지 (+1.5)."""
        result = apply_rule_bonus("The command failed because node is not installed", [], 5.0)
        assert result >= 6.5

    def test_gotcha_tag_bonus(self) -> None:
        """gotcha 태그 있으면 +1.0."""
        result = apply_rule_bonus("some content", ["gotcha"], 5.0)
        assert result == 6.0

    def test_unfinished_tag_bonus(self) -> None:
        """unfinished 태그 있으면 +1.5."""
        result = apply_rule_bonus("3/5 단계 완료", ["unfinished", "workflow"], 5.0)
        assert result == 6.5

    def test_gotcha_tag_stacks_with_content(self) -> None:
        """gotcha 태그 + 주의 패턴 = +1.0 + +1.5."""
        result = apply_rule_bonus("주의: 이것은 gotcha 사항", ["gotcha"], 5.0)
        assert result >= 7.5  # +1.0 (tag) + +1.5 (content pattern)

    def test_custom_rule(self) -> None:
        """ImportanceRule 직접 생성 테스트."""
        import re

        rule = ImportanceRule(
            name="test_rule",
            bonus=1.0,
            pattern=re.compile(r"custom_pattern"),
        )
        assert rule.name == "test_rule"
        assert rule.bonus == 1.0


# ── Scorer Tests ─────────────────────────────────────────────────


class TestDecayFromImportance:
    """Test importance → decay rate conversion."""

    def test_max_importance_zero_decay(self) -> None:
        assert importance_to_decay_rate(10.0) == 0.0

    def test_min_importance_max_decay(self) -> None:
        assert importance_to_decay_rate(1.0) == 0.005

    def test_middle_importance(self) -> None:
        rate = importance_to_decay_rate(5.5)
        assert 0.0 < rate < 0.005

    def test_monotonically_decreasing(self) -> None:
        """Higher importance → lower decay rate."""
        rates = [importance_to_decay_rate(float(i)) for i in range(1, 11)]
        for a, b in zip(rates, rates[1:]):
            assert a >= b

    def test_old_critical_equivalent(self) -> None:
        """importance=9.0 (old CRITICAL) should have near-zero decay."""
        rate = importance_to_decay_rate(9.0)
        assert rate < 0.001

    def test_old_low_equivalent(self) -> None:
        """importance=2.0 (old LOW) should have high decay."""
        rate = importance_to_decay_rate(2.0)
        assert rate > 0.004


class TestUsageBonus:
    """Test usage-based importance bonus."""

    def test_zero_usage_zero_bonus(self) -> None:
        assert compute_usage_bonus(0, 0) == 0.0

    def test_recall_weighted_more(self) -> None:
        """Recall은 search보다 2배 가중치."""
        recall_bonus = compute_usage_bonus(1, 0)
        search_bonus = compute_usage_bonus(0, 1)
        assert recall_bonus > search_bonus

    def test_grows_logarithmically(self) -> None:
        b1 = compute_usage_bonus(10, 10)
        b2 = compute_usage_bonus(100, 100)
        assert b2 > b1
        assert b2 < b1 * 5  # sub-linear growth

    def test_positive_for_any_usage(self) -> None:
        assert compute_usage_bonus(1, 0) > 0
        assert compute_usage_bonus(0, 1) > 0


class TestEffectiveImportance:
    """Test effective importance computation."""

    def test_no_usage_returns_stored(self) -> None:
        assert compute_effective_importance(5.0, 0, 0) == 5.0

    def test_with_usage_increases(self) -> None:
        assert compute_effective_importance(5.0, 5, 5) > 5.0

    def test_clamped_to_max(self) -> None:
        assert compute_effective_importance(9.5, 100, 100) == IMPORTANCE_MAX

    def test_clamped_to_min(self) -> None:
        assert compute_effective_importance(1.0, 0, 0) == IMPORTANCE_MIN


class TestRetrievalScore:
    """Test composite retrieval score."""

    def test_all_max(self) -> None:
        score = compute_retrieval_score(1.0, 1.0, 1.0)
        assert abs(score - 1.0) < 0.001

    def test_all_zero(self) -> None:
        score = compute_retrieval_score(0.0, 0.0, 0.0)
        assert abs(score) < 0.001

    def test_relevance_dominates(self) -> None:
        """relevance weight (0.6)이 가장 큰 영향."""
        high_rel = compute_retrieval_score(1.0, 0.0, 0.0)
        high_imp = compute_retrieval_score(0.0, 1.0, 0.0)
        high_rec = compute_retrieval_score(0.0, 0.0, 1.0)
        assert high_rel > high_imp > high_rec

    def test_custom_weights(self) -> None:
        score = compute_retrieval_score(
            1.0, 0.5, 0.5, alpha=0.5, beta=0.3, gamma=0.2,
        )
        expected = 0.5 * 1.0 + 0.3 * 0.5 + 0.2 * 0.5
        assert abs(score - expected) < 0.001


# ── Migration Tests ──────────────────────────────────────────────


class TestImportanceMigration:
    """Test v3 metadata migration (priority → importance)."""

    def test_v3_migration_adds_importance(self, store: MemoryStore) -> None:
        """새로 저장된 메모리는 이미 importance가 있어야 함."""
        store.store("proj", "critical info", priority=MemoryPriority.CRITICAL)
        store.store("proj", "normal info")
        store.store("proj", "low info", priority=MemoryPriority.LOW)

        result = store.migrate_metadata_v3()
        assert result["errors"] == 0

        col = store._get_collection("proj")
        all_data = col.get(include=["metadatas"])
        for meta in all_data["metadatas"]:
            assert "importance" in meta
            assert isinstance(meta["importance"], float)
            assert "recall_count" in meta
            assert "search_count" in meta

    def test_v3_migration_idempotent(self, store: MemoryStore) -> None:
        """2번 실행해도 안전."""
        store.store("proj", "test content")
        r1 = store.migrate_metadata_v3()
        r2 = store.migrate_metadata_v3()
        assert r2["updated"] == 0
        assert r2["skipped"] >= 1

    def test_v3_migration_legacy_data(self, store: MemoryStore) -> None:
        """priority만 있고 importance 없는 레거시 데이터 처리."""
        col = store._get_collection("legacy")
        col.add(
            ids=["legacy_1"],
            documents=["old memory about SSH deploy"],
            embeddings=[store._embed("old memory about SSH deploy")],
            metadatas=[{
                "project": "legacy",
                "type": "fact",
                "priority": "critical",
                "tags": "[]",
                "created_at": "2026-01-01T00:00:00+00:00",
                "created_ts": 1735689600,
            "deleted_ts": 0,
            }],
        )

        result = store.migrate_metadata_v3()
        assert result["updated"] >= 1

        raw = col.get(ids=["legacy_1"], include=["metadatas"])
        meta = raw["metadatas"][0]
        assert meta["importance"] == 9.0  # critical → 9.0
        assert meta["recall_count"] == 0
        assert meta["search_count"] == 0

    def test_v3_migration_empty_db(self, store: MemoryStore) -> None:
        result = store.migrate_metadata_v3()
        assert result["projects_scanned"] == 0
        assert result["updated"] == 0


# ── Store Integration Tests ──────────────────────────────────────


class TestStoreWithImportance:
    """Test store() with importance parameter."""

    def test_store_default_importance(self, store: MemoryStore) -> None:
        """기본 importance는 5.0."""
        doc_id = store.store("proj", "some fact")
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["importance"] == 5.0

    def test_store_explicit_importance(self, store: MemoryStore) -> None:
        """명시적 importance 설정."""
        doc_id = store.store("proj", "database port is 5432", importance=8.5)
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["importance"] == 8.5

    def test_store_importance_with_rule_bonus(self, store: MemoryStore) -> None:
        """SSH 패턴 콘텐츠는 규칙에 의해 보너스."""
        doc_id = store.store(
            "proj", "ssh user@server deploy 경로", importance=5.0,
        )
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["importance"] > 5.0

    def test_store_backward_compat_priority(self, store: MemoryStore) -> None:
        """priority 파라미터만 제공 시 importance로 변환."""
        doc_id = store.store(
            "proj", "info", priority=MemoryPriority.CRITICAL,
        )
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["importance"] == 9.0

    def test_store_importance_overrides_priority(self, store: MemoryStore) -> None:
        """importance 명시 시 priority 무시."""
        doc_id = store.store(
            "proj", "plain info",
            priority=MemoryPriority.LOW,
            importance=7.0,
        )
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["importance"] == 7.0

    def test_store_auto_saved_penalty(self, store: MemoryStore) -> None:
        """auto-saved 태그는 자동으로 importance 감소."""
        doc_id = store.store(
            "proj", "auto saved content",
            tags=["auto-saved"],
            importance=5.0,
        )
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["importance"] == 2.0  # 5.0 - 3.0

    def test_store_has_usage_counters(self, store: MemoryStore) -> None:
        """새 메모리는 usage 카운터 0으로 초기화."""
        doc_id = store.store("proj", "content")
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        meta = raw["metadatas"][0]
        assert meta["recall_count"] == 0
        assert meta["search_count"] == 0

    def test_store_has_priority_field_for_compat(self, store: MemoryStore) -> None:
        """backward compat를 위해 priority 필드도 저장."""
        doc_id = store.store("proj", "content")
        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert "priority" in raw["metadatas"][0]


# ── Pydantic Model Tests ─────────────────────────────────────────


class TestMemoryStoreInputModel:
    """Test MemoryStoreInput Pydantic model."""

    def test_default_importance(self) -> None:
        from memory_mcp.tools.models import MemoryStoreInput

        inp = MemoryStoreInput(project="proj", content="test")
        assert inp.importance == IMPORTANCE_DEFAULT
        assert inp.priority is None

    def test_priority_converts_to_importance(self) -> None:
        from memory_mcp.tools.models import MemoryStoreInput

        inp = MemoryStoreInput(
            project="proj", content="test", priority="critical",
        )
        assert inp.importance == 9.0

    def test_explicit_importance(self) -> None:
        from memory_mcp.tools.models import MemoryStoreInput

        inp = MemoryStoreInput(
            project="proj", content="test", importance=7.5,
        )
        assert inp.importance == 7.5

    def test_importance_validation_min(self) -> None:
        from memory_mcp.tools.models import MemoryStoreInput

        with pytest.raises(Exception):  # noqa: B017
            MemoryStoreInput(
                project="proj", content="test", importance=0.5,
            )

    def test_importance_validation_max(self) -> None:
        from memory_mcp.tools.models import MemoryStoreInput

        with pytest.raises(Exception):  # noqa: B017
            MemoryStoreInput(
                project="proj", content="test", importance=11.0,
            )


class TestMemorySearchInputModel:
    """Test MemorySearchInput importance filter fields."""

    def test_importance_min_max_filter(self) -> None:
        from memory_mcp.tools.models import MemorySearchInput

        inp = MemorySearchInput(
            query="test",
            project="proj",
            importance_min=7.0,
            importance_max=10.0,
        )
        assert inp.importance_min == 7.0
        assert inp.importance_max == 10.0

    def test_priority_filter_converts_critical(self) -> None:
        from memory_mcp.tools.models import MemorySearchInput

        inp = MemorySearchInput(
            query="test", project="proj", priority="critical",
        )
        assert inp.importance_min == IMPORTANCE_CRITICAL_THRESHOLD

    def test_priority_filter_converts_low(self) -> None:
        from memory_mcp.tools.models import MemorySearchInput

        inp = MemorySearchInput(
            query="test", project="proj", priority="low",
        )
        assert inp.importance_max == IMPORTANCE_LOW_THRESHOLD

    def test_priority_filter_converts_normal(self) -> None:
        from memory_mcp.tools.models import MemorySearchInput

        inp = MemorySearchInput(
            query="test", project="proj", priority="normal",
        )
        assert inp.importance_min == IMPORTANCE_LOW_THRESHOLD
        assert inp.importance_max == IMPORTANCE_CRITICAL_THRESHOLD

    def test_explicit_importance_range_not_overridden(self) -> None:
        """importance_min/max 명시 시 priority 변환 안 함."""
        from memory_mcp.tools.models import MemorySearchInput

        inp = MemorySearchInput(
            query="test", project="proj",
            priority="critical",
            importance_min=5.0,
            importance_max=8.0,
        )
        assert inp.importance_min == 5.0
        assert inp.importance_max == 8.0


# ═══════════════════════════════════════════════════════════════════
# Phase 9C: Usage Tracking
# ═══════════════════════════════════════════════════════════════════

class TestUsageTracking:
    """Test update_usage_counters method."""

    def test_increment_search_count(self, store: MemoryStore) -> None:
        """search_count should be incremented."""
        doc_id = store.store("proj", "some fact", MemoryType.FACT)

        updated = store.update_usage_counters("proj", [doc_id], "search_count")
        assert updated == 1

        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["search_count"] == 1

    def test_increment_recall_count(self, store: MemoryStore) -> None:
        """recall_count should be incremented."""
        doc_id = store.store("proj", "important fact", MemoryType.FACT)

        store.update_usage_counters("proj", [doc_id], "recall_count")
        store.update_usage_counters("proj", [doc_id], "recall_count")

        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        assert raw["metadatas"][0]["recall_count"] == 2

    def test_multiple_ids_updated(self, store: MemoryStore) -> None:
        """Multiple memory IDs should all be updated."""
        id1 = store.store("proj", "fact 1", MemoryType.FACT)
        id2 = store.store("proj", "fact 2", MemoryType.FACT)

        updated = store.update_usage_counters("proj", [id1, id2], "search_count")
        assert updated == 2

        col = store._get_collection("proj")
        raw = col.get(ids=[id1, id2], include=["metadatas"])
        assert raw["metadatas"][0]["search_count"] == 1
        assert raw["metadatas"][1]["search_count"] == 1

    def test_last_accessed_at_updated(self, store: MemoryStore) -> None:
        """last_accessed_at should be set after counter update."""
        doc_id = store.store("proj", "test memory", MemoryType.FACT)

        store.update_usage_counters("proj", [doc_id], "search_count")

        col = store._get_collection("proj")
        raw = col.get(ids=[doc_id], include=["metadatas"])
        meta = raw["metadatas"][0]
        assert "last_accessed_at" in meta
        assert len(meta["last_accessed_at"]) > 0

    def test_invalid_counter_name_returns_zero(self, store: MemoryStore) -> None:
        """Invalid counter name should return 0."""
        doc_id = store.store("proj", "test", MemoryType.FACT)
        assert store.update_usage_counters("proj", [doc_id], "invalid_counter") == 0

    def test_empty_ids_returns_zero(self, store: MemoryStore) -> None:
        """Empty ID list should return 0."""
        assert store.update_usage_counters("proj", [], "search_count") == 0


# ═══════════════════════════════════════════════════════════════════
# Phase 10: Rehearsal Effect Integration Tests (H-2.2)
# ═══════════════════════════════════════════════════════════════════


class TestRehearsalEffectDecay:
    """Test that usage (recall/search) reduces effective decay rate."""

    def test_usage_bonus_lowers_decay_rate(self) -> None:
        """Higher recall_count → higher effective importance → lower decay rate."""
        base_rate = importance_to_decay_rate(
            compute_effective_importance(5.0, 0, 0),
        )
        used_rate = importance_to_decay_rate(
            compute_effective_importance(5.0, 10, 5),
        )
        assert used_rate < base_rate, (
            "Frequently used memory should have lower decay rate"
        )

    def test_extreme_usage_approaches_zero_decay(self) -> None:
        """Very high usage should push decay rate towards zero."""
        rate = importance_to_decay_rate(
            compute_effective_importance(5.0, 100, 100),
        )
        assert rate < 0.002, (
            f"Extremely used memory should have near-zero decay, got {rate}"
        )

    def test_low_importance_with_usage_recovers(self) -> None:
        """Low-importance memory becomes more durable with frequent access."""
        low_no_use = importance_to_decay_rate(
            compute_effective_importance(2.0, 0, 0),
        )
        low_with_use = importance_to_decay_rate(
            compute_effective_importance(2.0, 20, 10),
        )
        assert low_with_use < low_no_use * 0.85, (
            f"Low-importance + heavy usage should significantly reduce decay: "
            f"no_use={low_no_use:.6f}, with_use={low_with_use:.6f}"
        )


class TestRehearsalEffectTimeline:
    """Test time-decay pipeline with last_accessed_at (ACT-R rehearsal)."""

    def test_time_decay_uses_last_accessed(self) -> None:
        """_apply_time_decay should treat recently-accessed memory as fresh."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(hours=720)).isoformat()  # 30 days ago
        recent_time = (now - timedelta(hours=1)).isoformat()  # 1 hour ago

        # Same memory, same importance, same created_at (old)
        base_result = {
            "distance": 0.3,
            "metadata": {
                "importance": 5.0,
                "created_at": old_time,
                "recall_count": 0,
                "search_count": 0,
            },
        }
        accessed_result = {
            "distance": 0.3,
            "metadata": {
                "importance": 5.0,
                "created_at": old_time,
                "last_accessed_at": recent_time,
                "recall_count": 5,
                "search_count": 10,
            },
        }

        from memory_mcp.db.store import MemoryStore

        decayed_base = MemoryStore._apply_time_decay([base_result.copy()])[0]
        decayed_accessed = MemoryStore._apply_time_decay(
            [accessed_result.copy()],
        )[0]

        # Accessed memory should have LESS distance inflation (fresher)
        assert decayed_accessed["distance"] < decayed_base["distance"], (
            "Recently accessed memory should have less time-decay penalty"
        )

    def test_retrieval_score_uses_last_accessed(self) -> None:
        """_apply_retrieval_score recency should use last_accessed_at."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(hours=720)).isoformat()  # 30 days ago
        recent_time = (now - timedelta(hours=1)).isoformat()  # 1 hour ago

        base_result = {
            "distance": 0.3,
            "original_distance": 0.3,
            "metadata": {
                "importance": 5.0,
                "created_at": old_time,
                "recall_count": 0,
                "search_count": 0,
            },
        }
        accessed_result = {
            "distance": 0.3,
            "original_distance": 0.3,
            "metadata": {
                "importance": 5.0,
                "created_at": old_time,
                "last_accessed_at": recent_time,
                "recall_count": 5,
                "search_count": 10,
            },
        }

        from memory_mcp.db.store import MemoryStore

        scored_base = MemoryStore._apply_retrieval_score(
            [base_result.copy()],
        )[0]
        scored_accessed = MemoryStore._apply_retrieval_score(
            [accessed_result.copy()],
        )[0]

        # Accessed memory: higher recency + higher effective importance
        assert scored_accessed["retrieval_score"] > scored_base["retrieval_score"], (
            "Recently accessed memory should have higher retrieval score"
        )

    def test_full_pipeline_rehearsal_effect(self, store: MemoryStore) -> None:
        """End-to-end: frequently accessed memory should rank higher."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(hours=720)).isoformat()

        # Store two memories with same content/importance
        id1 = store.store("proj", "Python async programming guide")
        id2 = store.store("proj", "Python async programming tutorial")

        # Manually set both to old creation time
        col = store._get_collection("proj")
        for doc_id in [id1, id2]:
            raw = col.get(ids=[doc_id], include=["metadatas"])
            meta = raw["metadatas"][0]
            meta["created_at"] = old_time
            col.update(ids=[doc_id], metadatas=[meta])

        # Simulate heavy usage on id1 only
        for _ in range(10):
            store.update_usage_counters("proj", [id1], "recall_count")
        for _ in range(5):
            store.update_usage_counters("proj", [id1], "search_count")

        # Search — id1 should benefit from rehearsal
        results = store.search(
            "Python async programming",
            "proj",
            n_results=2,
            time_weighted=True,
        )
        assert len(results) == 2

        # Find which result is id1 vs id2
        r1 = next(r for r in results if r["id"] == id1)
        r2 = next(r for r in results if r["id"] == id2)

        # id1 should have better (lower) distance due to rehearsal
        assert r1["distance"] < r2["distance"], (
            f"Rehearsed memory (d={r1['distance']:.4f}) should have "
            f"less decay than unrehearsed (d={r2['distance']:.4f})"
        )

        # id1 should have higher retrieval score
        if "retrieval_score" in r1:
            assert r1["retrieval_score"] > r2["retrieval_score"], (
                "Rehearsed memory should have higher retrieval score"
            )


# ═══════════════════════════════════════════════════════════════════
# Phase MF-3: Code-Readable Content Detection
# ═══════════════════════════════════════════════════════════════════


class TestCodeReadableDetection:
    """Test detect_code_readable function."""

    def test_code_structure_detected(self) -> None:
        """프로젝트 구조 설명은 code-readable로 감지."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable("프로젝트 구조: src/에 main.py가 있다", [])
        assert result is not None
        assert "project structure" in result

    def test_import_description_detected(self) -> None:
        """import 설명은 code-readable로 감지."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable("from fastapi import FastAPI를 사용", [])
        assert result is not None
        assert "imports" in result

    def test_function_description_detected(self) -> None:
        """함수 시그니처 설명은 code-readable로 감지."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable("class UserService has methods for auth", [])
        assert result is not None
        assert "function/class" in result

    def test_gotcha_content_not_detected(self) -> None:
        """gotcha 태그가 있으면 감지하지 않음."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable("프로젝트 구조를 변경하면 안됨", ["gotcha"])
        assert result is None

    def test_reason_exempts_detection(self) -> None:
        """이유/reason이 포함되면 code-readable 아님 (WHY 정보)."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable(
            "프로젝트 구조를 이렇게 한 이유: 모듈 분리를 위해", [],
        )
        assert result is None

    def test_gotcha_exempts_detection(self) -> None:
        """gotcha/주의 키워드가 있으면 감지하지 않음."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable(
            "프로젝트 구조 변경 시 주의: 순환참조 발생", [],
        )
        assert result is None

    def test_decision_content_passes(self) -> None:
        """결정 이유는 code-invisible이므로 통과."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable(
            "FastAPI를 선택한 이유: 비동기 지원이 우수하고 타입 힌트 기반", [],
        )
        assert result is None

    def test_pure_decision_no_pattern(self) -> None:
        """코드 패턴 없는 결정은 감지하지 않음."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable(
            "커서 기반 페이지네이션을 채택: offset은 동시 삽입 시 중복 발생", [],
        )
        assert result is None

    def test_infra_tag_exempts(self) -> None:
        """infra/deploy 태그는 항상 통과."""
        from memory_mcp.importance.rules import detect_code_readable

        result = detect_code_readable("project structure on server", ["deploy"])
        assert result is None

    def test_code_structure_penalty_applied(self) -> None:
        """Code-readable 콘텐츠에 importance 페널티 적용."""
        result = apply_rule_bonus("프로젝트 구조: src/ 안에 main.py", [], 7.0)
        assert result < 7.0  # penalty applied

    def test_gotcha_with_structure_no_penalty(self) -> None:
        """gotcha 태그 + 구조 설명 = 페널티 상쇄."""
        result = apply_rule_bonus("프로젝트 구조 바꾸면 삽질 주의", ["gotcha"], 7.0)
        # gotcha_or_caveat (+1.5) + gotcha_tag (+1.0) + code_structure (-2.0) = +0.5
        assert result >= 7.0
