"""Pydantic input models for MCP tools."""

import json as _json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory_mcp.constants import (
    CONTEXT_SEARCH_DEFAULT_N,
    CONTEXT_SEARCH_MAX_N,
    DEFAULT_N_RESULTS,
    GLOBAL_PROJECT_NAME,
    IMPORTANCE_DEFAULT,
    IMPORTANCE_MAX,
    IMPORTANCE_MIN,
    MAX_N_RESULTS,
    PRIORITY_TO_IMPORTANCE,
    MemoryPriority,
    MemoryType,
)


class _FlexibleInput(BaseModel):
    """Base class that accepts JSON string input and auto-parses it.

    Some MCP clients (e.g. Claude Desktop) occasionally serialize the entire
    params object as a JSON string instead of a dict.  This validator
    transparently handles both forms.
    """

    @model_validator(mode="before")
    @classmethod
    def _parse_json_string(cls, data):  # noqa: ANN001
        if isinstance(data, str):
            try:
                parsed = _json.loads(data)
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    return data
            except (_json.JSONDecodeError, ValueError):
                return data

        # Auto-fix: comma-separated tags string → list
        if isinstance(data, dict) and "tags" in data:
            tags = data["tags"]
            if isinstance(tags, str):
                data["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

        return data


class MemoryStoreInput(_FlexibleInput):
    """Input for storing a memory."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier (e.g., 'bms_modem_900mhz', 'rf_simulation')",
        min_length=1,
        max_length=100,
    )
    content: str = Field(
        ...,
        description="Content to remember. Be specific and self-contained.",
        min_length=1,
        max_length=5000,
    )
    memory_type: MemoryType = Field(
        default=MemoryType.FACT,
        description=(
            "Memory type: 'fact' (permanent facts), 'decision' (design decisions), "
            "'summary' (session summaries), 'snippet' (code patterns)"
        ),
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional tags for filtering (e.g., ['vhdl', 'fpga', 'zynq'])",
        max_length=10,
    )
    importance: float = Field(
        default=IMPORTANCE_DEFAULT,
        description=(
            "Memory importance (1.0-10.0): "
            "9-10=critical (always loaded), 4-8=normal, 1-3=low/auto-saved. "
            "Server rules may adjust this based on content patterns."
        ),
        ge=IMPORTANCE_MIN,
        le=IMPORTANCE_MAX,
    )
    priority: MemoryPriority | None = Field(
        default=None,
        description=(
            "DEPRECATED: Use importance (1.0-10.0) instead. "
            "If provided, overrides importance with conversion: "
            "critical=9.0, normal=5.0, low=2.0"
        ),
    )
    linked_projects: list[str] | None = Field(
        default=None,
        description=(
            "Other projects this memory is also relevant to. "
            "These projects will see this memory in their auto_recall. "
            "Example: a JWT decision in 'auth-service' linked to ['api-gateway']."
        ),
        max_length=20,
    )
    force_store: bool = Field(
        default=False,
        description=(
            "If True, store the memory even if a near-duplicate exists. "
            "Use this after reviewing the duplicate warning and deciding "
            "to store anyway."
        ),
    )
    is_global: bool = Field(
        default=False,
        description=(
            "If True, store this memory in the global shared space. "
            "Global memories are automatically loaded for ALL projects "
            "during auto_recall. Use for personal preferences, coding style, "
            "cross-project conventions, etc."
        ),
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional session identifier for multi-session tracking. "
            "When multiple sessions work on the same project concurrently, "
            "this helps identify which session created each memory."
        ),
        max_length=100,
    )

    @model_validator(mode="after")
    def _resolve_priority_to_importance(self) -> "MemoryStoreInput":
        """Convert legacy priority field to importance if provided."""
        if self.priority is not None:
            self.importance = PRIORITY_TO_IMPORTANCE[self.priority.value]
        return self

    @model_validator(mode="after")
    def _resolve_global_project(self) -> "MemoryStoreInput":
        """Route to _global project when is_global=True."""
        if self.is_global:
            self.project = GLOBAL_PROJECT_NAME
        return self


class MemorySearchInput(_FlexibleInput):
    """Input for searching memories."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Semantic search query describing what you're looking for",
        min_length=1,
        max_length=500,
    )
    project: str | None = Field(
        default=None,
        description="Project to search in. Required unless cross_project=True.",
    )
    memory_type: MemoryType | None = Field(
        default=None,
        description="Filter by memory type (optional)",
    )
    n_results: int = Field(
        default=DEFAULT_N_RESULTS,
        description=f"Max results to return (1-{MAX_N_RESULTS})",
        ge=1,
        le=MAX_N_RESULTS,
    )
    cross_project: bool = Field(
        default=False,
        description="If True, search across ALL projects",
    )
    source_project: str | None = Field(
        default=None,
        description=(
            "Search in a specific different project instead of `project`. "
            "Useful for referencing memories from another project. "
            "Ignored when cross_project=True."
        ),
        max_length=100,
    )
    # ── Phase 8A: RAG 검색 확장 필드 ──
    tags: list[str] | None = Field(
        default=None,
        description="Filter by tags (OR matching — returns memories with ANY of these tags)",
    )
    priority: MemoryPriority | None = Field(
        default=None,
        description="Filter by priority level",
    )
    # ── Phase 9: Importance filters ──
    importance_min: float | None = Field(
        default=None,
        description="Filter: minimum importance score (inclusive)",
        ge=IMPORTANCE_MIN,
        le=IMPORTANCE_MAX,
    )
    importance_max: float | None = Field(
        default=None,
        description="Filter: maximum importance score (inclusive)",
        ge=IMPORTANCE_MIN,
        le=IMPORTANCE_MAX,
    )
    date_after: str | None = Field(
        default=None,
        description="Only return memories created after this ISO date (e.g. '2026-02-22')",
    )
    date_before: str | None = Field(
        default=None,
        description="Only return memories created before this ISO date (e.g. '2026-02-22')",
    )
    use_mmr: bool = Field(
        default=False,
        description="Use MMR (Maximum Marginal Relevance) for diverse results",
    )
    time_weighted: bool = Field(
        default=False,
        description="Apply time-decay weighting (recent memories score higher)",
    )
    # ── Phase 8B: Hybrid Search ──
    use_hybrid: bool = Field(
        default=False,
        description="Use hybrid search (semantic + BM25 keyword matching via RRF fusion)",
    )
    # ── H-2.1: Dynamic Weighted RRF ──
    dynamic_rrf: bool = Field(
        default=False,
        description=(
            "When True with use_hybrid, dynamically adjust BM25/semantic weights "
            "based on query specificity. Specific queries favor BM25, "
            "abstract queries favor semantic search."
        ),
    )
    # ── H-2.4: Adaptive MMR Lambda ──
    mmr_lambda: float | None = Field(
        default=None,
        description=(
            "MMR relevance/diversity trade-off (0.0=full diversity, 1.0=full relevance). "
            "Only used when use_mmr=True. Default (None) uses 0.7. "
            "Higher values (0.9) prioritize relevance for targeted search. "
            "Lower values (0.5) increase diversity for broad exploration."
        ),
        ge=0.0,
        le=1.0,
    )

    @model_validator(mode="after")
    def _resolve_priority_filter(self) -> "MemorySearchInput":
        """Convert legacy priority filter to importance range if needed."""
        if (
            self.priority is not None
            and self.importance_min is None
            and self.importance_max is None
        ):
            from memory_mcp.constants import (
                IMPORTANCE_CRITICAL_THRESHOLD,
                IMPORTANCE_LOW_THRESHOLD,
            )

            if self.priority == MemoryPriority.CRITICAL:
                self.importance_min = IMPORTANCE_CRITICAL_THRESHOLD
            elif self.priority == MemoryPriority.LOW:
                self.importance_max = IMPORTANCE_LOW_THRESHOLD
            else:  # NORMAL
                self.importance_min = IMPORTANCE_LOW_THRESHOLD
                self.importance_max = IMPORTANCE_CRITICAL_THRESHOLD
        return self


class MemoryDeleteInput(_FlexibleInput):
    """Input for deleting a memory."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(..., description="Project identifier", min_length=1)
    memory_id: str = Field(..., description="Memory ID to delete", min_length=1)


class MemoryUpdateInput(_FlexibleInput):
    """Input for updating an existing memory."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    memory_id: str = Field(
        ...,
        description="ID of the memory to update",
        min_length=1,
    )
    content: str | None = Field(
        default=None,
        description=(
            "New content. If provided, the embedding will be automatically "
            "re-computed and importance rules re-evaluated. Max 5000 chars."
        ),
        min_length=1,
        max_length=5000,
    )
    memory_type: MemoryType | None = Field(
        default=None,
        description="New memory type (fact, decision, summary, snippet)",
    )
    importance: float | None = Field(
        default=None,
        description="New importance score (1.0-10.0)",
        ge=IMPORTANCE_MIN,
        le=IMPORTANCE_MAX,
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "New tags list (replaces existing tags entirely). "
            "Pass [] to clear all tags. None means no change."
        ),
        max_length=10,
    )
    linked_projects: list[str] | None = Field(
        default=None,
        description=(
            "Update linked projects list (replaces existing links entirely). "
            "Pass [] to clear all links. None means no change."
        ),
        max_length=20,
    )

    @model_validator(mode="after")
    def _at_least_one_update_field(self) -> "MemoryUpdateInput":
        """Ensure at least one updatable field is provided."""
        if (
            self.content is None
            and self.memory_type is None
            and self.importance is None
            and self.tags is None
            and self.linked_projects is None
        ):
            raise ValueError(
                "At least one of content, memory_type, importance, tags, "
                "or linked_projects must be provided for update."
            )
        return self


class SessionSummarizeInput(_FlexibleInput):
    """Input for summarizing and storing a session."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(..., description="Project identifier", min_length=1)
    summary: str = Field(
        ...,
        description=(
            "Session summary: what was worked on, key decisions made, "
            "problems encountered, and next steps."
        ),
        min_length=10,
        max_length=10000,
    )
    tags: list[str] | str = Field(
        default_factory=list,
        description="Tags for this session",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional session identifier for tracking which session "
            "created this summary."
        ),
        max_length=100,
    )


class EnvironmentInfo(_FlexibleInput):
    """Client environment information for session continuity checking."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    cwd: str | None = Field(
        default=None,
        description="Current working directory of the client.",
        max_length=500,
    )
    hostname: str | None = Field(
        default=None,
        description="Hostname of the machine running the client.",
        max_length=200,
    )


class AutoRecallInput(_FlexibleInput):
    """Input for automatically recalling relevant memories for a session."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier to recall memories from",
        min_length=1,
        max_length=100,
    )
    context: str = Field(
        default="",
        description=(
            "Optional context about the current task or topic. "
            "Used to find the most relevant memories."
        ),
        max_length=2000,
    )
    n_results: int = Field(
        default=10,
        description="Max number of memories to recall (1-50)",
        ge=1,
        le=50,
    )
    include_recent_summaries: bool = Field(
        default=True,
        description="If True, also include the most recent session summaries",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Current session identifier. When provided, auto_recall will "
            "include a section showing recent changes from OTHER sessions, "
            "helping detect concurrent modifications."
        ),
        max_length=100,
    )
    environment: EnvironmentInfo | None = Field(
        default=None,
        description=(
            "Client environment info for session continuity checking. "
            "When provided, the server compares with previous sessions "
            "and generates warnings for significant changes."
        ),
    )
    recall_source: str | None = Field(
        default=None,
        description=(
            "Source of this recall request. Use 'compact' after context "
            "compaction to prioritize recent session summaries and "
            "workflow memories for faster context recovery."
        ),
        max_length=20,
    )
    mode: str | None = Field(
        default=None,
        description=(
            "Recall mode: 'brief' returns a compact project overview (~100-200 tokens) "
            "with only critical memories — ideal for session start. "
            "'full' uses the legacy multi-step recall with all memory categories. "
            "Default (None) auto-selects: 'brief' for fresh sessions, "
            "'full' when recall_source='compact'."
        ),
        max_length=10,
    )


class ContextSearchInput(_FlexibleInput):
    """Input for compact mid-conversation memory search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Semantic search query describing what you're looking for",
        min_length=1,
        max_length=500,
    )
    project: str | None = Field(
        default=None,
        description="Project to search in. Required unless cross_project=True.",
    )
    n_results: int = Field(
        default=CONTEXT_SEARCH_DEFAULT_N,
        description=f"Max results to return (1-{CONTEXT_SEARCH_MAX_N})",
        ge=1,
        le=CONTEXT_SEARCH_MAX_N,
    )
    cross_project: bool = Field(
        default=False,
        description="If True, search across ALL projects",
    )
    include_content: bool = Field(
        default=True,
        description=(
            "If True, include truncated content in each result. "
            "If False, returns only type + importance + date (ultra-compact)."
        ),
    )


class ProjectStatsInput(_FlexibleInput):
    """Input for getting project stats."""

    model_config = ConfigDict(extra="forbid")

    project: str | None = Field(
        default=None,
        description="Project name. If omitted, returns global stats for all projects.",
    )


class ProjectRenameInput(_FlexibleInput):
    """Input for renaming a project."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    old_name: str = Field(
        ...,
        description="Current project name to rename",
        min_length=1,
        max_length=100,
    )
    new_name: str = Field(
        ...,
        description="New project name",
        min_length=1,
        max_length=100,
    )


class ProjectDeleteInput(_FlexibleInput):
    """Input for deleting an entire project and all its memories."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project name to delete. All memories will be permanently removed.",
        min_length=1,
        max_length=100,
    )
    confirm: bool = Field(
        default=False,
        description="Must be True to confirm deletion. Prevents accidental data loss.",
    )


class InboxInput(_FlexibleInput):
    """Input for viewing and managing unreviewed memories (inbox)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    n_results: int = Field(
        default=20,
        description="Max number of unreviewed memories to return (1-2500)",
        ge=1,
        le=2500,
    )
    mark_reviewed: bool = Field(
        default=False,
        description=(
            "If True, mark all returned memories as reviewed "
            "(removes 'unreviewed' tag). Use after reviewing the list."
        ),
    )


class ReportFailureInput(_FlexibleInput):
    """Input for reporting a tool/command failure to the circuit breaker."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    tool_name: str = Field(
        ...,
        description="Name of the failing tool, command, or API (e.g., 'docker exec', 'npm install', 'Notion API')",
        min_length=1,
        max_length=200,
    )
    error_summary: str = Field(
        ...,
        description="Brief description of the error (e.g., 'permission denied', 'connection refused')",
        min_length=1,
        max_length=500,
    )
    attempt: int = Field(
        ...,
        description="Which attempt number this is (1=first failure, 2=second, etc.)",
        ge=1,
        le=20,
    )
    file_path: str | None = Field(
        default=None,
        description="File being edited when failure occurred (for circular debug detection)",
        max_length=500,
    )


class GuideInput(_FlexibleInput):
    """Input for getting the CLAUDE.md guide template."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project_id: str = Field(
        ...,
        description="Project identifier to embed in the guide (e.g., 'my_project')",
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    current_version: int | None = Field(
        default=None,
        description="Current guide version in local CLAUDE.md (from <!-- KANDELA-GUIDE-START vN --> marker). Server compares and returns NEEDS_UPDATE.",
        ge=1,
    )


class CommandPromptInput(_FlexibleInput):
    """Input for getting a slash command prompt from the server."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    command: str = Field(
        ...,
        description="Slash command name (e.g., 'dm.init', 'dm.sync', 'init', 'sync')",
        min_length=1,
        max_length=50,
    )
    arguments: str = Field(
        default="",
        description="User arguments passed to the command (e.g., project_id)",
        max_length=500,
    )
    project: str = Field(
        default="",
        description="Current project ID (auto-detected from CLAUDE.md context)",
        max_length=100,
    )


class ConfirmChangeInput(_FlexibleInput):
    """Input for confirming a change that may conflict with a previous decision."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    proposed_change: str = Field(
        default="",
        description=(
            "Description of the proposed change. "
            "Empty when action='keep' (user chose to keep the existing decision)."
        ),
        max_length=1000,
    )
    related_memory_id: str = Field(
        default="",
        description="ID of the related memory (previous decision) that may conflict",
        max_length=100,
    )
    action: str = Field(
        default="change",
        description=(
            "'change' — user confirmed the change (default). "
            "'keep' — user chose to keep the existing decision; gate is cleared without updating memory."
        ),
        max_length=10,
    )


class InfraUpdateInput(_FlexibleInput):
    """Input for updating the project infra/test setup document."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    content: str = Field(
        ...,
        description=(
            "Project infrastructure and test setup. Free-form text. Recommended format:\n"
            "## 테스트 실행\n"
            "- 위치: ...\n"
            "- 명령어: ...\n"
            "## 벤치마크\n"
            "- 위치: ...\n"
            "- 명령어: ...\n"
            "## 주요 컨테이너/서버\n"
            "- ...\n"
            "## 주의사항\n"
            "- ..."
        ),
        min_length=1,
        max_length=5000,
    )


class InfraGetInput(_FlexibleInput):
    """Input for retrieving the project infra/test setup document."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )


class ProgressUpdateInput(_FlexibleInput):
    """Input for updating the project progress document."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    content: str = Field(
        ...,
        description=(
            "Current project progress. Free-form text. Recommended format:\n"
            "## 현재 Phase: Phase X\n"
            "## 진행 중: ...\n"
            "## 완료: ...\n"
            "## 다음: ..."
        ),
        min_length=1,
        max_length=5000,
    )


class ProgressGetInput(_FlexibleInput):
    """Input for retrieving the project progress document."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )


class ChecklistAddInput(_FlexibleInput):
    """Input for adding an item to a named checklist."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    name: str = Field(
        ...,
        description="Checklist name (e.g., '배포 전 확인', 'sprint-1')",
        min_length=1,
        max_length=100,
    )
    item: str = Field(
        ...,
        description="Item to add to the checklist",
        min_length=1,
        max_length=500,
    )


class ChecklistGetInput(_FlexibleInput):
    """Input for retrieving a named checklist."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    name: str = Field(
        ...,
        description="Checklist name to retrieve",
        min_length=1,
        max_length=100,
    )


class ChecklistDoneInput(_FlexibleInput):
    """Input for marking a checklist item as done or undone."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        description="Project identifier",
        min_length=1,
        max_length=100,
    )
    name: str = Field(
        ...,
        description="Checklist name",
        min_length=1,
        max_length=100,
    )
    item_index: int = Field(
        ...,
        description="1-based index of the item to toggle (done ↔ undone)",
        ge=1,
        le=500,
    )
    done: bool = Field(
        default=True,
        description="True to mark as done [x], False to mark as undone [ ]",
    )


# ── Trash / Archive ────────────────────────────────────────────────


class MemoryRestoreInput(_FlexibleInput):
    """Input for restoring a memory or project from trash."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(..., description="Project name", min_length=1)
    memory_id: str | None = Field(
        None,
        description="Memory ID to restore. If omitted, restores entire project.",
    )


class TrashListInput(_FlexibleInput):
    """Input for listing trash contents."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str | None = Field(None, description="Filter by project")
    limit: int = Field(20, ge=1, le=100, description="Max results")


class TrashPurgeInput(_FlexibleInput):
    """Input for permanently deleting from trash."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str | None = Field(None, description="Filter by project")
    memory_id: str | None = Field(None, description="Specific memory to purge")

