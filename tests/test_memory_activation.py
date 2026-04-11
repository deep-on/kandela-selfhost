"""Tests for Memory Activation system (Phase MA).

MA-1: Semantic PreToolUse — gotcha cache + BM25 matching
MA-2: Contextual Re-injection (future)
MA-3: Utilization Tracking (future)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


# ============================================================
# MA-1: Gotcha Cache + BM25 Matching
# ============================================================


class TestGotchaCache:
    """Tests for _get_cached_gotchas() cache behavior."""

    def _make_search_fn(self, results: list[dict] | None = None):
        """Create a mock search function that returns given results."""
        if results is None:
            results = [
                {
                    "id": "g1",
                    "document": "프로덕션 컨테이너에서 pytest 금지. dev에서만 실행",
                    "content": "프로덕션 컨테이너에서 pytest 금지. dev에서만 실행",
                    "metadata": {"importance": 9.5, "tags": '["gotcha"]'},
                },
                {
                    "id": "g2",
                    "document": "배포 시 docker compose up에 --no-deps 필수",
                    "content": "배포 시 docker compose up에 --no-deps 필수",
                    "metadata": {"importance": 9.0, "tags": '["gotcha","deploy"]'},
                },
            ]
        fn = MagicMock(return_value=results)
        return fn

    def test_cache_hit(self):
        """Cache should return same index within TTL."""
        from memory_mcp.templates.hook_prompts import (
            _gotcha_cache,
            _get_cached_gotchas,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        search_fn = self._make_search_fn()

        # First call — cache miss
        idx1, gotchas1 = _get_cached_gotchas("test_proj", search_fn)
        call_count_1 = search_fn.call_count

        # Second call — cache hit
        idx2, gotchas2 = _get_cached_gotchas("test_proj", search_fn)
        call_count_2 = search_fn.call_count

        assert call_count_2 == call_count_1  # No additional search calls
        assert idx1 is idx2  # Same BM25 index object
        assert len(gotchas1) == len(gotchas2)

        invalidate_gotcha_cache()

    def test_cache_ttl_expiry(self):
        """Cache should expire after TTL."""
        from memory_mcp.templates.hook_prompts import (
            _gotcha_cache,
            _gotcha_lock,
            _get_cached_gotchas,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        search_fn = self._make_search_fn()

        # Populate cache
        _get_cached_gotchas("test_proj", search_fn)
        initial_calls = search_fn.call_count

        # Manually expire the cache
        with _gotcha_lock:
            if "test_proj" in _gotcha_cache:
                ts, idx, gotchas = _gotcha_cache["test_proj"]
                _gotcha_cache["test_proj"] = (ts - 400, idx, gotchas)  # 400s ago

        # Should trigger re-fetch
        _get_cached_gotchas("test_proj", search_fn)
        assert search_fn.call_count > initial_calls

        invalidate_gotcha_cache()

    def test_cache_invalidation(self):
        """invalidate_gotcha_cache should clear cache."""
        from memory_mcp.templates.hook_prompts import (
            _gotcha_cache,
            _get_cached_gotchas,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        search_fn = self._make_search_fn()

        _get_cached_gotchas("proj_a", search_fn)
        _get_cached_gotchas("proj_b", search_fn)

        # Invalidate specific project
        invalidate_gotcha_cache("proj_a")
        assert "proj_a" not in _gotcha_cache
        assert "proj_b" in _gotcha_cache

        # Invalidate all
        invalidate_gotcha_cache()
        assert len(_gotcha_cache) == 0

    def test_empty_results(self):
        """Cache should handle empty search results gracefully."""
        from memory_mcp.templates.hook_prompts import (
            _get_cached_gotchas,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        search_fn = self._make_search_fn(results=[])

        idx, gotchas = _get_cached_gotchas("empty_proj", search_fn)
        assert idx is None
        assert gotchas == []

        invalidate_gotcha_cache()

    def test_search_failure_graceful(self):
        """Cache should handle search exceptions gracefully."""
        from memory_mcp.templates.hook_prompts import (
            _get_cached_gotchas,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        search_fn = MagicMock(side_effect=Exception("ChromaDB down"))

        idx, gotchas = _get_cached_gotchas("fail_proj", search_fn)
        assert idx is None
        assert gotchas == []

        invalidate_gotcha_cache()

    def test_projects_isolated(self):
        """Different projects should have independent caches."""
        from memory_mcp.templates.hook_prompts import (
            _get_cached_gotchas,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()

        gotchas_a = [{"id": "a1", "document": "proj A gotcha", "metadata": {}}]
        gotchas_b = [{"id": "b1", "document": "proj B gotcha", "metadata": {}}]

        fn_a = self._make_search_fn(gotchas_a)
        fn_b = self._make_search_fn(gotchas_b)

        _, res_a = _get_cached_gotchas("proj_a", fn_a)
        _, res_b = _get_cached_gotchas("proj_b", fn_b)

        assert len(res_a) == 1
        assert res_a[0]["id"] == "a1"
        assert len(res_b) == 1
        assert res_b[0]["id"] == "b1"

        invalidate_gotcha_cache()


class TestMatchGotchas:
    """Tests for match_gotchas_for_command() BM25 matching."""

    def _search_fn_with_gotchas(self):
        """Return a search fn that provides realistic gotchas."""
        gotchas = [
            {
                "id": "g1",
                "document": "프로덕션 memory-mcp-server 컨테이너에서 pytest 테스트 실행 금지. 반드시 memory-mcp-dev 컨테이너에서만 실행할 것",
                "content": "프로덕션 memory-mcp-server 컨테이너에서 pytest 테스트 실행 금지. 반드시 memory-mcp-dev 컨테이너에서만 실행할 것",
                "metadata": {"importance": 9.5, "tags": '["gotcha"]'},
            },
            {
                "id": "g2",
                "document": "docker compose up -d 실행 시 반드시 --no-deps 플래그 추가 필수. 없으면 다른 컨테이너 중단됨",
                "content": "docker compose up -d 실행 시 반드시 --no-deps 플래그 추가 필수. 없으면 다른 컨테이너 중단됨",
                "metadata": {"importance": 9.0, "tags": '["gotcha","deploy"]'},
            },
            {
                "id": "g3",
                "document": "scp 실행 시 telegram 디렉토리 권한 문제 발생 가능. chown으로 권한 변경 후 재시도",
                "content": "scp 실행 시 telegram 디렉토리 권한 문제 발생 가능. chown으로 권한 변경 후 재시도",
                "metadata": {"importance": 8.5, "tags": '["gotcha"]'},
            },
        ]
        return MagicMock(return_value=gotchas)

    def test_relevant_match(self):
        """Command matching a gotcha should return results."""
        from memory_mcp.templates.hook_prompts import (
            match_gotchas_for_command,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        fn = self._search_fn_with_gotchas()

        matches = match_gotchas_for_command(
            "test_proj",
            'docker exec memory-mcp-server pytest tests/ -v',
            fn,
        )
        # Should match g1 (pytest + memory-mcp-server)
        assert len(matches) >= 1
        matched_ids = [m["id"] for m in matches]
        assert "g1" in matched_ids

        invalidate_gotcha_cache()

    def test_irrelevant_no_match(self):
        """Unrelated commands should return no matches."""
        from memory_mcp.templates.hook_prompts import (
            match_gotchas_for_command,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        fn = self._search_fn_with_gotchas()

        matches = match_gotchas_for_command(
            "test_proj",
            "ls -la /tmp",
            fn,
        )
        assert matches == []

        invalidate_gotcha_cache()

    def test_empty_command(self):
        """Empty command should return no matches."""
        from memory_mcp.templates.hook_prompts import (
            match_gotchas_for_command,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        fn = self._search_fn_with_gotchas()

        assert match_gotchas_for_command("test_proj", "", fn) == []
        assert match_gotchas_for_command("test_proj", "   ", fn) == []

        invalidate_gotcha_cache()

    def test_docker_compose_match(self):
        """docker compose up without --no-deps should match g2."""
        from memory_mcp.templates.hook_prompts import (
            match_gotchas_for_command,
            invalidate_gotcha_cache,
        )

        invalidate_gotcha_cache()
        fn = self._search_fn_with_gotchas()

        matches = match_gotchas_for_command(
            "test_proj",
            "docker compose -f docker/compose.yaml up -d",
            fn,
        )
        matched_ids = [m["id"] for m in matches]
        assert "g2" in matched_ids

        invalidate_gotcha_cache()


class TestFormatGotchaWarning:
    """Tests for format_gotcha_warning()."""

    def test_format_basic(self):
        """Should format matches with importance and content."""
        from memory_mcp.templates.hook_prompts import format_gotcha_warning

        matches = [
            {
                "content": "프로덕션에서 pytest 금지",
                "metadata": {"importance": 9.5},
                "score": 5.0,
            },
        ]
        output = format_gotcha_warning("my_proj", matches)
        assert "[Kandela" in output
        assert "my_proj" in output
        assert "9.5" in output
        assert "pytest" in output

    def test_format_multiple(self):
        """Should format multiple matches with numbering."""
        from memory_mcp.templates.hook_prompts import format_gotcha_warning

        matches = [
            {"content": "gotcha 1", "metadata": {"importance": 9.0}, "score": 5.0},
            {"content": "gotcha 2", "metadata": {"importance": 8.0}, "score": 3.0},
        ]
        output = format_gotcha_warning("proj", matches)
        assert "1." in output
        assert "2." in output


# ============================================================
# MA-2: Topic Buffer + Milestone Re-injection
# ============================================================


class TestTopicBuffer:
    """Tests for append_topic() and get_topic_summary()."""

    def test_append_and_get(self):
        from memory_mcp.templates.hook_prompts import (
            append_topic,
            clear_topic_buffer,
            get_topic_summary,
        )

        clear_topic_buffer()
        append_topic("proj", "docker exec memory-mcp-dev pytest")
        append_topic("proj", "scp file.py test-server:~/")
        summary = get_topic_summary("proj")
        assert "docker" in summary
        assert "scp" in summary
        clear_topic_buffer()

    def test_maxlen_20(self):
        from memory_mcp.templates.hook_prompts import (
            _topic_buffer,
            append_topic,
            clear_topic_buffer,
        )

        clear_topic_buffer()
        for i in range(30):
            append_topic("proj", f"cmd_{i}")
        assert len(_topic_buffer["proj"]) == 20
        clear_topic_buffer()

    def test_empty_command_ignored(self):
        from memory_mcp.templates.hook_prompts import (
            append_topic,
            clear_topic_buffer,
            get_topic_summary,
        )

        clear_topic_buffer()
        append_topic("proj", "")
        append_topic("proj", "   ")
        assert get_topic_summary("proj") == ""
        clear_topic_buffer()

    def test_projects_isolated(self):
        from memory_mcp.templates.hook_prompts import (
            append_topic,
            clear_topic_buffer,
            get_topic_summary,
        )

        clear_topic_buffer()
        append_topic("a", "cmd_a")
        append_topic("b", "cmd_b")
        assert "cmd_a" in get_topic_summary("a")
        assert "cmd_b" not in get_topic_summary("a")
        clear_topic_buffer()

    def test_summary_dedup(self):
        from memory_mcp.templates.hook_prompts import (
            append_topic,
            clear_topic_buffer,
            get_topic_summary,
        )

        clear_topic_buffer()
        append_topic("proj", "same_cmd")
        append_topic("proj", "same_cmd")
        append_topic("proj", "same_cmd")
        summary = get_topic_summary("proj")
        assert summary.count("same_cmd") == 1
        clear_topic_buffer()


class TestMilestones:
    """Tests for check_milestones() bitmask logic."""

    def test_no_milestone_below_30(self):
        from memory_mcp.templates.hook_prompts import check_milestones

        mask, crossed = check_milestones(25, 0)
        assert mask == 0
        assert crossed == []

    def test_30_milestone(self):
        from memory_mcp.templates.hook_prompts import check_milestones

        mask, crossed = check_milestones(35, 0)
        assert crossed == [30]
        assert mask & 1  # bit 0 set

    def test_50_milestone(self):
        from memory_mcp.templates.hook_prompts import check_milestones

        # Already hit 30, now crossing 50
        mask, crossed = check_milestones(55, 0b001)
        assert crossed == [50]
        assert mask == 0b011

    def test_70_milestone(self):
        from memory_mcp.templates.hook_prompts import check_milestones

        # Already hit 30+50, now crossing 70
        mask, crossed = check_milestones(75, 0b011)
        assert crossed == [70]
        assert mask == 0b111

    def test_multiple_milestones_at_once(self):
        """Jumping from 0 to 60% should cross both 30 and 50."""
        from memory_mcp.templates.hook_prompts import check_milestones

        mask, crossed = check_milestones(60, 0)
        assert 30 in crossed
        assert 50 in crossed
        assert mask == 0b011

    def test_no_double_fire(self):
        """Already crossed milestones should not fire again."""
        from memory_mcp.templates.hook_prompts import check_milestones

        mask, crossed = check_milestones(80, 0b111)
        assert crossed == []
        assert mask == 0b111

    def test_all_at_once(self):
        """Jumping from 0 to 80% should cross all three."""
        from memory_mcp.templates.hook_prompts import check_milestones

        mask, crossed = check_milestones(80, 0)
        assert crossed == [30, 50, 70]
        assert mask == 0b111


class TestFormatMilestoneInjection:
    """Tests for format_milestone_injection()."""

    def test_format_basic(self):
        from memory_mcp.templates.hook_prompts import format_milestone_injection

        matches = [
            {"content": "프로덕션에서 pytest 금지", "metadata": {"importance": 9.5}},
        ]
        output = format_milestone_injection("proj", 50, matches)
        assert "Memory Refresh" in output
        assert "50%" in output
        assert "pytest" in output
        assert "9.5" in output

    def test_format_max_items(self):
        from memory_mcp.templates.hook_prompts import format_milestone_injection

        matches = [
            {"content": f"gotcha {i}", "metadata": {"importance": 9.0}}
            for i in range(3)
        ]
        output = format_milestone_injection("proj", 70, matches)
        assert "1." in output
        assert "2." in output
        assert "3." in output


# ============================================================
# MA-3: Utilization Tracking
# ============================================================


class TestUtilizationStore:
    """Tests for UtilizationStore (SQLite persistence)."""

    @pytest.fixture
    def store(self, tmp_path):
        from memory_mcp.db.utilization import UtilizationStore

        return UtilizationStore(tmp_path / "test_util.db")

    def test_record_and_stats_empty(self, store):
        stats = store.get_stats("proj")
        assert stats["lifetime"]["total"] == 0
        assert stats["lifetime"]["rate"] is None

    def test_record_injection(self, store):
        store.record_injection("proj", ["m1", "m2"], "pre_tool", "docker exec pytest")
        # Unresolved — should not count in stats
        stats = store.get_stats("proj")
        assert stats["lifetime"]["total"] == 0

    def test_resolve_utilized(self, store):
        store.record_injection("proj", ["m1"], "pre_tool", "docker exec pytest")
        updated = store.resolve_event("proj", "m1", utilized=True)
        assert updated == 1
        stats = store.get_stats("proj")
        assert stats["lifetime"]["success"] == 1
        assert stats["lifetime"]["failure"] == 0
        assert stats["lifetime"]["rate"] == 1.0

    def test_resolve_not_utilized(self, store):
        store.record_injection("proj", ["m1"], "pre_tool", "docker exec pytest")
        store.resolve_event("proj", "m1", utilized=False)
        stats = store.get_stats("proj")
        assert stats["lifetime"]["success"] == 0
        assert stats["lifetime"]["failure"] == 1
        assert stats["lifetime"]["rate"] == 0.0

    def test_mixed_results(self, store):
        for i in range(3):
            store.record_injection("proj", [f"m{i}"], "pre_tool", f"cmd_{i}")
        store.resolve_event("proj", "m0", utilized=True)
        store.resolve_event("proj", "m1", utilized=True)
        store.resolve_event("proj", "m2", utilized=False)
        stats = store.get_stats("proj")
        assert stats["lifetime"]["success"] == 2
        assert stats["lifetime"]["failure"] == 1
        assert abs(stats["lifetime"]["rate"] - 0.667) < 0.01

    def test_worst_gotchas(self, store):
        for i in range(5):
            store.record_injection("proj", ["bad_gotcha"], "pre_tool", f"cmd_{i}")
            store.resolve_event("proj", "bad_gotcha", utilized=False)
        store.record_injection("proj", ["good_gotcha"], "pre_tool", "cmd_ok")
        store.resolve_event("proj", "good_gotcha", utilized=True)

        stats = store.get_stats("proj")
        assert len(stats["worst_gotchas"]) >= 1
        assert stats["worst_gotchas"][0]["memory_id"] == "bad_gotcha"
        assert stats["worst_gotchas"][0]["failure_count"] == 5

    def test_projects_isolated(self, store):
        store.record_injection("a", ["m1"], "pre_tool", "cmd")
        store.resolve_event("a", "m1", utilized=True)
        store.record_injection("b", ["m2"], "pre_tool", "cmd")
        store.resolve_event("b", "m2", utilized=False)

        stats_a = store.get_stats("a")
        stats_b = store.get_stats("b")
        assert stats_a["lifetime"]["success"] == 1
        assert stats_a["lifetime"]["failure"] == 0
        assert stats_b["lifetime"]["success"] == 0
        assert stats_b["lifetime"]["failure"] == 1

    def test_daily_breakdown(self, store):
        store.record_injection("proj", ["m1"], "pre_tool", "cmd")
        store.resolve_event("proj", "m1", utilized=True)
        stats = store.get_stats("proj", days=7)
        assert len(stats["daily"]) >= 1
        assert stats["daily"][0]["success"] >= 1


class TestInjectionTracking:
    """Tests for track_injection() + check_injection_utilization()."""

    def test_track_creates_event(self):
        from memory_mcp.templates.hook_prompts import (
            _injection_registry,
            _injection_lock,
            track_injection,
        )

        # Clear
        with _injection_lock:
            _injection_registry.clear()

        track_injection("proj", ["m1"], "pre_tool", "docker exec test")
        with _injection_lock:
            assert len(_injection_registry["proj"]) == 1

    def test_check_violation(self):
        from memory_mcp.templates.hook_prompts import (
            _injection_registry,
            _injection_lock,
            check_injection_utilization,
            track_injection,
        )

        with _injection_lock:
            _injection_registry.clear()

        track_injection("proj", ["m1"], "pre_tool", "docker exec memory-mcp-server pytest")
        # Same command fails
        check_injection_utilization("proj", "docker exec memory-mcp-server pytest tests/", exit_code=1)

        with _injection_lock:
            events = list(_injection_registry["proj"])
        resolved = [e for e in events if e.resolved]
        assert len(resolved) >= 1
        assert resolved[0].utilized is False


class TestExistingDangerUnchanged:
    """Verify that classify_danger still works exactly as before."""

    def test_destructive_detected(self):
        from memory_mcp.templates.hook_prompts import classify_danger

        assert classify_danger("rm -rf /tmp/data") == "destructive"
        assert classify_danger("git push --force") == "destructive"

    def test_restart_detected(self):
        from memory_mcp.templates.hook_prompts import classify_danger

        assert classify_danger("docker restart my-container") == "restart"
        assert classify_danger("systemctl restart nginx") == "restart"

    def test_deploy_detected(self):
        from memory_mcp.templates.hook_prompts import classify_danger

        assert classify_danger("kubectl apply -f deploy.yaml") == "deploy"

    def test_safe_command_none(self):
        from memory_mcp.templates.hook_prompts import classify_danger

        assert classify_danger("ls -la") is None
        assert classify_danger("cat README.md") is None
        assert classify_danger("pytest tests/ -v") is None
