"""Tests for the CLAUDE.md guide template (Phase 6, updated for v4)."""

import re

import pytest

from memory_mcp.templates.claude_md_guide import GUIDE_VERSION, get_guide


class TestGuideVersion:
    """Test guide version metadata."""

    def test_version_is_positive_int(self) -> None:
        """버전 번호는 양의 정수여야 한다."""
        assert isinstance(GUIDE_VERSION, int)
        assert GUIDE_VERSION >= 1

    def test_get_guide_returns_version(self) -> None:
        """get_guide()가 version을 반환해야 한다."""
        result = get_guide("test_proj")
        assert result["version"] == GUIDE_VERSION

    def test_version_is_19(self) -> None:
        """가이드 버전이 19이어야 한다 (v19: 클라이언트 규칙 제거, 서버사이드로 이동)."""
        assert GUIDE_VERSION == 19


class TestGuideContent:
    """Test guide template content and substitution."""

    def test_substitutes_project_id(self) -> None:
        """project_id가 올바르게 치환되어야 한다."""
        result = get_guide("my_cool_project")
        content = result["content"]
        assert "memory project ID: my_cool_project" in content

    def test_no_unresolved_template_vars(self) -> None:
        """치환 후 $project_id, $version 같은 미해결 변수가 없어야 한다."""
        result = get_guide("test_proj")
        content = result["content"]
        assert "$project_id" not in content
        assert "$version" not in content

    def test_different_project_ids_produce_different_content(self) -> None:
        """다른 project_id는 다른 content를 생성해야 한다."""
        r1 = get_guide("alpha")
        r2 = get_guide("beta")
        assert r1["content"] != r2["content"]
        assert "memory project ID: alpha" in r1["content"]
        assert "memory project ID: beta" in r2["content"]


class TestGuideMarkers:
    """Test version markers for safe section replacement."""

    def test_has_start_marker(self) -> None:
        """START 마커가 포함되어야 한다."""
        result = get_guide("test_proj")
        content = result["content"]
        expected = f"<!-- KANDELA-GUIDE-START v{GUIDE_VERSION} -->"
        assert content.startswith(expected)

    def test_has_end_marker(self) -> None:
        """END 마커가 포함되어야 한다."""
        result = get_guide("test_proj")
        content = result["content"]
        assert content.rstrip().endswith("<!-- KANDELA-GUIDE-END -->")

    def test_markers_are_on_separate_lines(self) -> None:
        """마커가 별도의 줄에 위치해야 한다."""
        result = get_guide("test_proj")
        lines = result["content"].strip().split("\n")
        assert lines[0].startswith("<!-- KANDELA-GUIDE-START")
        assert lines[-1].strip() == "<!-- KANDELA-GUIDE-END -->"

    def test_version_in_marker_matches_constant(self) -> None:
        """마커 내 버전 번호가 GUIDE_VERSION과 일치해야 한다."""
        result = get_guide("test_proj")
        match = re.search(r"KANDELA-GUIDE-START v(\d+)", result["content"])
        assert match is not None
        assert int(match.group(1)) == GUIDE_VERSION


class TestGuideRequiredSections:
    """Test that the compact guide contains essential sections."""

    @pytest.fixture()
    def guide_content(self) -> str:
        return get_guide("test_proj")["content"]

    def test_has_memory_project_id(self, guide_content: str) -> None:
        """Memory project ID가 있어야 한다."""
        assert "## Kandela" in guide_content
        assert "memory project ID:" in guide_content

    def test_has_auto_recall_call(self, guide_content: str) -> None:
        """v19 컴팩트 가이드: auto_recall 호출 방법이 있어야 한다."""
        assert "auto_recall" in guide_content
        assert "mode='brief'" in guide_content

    def test_has_context_search_call(self, guide_content: str) -> None:
        """v19 컴팩트 가이드: context_search on-demand 검색 방법이 있어야 한다."""
        assert "context_search" in guide_content

    def test_has_guide_command_reference(self, guide_content: str) -> None:
        """v19 컴팩트 가이드: 전체 가이드 참조 방법이 있어야 한다."""
        assert "get_command_prompt" in guide_content

    def test_detailed_rules_moved_to_server(self, guide_content: str) -> None:
        """v19: 상세 규칙은 클라이언트(CLAUDE.md)에 없고 서버사이드로 이동됨."""
        assert "### 저장 원칙" not in guide_content
        assert "### Importance" not in guide_content
        assert "### 활용 규칙" not in guide_content
        assert "### 자동 동작" not in guide_content

    def test_no_reference_pointer_in_guide(self, guide_content: str) -> None:
        """CLAUDE.md 가이드에 .kandela-guide.md 참조가 없어야 한다 (v10: 자동로드 방지)."""
        assert ".kandela-guide.md" not in guide_content

    def test_compact_size(self, guide_content: str) -> None:
        """간결한 가이드는 v3보다 현저히 짧아야 한다."""
        # v3 was ~4500+ chars, v4 compact should be much shorter
        # v18 added Rule 12 (리소스 확인), ~1900 chars
        assert len(guide_content) < 2000


class TestGuideHookCompatibility:
    """Test that the guide is compatible with existing hooks."""

    def test_project_id_line_format_for_grep(self) -> None:
        """hooks의 grep -m1 패턴 'memory project ID:'에 첫 번째 매칭이 실제 ID여야 한다."""
        result = get_guide("my_test_project")
        content = result["content"]
        lines = content.split("\n")
        matched = [
            line for line in lines
            if "memory project ID:" in line
        ]
        assert len(matched) >= 1
        assert "my_test_project" in matched[0]

    def test_project_id_extractable_by_sed_pattern(self) -> None:
        """hooks의 sed 패턴으로 project_id를 추출할 수 있어야 한다."""
        result = get_guide("alpha_beta-123")
        content = result["content"]
        lines = content.split("\n")
        id_line = next(l for l in lines if "memory project ID:" in l)
        match = re.search(r"memory project ID:\s*([a-zA-Z0-9_-]+)", id_line)
        assert match is not None
        assert match.group(1) == "alpha_beta-123"


# ── Reference file tests ─────────────────────────────────────────


class TestReferenceContent:
    """Test the reference file template (.kandela-guide.md)."""

    @pytest.fixture()
    def ref_content(self) -> str:
        return get_guide("test_proj")["reference_content"]

    def test_get_guide_returns_reference_content(self) -> None:
        """get_guide()가 reference_content를 반환해야 한다."""
        result = get_guide("test_proj")
        assert "reference_content" in result
        assert isinstance(result["reference_content"], str)
        assert len(result["reference_content"]) > 0

    def test_has_reference_marker(self, ref_content: str) -> None:
        """레퍼런스 파일에 버전 마커가 있어야 한다."""
        expected = f"<!-- KANDELA-REFERENCE v{GUIDE_VERSION} -->"
        assert expected in ref_content

    def test_has_auto_recall_stages(self, ref_content: str) -> None:
        """Auto-Recall 단계가 설명되어야 한다."""
        assert "Auto-Recall" in ref_content
        assert "importance >= 9.0" in ref_content
        assert "importance >= 3.0" in ref_content
        assert "importance < 3.0" in ref_content

    def test_has_search_options(self, ref_content: str) -> None:
        """검색 고급 옵션 테이블이 있어야 한다."""
        assert "search" in ref_content
        assert "use_hybrid" in ref_content
        assert "use_mmr" in ref_content
        assert "time_weighted" in ref_content
        assert "importance_min" in ref_content
        assert "importance_max" in ref_content
        assert "date_after" in ref_content
        assert "date_before" in ref_content

    def test_has_importance_rules(self, ref_content: str) -> None:
        """서버 Importance 보정 규칙이 있어야 한다."""
        assert "보정 규칙" in ref_content
        assert "SSH" in ref_content
        assert "Docker" in ref_content
        assert "+2.0" in ref_content
        assert "-3.0" in ref_content

    def test_no_hooks_detail(self, ref_content: str) -> None:
        """Hooks 설정 상세가 제거되어야 한다 (v17: 내부 비공개)."""
        assert "Hooks 설정" not in ref_content
        assert "memory-session-start.sh" not in ref_content
        assert "memory-pre-compact.sh" not in ref_content

    def test_has_tool_list(self, ref_content: str) -> None:
        """MCP 도구 목록이 있어야 한다."""
        assert "MCP 도구" in ref_content
        assert "23개" in ref_content
        assert "store" in ref_content
        assert "search" in ref_content
        assert "auto_recall" in ref_content
        assert "get_guide" in ref_content
        assert "inbox" in ref_content

    def test_has_slash_commands(self, ref_content: str) -> None:
        """슬래시 명령이 있어야 한다 (kd-* 포맷)."""
        assert "슬래시 명령" in ref_content
        assert "/kd-init" in ref_content
        assert "/kd-update" in ref_content
        assert "/kd-list" in ref_content
        assert "/kd-inbox" in ref_content
        assert "/kd-status" in ref_content
        assert "/kd-task" in ref_content
        assert "/kd-worker" in ref_content

    def test_has_mcp_connection(self, ref_content: str) -> None:
        """MCP 연결 방법이 있어야 한다."""
        assert "클라이언트 MCP 연결" in ref_content
        assert "claude mcp add" in ref_content

    def test_has_legacy_compat(self, ref_content: str) -> None:
        """레거시 호환 설명이 있어야 한다."""
        assert "레거시" in ref_content
        assert "priority" in ref_content

    def test_has_usage_tracking(self, ref_content: str) -> None:
        """사용 빈도 반영 설명이 있어야 한다."""
        assert "사용 빈도" in ref_content

    def test_no_unresolved_vars(self, ref_content: str) -> None:
        """미해결 template 변수가 없어야 한다."""
        assert "$version" not in ref_content

    def test_command_count_is_14(self, ref_content: str) -> None:
        """슬래시 명령이 14개여야 한다 (kd-* 포맷)."""
        kd_mentions = re.findall(r"/kd-[\w-]+", ref_content)
        unique_cmds = set(kd_mentions)
        assert len(unique_cmds) == 14
