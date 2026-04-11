"""MCP server definition with all memory tools."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator

from pydantic import BeforeValidator

from mcp.server.fastmcp import Context, FastMCP

from memory_mcp.constants import (
    DEFAULT_DB_PATH,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MMR_LAMBDA_RECALL,
    GLOBAL_PROJECT_NAME,
    IMPORTANCE_CRITICAL_THRESHOLD,
    IMPORTANCE_LOW_THRESHOLD,
    MemoryPriority,
    MemoryType,
)
from memory_mcp.db.session_env import SessionEnvironmentStore
from memory_mcp.db.store import MemoryStore
from memory_mcp.tools.models import (
    AutoRecallInput,
    ChecklistAddInput,
    ChecklistDoneInput,
    ChecklistGetInput,
    CommandPromptInput,
    ConfirmChangeInput,
    ContextSearchInput,
    GuideInput,
    InboxInput,
    MemoryDeleteInput,
    MemorySearchInput,
    MemoryStoreInput,
    MemoryUpdateInput,
    ProjectDeleteInput,
    ProjectRenameInput,
    ProjectStatsInput,
    InfraGetInput,
    InfraUpdateInput,
    ProgressGetInput,
    ProgressUpdateInput,
    ReportFailureInput,
    SessionSummarizeInput,
    MemoryRestoreInput,
    TrashListInput,
    TrashPurgeInput,
)
from memory_mcp.dashboard import register_dashboard_routes
from memory_mcp.install import register_install_routes
from memory_mcp.utils.formatting import format_search_results, format_stats
from memory_mcp.utils.schema import dereference_tool_schemas

logger = logging.getLogger(__name__)


def _parse_json_str(v: Any) -> Any:
    """BeforeValidator: iteratively unwrap JSON string → dict (up to 3 levels).

    PRIMARY defense against Claude's intermittent JSON double/triple
    serialization. FastMCP's pre_parse_json skips string→string results,
    so this BeforeValidator is the only reliable unwrapper.

    Handles: dict, json.dumps(dict), json.dumps(json.dumps(dict)),
    json.dumps(json.dumps(json.dumps(dict))),
    and Claude's occasional trailing-brace bug (e.g. '{"a":1}}').
    """
    if not isinstance(v, str):
        return v
    current = v.strip()
    for depth in range(3):
        try:
            parsed = _json.loads(current)
            if isinstance(parsed, dict):
                return parsed
            elif isinstance(parsed, str):
                current = parsed  # unwrap one level, retry
            else:
                return v  # list/number/bool — not a model dict, return original
        except (_json.JSONDecodeError, ValueError):
            # Claude Code occasionally appends extra '}' — try stripping one
            if current.endswith('}') and current.count('{') < current.count('}'):
                current = current[:-1]
                continue
            if depth == 0:
                logger.warning(
                    "JSON parse failed for tool param (len=%d): %.100s",
                    len(v), v,
                )
            return v
    return v




# ── Auto fact extraction from summaries ──────────────────────

# Patterns that indicate a design decision or important fact
_FACT_PATTERNS = [
    # "chose X over Y", "decided to use X", "using X instead of Y"
    re.compile(r"(?:chose|selected|picked|decided to use|using)\s+(.{10,80}?)(?:\s+(?:over|instead of|rather than)\s+.{5,60})?[.,;]", re.IGNORECASE),
    # "X pattern/approach/strategy/architecture"
    re.compile(r"(?:adopted|implemented|applied|followed)\s+(?:the\s+)?(.{10,80}?)(?:\s+(?:pattern|approach|strategy|architecture))", re.IGNORECASE),
    # "key decision: ..." or "decided: ..."
    re.compile(r"(?:key decision|decided|decision):\s*(.{10,120}?)(?:\.|$)", re.IGNORECASE),
    # "fixed ... by ..." or "resolved ... with ..."
    re.compile(r"(?:fixed|resolved|solved)\s+(.{10,80}?)\s+(?:by|with|using)\s+(.{10,80}?)(?:\.|$)", re.IGNORECASE),
    # "error/issue/bug/problem with X → solved by Y"
    re.compile(r"(?:error|issue|bug|problem)\s+(?:with\s+)?(.{5,60}?)(?:\s*[→—-]+\s*(?:solved|fixed|resolved)\s+(?:by|with)\s+(.{10,80}))?(?:\.|$)", re.IGNORECASE),
]

# Sentence-level keywords that suggest a fact worth extracting
_FACT_KEYWORDS = {
    "cursor", "pagination", "offset", "optimistic", "locking", "version field",
    "pool_size", "event-driven", "event naming", "naming convention",
    "protocol", "abc", "middleware", "visitor", "asyncio", "threading",
    "lru_cache", "redis", "typer", "click", "toml", "entry point",
    "plugin", "jwt", "auth", "bearer", "docker", "compose",
}


def _extract_facts_from_summary(summary: str) -> list[tuple[str, float, list[str]]]:
    """Extract key facts from a session summary.

    Returns list of (content, importance, tags) tuples.
    Deduplication is handled by store.store() via cosine distance check.
    """
    facts: list[tuple[str, float, list[str]]] = []
    seen_content: set[str] = set()

    # Split into sentences
    sentences = re.split(r"[.!?\n]+", summary)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]

    for sentence in sentences:
        lower = sentence.lower()

        # Check pattern matches
        for pattern in _FACT_PATTERNS:
            match = pattern.search(sentence)
            if match:
                # Use full sentence as fact content (more context)
                content = sentence.strip()
                if content not in seen_content and len(content) > 20:
                    seen_content.add(content)
                    is_gotcha = any(w in lower for w in ("error", "bug", "fix", "issue", "resolved", "solved"))
                    importance = 8.5 if is_gotcha else 6.5
                    tags = ["auto-extracted", "gotcha"] if is_gotcha else ["auto-extracted"]
                    facts.append((content, importance, tags))
                break

        # Keyword-based extraction (if not already matched by pattern)
        if sentence.strip() not in seen_content:
            keyword_hits = sum(1 for kw in _FACT_KEYWORDS if kw in lower)
            if keyword_hits >= 2:
                content = sentence.strip()
                if len(content) > 20:
                    seen_content.add(content)
                    facts.append((content, 6.0, ["auto-extracted"]))

    # Limit to top 5 facts per summary to avoid noise
    return facts[:5]


# Module-level store references, set during lifespan
_store: MemoryStore | None = None
_session_env_store: SessionEnvironmentStore | None = None

# Tags that indicate infrastructure/environment memories
INFRASTRUCTURE_TAGS = frozenset({
    "infrastructure", "deployment", "environment", "docker",
    "server", "ssh", "dev-container", "testing",
})

# Tags that indicate workflow/procedure memories (for compaction recovery)
WORKFLOW_TAGS = frozenset({
    "workflow", "procedure", "build", "deploy",
})


def create_server(
    db_path: str = DEFAULT_DB_PATH,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Create and configure the MCP server with all tools."""
    import os

    # Pre-load embedding model before lifespan to avoid blocking event loop
    from memory_mcp.db.store import preload_embedding_model
    _preloaded_embedder = preload_embedding_model(embedding_model)

    # ── Eager MemoryStore initialization (before uvicorn starts) ──
    # Initializing here avoids the lifespan race condition where multiple
    # sessions trigger concurrent MemoryStore creation on server restart.
    global _store, _session_env_store
    logger.info("Pre-initializing MemoryStore (db=%s, model=%s)", db_path, embedding_model)
    _store = MemoryStore(db_path=db_path, embedding_model=embedding_model)
    _session_env_store = SessionEnvironmentStore(db_path=db_path)
    try:
        result = _store.migrate_metadata_v4_trash()
        if result["updated"] > 0:
            logger.info("v4 trash migration: %d memories updated", result["updated"])
    except Exception:
        logger.exception("v4 trash migration failed (non-fatal)")
    logger.info("MemoryStore pre-initialized — ready to serve")

    # Holder pattern: mutable dict shared with auth middleware so lifespan
    # can update the event after the event loop is running.
    _ready_holder: dict = {"event": None}

    async def _trash_cleanup_loop() -> None:
        """Periodically purge expired trash memories (every 24h)."""
        while True:
            try:
                await asyncio.sleep(86400)  # 24 hours
                if _store:
                    result = await asyncio.to_thread(_store.purge_expired_trash, 30)
                    if result["purged_count"] > 0:
                        logger.info("TRASH_CLEANUP purged=%d", result["purged_count"])
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Trash cleanup failed")

    @asynccontextmanager
    async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
        # Store is already initialized — just signal readiness on first entry
        if _ready_holder["event"] is None:
            ev = asyncio.Event()
            ev.set()
            _ready_holder["event"] = ev
            logger.info("MemoryStore ready — accepting connections (eager init)")
        yield {"store": _store}
        # Cleanup intentionally omitted: store persists with process lifetime.

    mcp = FastMCP("kandela", lifespan=lifespan, host=host, port=port, stateless_http=True)
    # Expose readiness holder for auth middleware (set before register_dashboard_routes)
    mcp._ready_holder = _ready_holder  # type: ignore[attr-defined]
    mcp._ready_event = None  # type: ignore[attr-defined]  # kept for backward compat

    # ── Robust JSON string→dict pre-processing ──────────────────
    # NOTE: This monkey-patch is a SECONDARY defense only.
    # FastMCP captures the original call_tool reference at decorator
    # registration time (L162: _mcp_server.call_tool()(self.call_tool)),
    # so this patch may NOT be invoked in all MCP request code paths.
    # PRIMARY defense is _parse_json_str (BeforeValidator on each tool param).
    _orig_call_tool = mcp.call_tool

    async def _safe_call_tool(
        name: str, arguments: dict[str, Any]
    ) -> Any:
        for key, value in list(arguments.items()):
            if isinstance(value, str):
                try:
                    parsed = _json.loads(value)
                    # Double-serialized: json.dumps(json.dumps(dict))
                    if isinstance(parsed, str):
                        try:
                            parsed2 = _json.loads(parsed)
                            if isinstance(parsed2, dict):
                                arguments[key] = parsed2
                                continue
                        except (_json.JSONDecodeError, ValueError):
                            pass
                    if isinstance(parsed, dict):
                        arguments[key] = parsed
                except (_json.JSONDecodeError, ValueError):
                    pass
        return await _orig_call_tool(name, arguments)

    mcp.call_tool = _safe_call_tool  # type: ignore[method-assign]

    def _get_store() -> MemoryStore:
        # Single-user mode: global store pre-initialized in create_server()
        if _store is None:
            raise RuntimeError(
                "MemoryStore not initialized — this should not happen. "
                "Ensure create_server() completed successfully."
            )
        return _store

    def _get_session_env_store() -> SessionEnvironmentStore:
        global _session_env_store
        if _session_env_store is None:
            _session_env_store = SessionEnvironmentStore(db_path=db_path)
        return _session_env_store

    # ── Session project tracking (cross-project mismatch hint) ──
    # { user_id_or_"_default" -> project_id }
    _active_project: dict[str, str] = {}

    def _set_active_project(project: str) -> None:
        """Record the session's active project (called from auto_recall)."""
        _active_project["_default"] = project

    def _project_mismatch_hint(project: str) -> str:
        """Return a hint if the given project differs from the session's active project."""
        uid = "_default"
        active = _active_project.get(uid)
        if not active or active == project:
            return ""
        # Check workspace path for the target project
        store = _get_store()
        workspaces = {}
        try:
            for pid in store.list_projects():
                meta = store.get_project_metadata(pid)
                ws = (meta or {}).get("workspace")
                if ws:
                    workspaces[pid] = ws
        except Exception:
            pass
        ws_path = workspaces.get(project, "")
        location = f" (경로: {ws_path})" if ws_path else ""
        return (
            f"\n\n⚠️ 현재 세션의 프로젝트는 '{active}'입니다. "
            f"'{project}' 프로젝트 작업은 해당 디렉토리{location}에서 "
            f"새 세션을 여는 것을 권장합니다."
        )

    # ── memory_store ─────────────────────────────────────────────

    @mcp.tool(
        name="store",
        annotations={
            "title": "Kandela: Store",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def memory_store(params: Annotated[MemoryStoreInput, BeforeValidator(_parse_json_str)]) -> str:
        """Store a fact, decision, snippet, or other memory for a project.

        Use this to persist important information across sessions. Memories
        should be self-contained and specific — avoid vague or overly broad
        content.

        Automatically checks for near-duplicate memories before storing.
        If a similar memory exists (cosine distance < 0.15), returns a
        warning with the existing memory's content and ID. Set force_store=True
        to store anyway.

        Args:
            params: MemoryStoreInput with project, content, memory_type, tags.

        Returns:
            Confirmation message with the stored memory ID, or a duplicate
            warning if a similar memory already exists.
        """
        logger.info("TOOL kandela:store project=%s type=%s", params.project, params.memory_type)
        store = _get_store()

        precomputed_embedding = None

        # Duplicate detection (skip if force_store=True)
        if not params.force_store:
            duplicate, precomputed_embedding = await asyncio.to_thread(
                store.check_duplicate,
                project=params.project,
                content=params.content,
            )
            if duplicate is not None:
                return (
                    f"DUPLICATE WARNING: A similar memory already exists in "
                    f"project '{params.project}'.\n\n"
                    f"Existing memory (distance={duplicate['distance']:.4f}):\n"
                    f"  ID: {duplicate['id']}\n"
                    f"  Content: {duplicate['content']}\n\n"
                    f"To store anyway, call store again with "
                    f"force_store=True.\n"
                    f"To update the existing memory instead, use "
                    f"update with the ID above."
                )

        doc_id = await asyncio.to_thread(
            store.store,
            project=params.project,
            content=params.content,
            memory_type=params.memory_type,
            tags=params.tags,
            priority=params.priority or MemoryPriority.NORMAL,
            importance=params.importance,
            linked_projects=params.linked_projects,
            session_id=params.session_id,
            _embedding=precomputed_embedding,
        )

        # Cross-project pattern detection: hint if similar content exists
        # in multiple other projects (skip when storing to _global or force_store)
        hint = ""
        if (
            precomputed_embedding is not None
            and not params.is_global
            and params.project != GLOBAL_PROJECT_NAME
        ):
            all_projects = await asyncio.to_thread(store.list_projects)
            if len(all_projects) >= 3:
                cross_matches = await asyncio.to_thread(
                    store.detect_cross_project_pattern,
                    source_project=params.project,
                    embedding=precomputed_embedding,
                )
                if cross_matches:
                    project_names = [m["project"] for m in cross_matches]
                    hint = (
                        f"\n\nHINT: Similar content found in "
                        f"{len(cross_matches)} other project(s) "
                        f"({', '.join(project_names)}). "
                        f"Consider storing as global memory "
                        f"(is_global=True) so it's shared across all projects."
                    )

        # MF-3: Code-readable content hint
        code_hint = ""
        from memory_mcp.importance.rules import detect_code_readable
        code_readable_warning = detect_code_readable(params.content, params.tags or [])
        if code_readable_warning:
            code_hint = f"\n\n{code_readable_warning}"

        # Long content split tip
        split_tip = ""
        if len(params.content) > 300:
            split_tip = (
                "\n\n💡 Tip: 300자 이상의 기억은 brief recall에서 핵심이 잘릴 수 있습니다. "
                "독립된 fact 여러 개로 분리 저장을 권장합니다."
            )

        mismatch = _project_mismatch_hint(params.project)
        return (
            f"Stored [{params.memory_type.value}] "
            f"(importance={params.importance:.1f}) "
            f"in project '{params.project}' (id: {doc_id})"
            f"{hint}{code_hint}{split_tip}{mismatch}"
        )

    # ── memory_search ────────────────────────────────────────────

    @mcp.tool(
        name="search",
        annotations={
            "title": "Kandela: Search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_search(params: Annotated[MemorySearchInput, BeforeValidator(_parse_json_str)]) -> str:
        """Search stored memories using semantic similarity.

        Finds memories relevant to the query. Can search within a single
        project or across all projects. Optionally filter by memory type.

        Use ``source_project`` to search in a different project than the
        current one (e.g., reference auth-service decisions from api-gateway).

        Args:
            params: MemorySearchInput with query, project, memory_type,
                    n_results, cross_project.

        Returns:
            Formatted search results with content, metadata, and relevance.
        """
        logger.info("TOOL kandela:search project=%s query=%.80s", params.project, params.query)
        store = _get_store()

        # Determine effective project: source_project overrides project
        # (ignored when cross_project=True)
        effective_project = (
            params.project
            if params.cross_project
            else (params.source_project or params.project)
        )

        results = await asyncio.to_thread(
            store.search,
            query=params.query,
            project=effective_project,
            memory_type=params.memory_type,
            n_results=params.n_results,
            cross_project=params.cross_project,
            tags=params.tags,
            priority=params.priority,
            importance_min=params.importance_min,
            importance_max=params.importance_max,
            date_after=params.date_after,
            date_before=params.date_before,
            use_mmr=params.use_mmr,
            time_weighted=params.time_weighted,
            use_hybrid=params.use_hybrid,
            dynamic_rrf=params.dynamic_rrf,
            mmr_lambda=params.mmr_lambda,
        )
        # Phase 9C: Track search usage
        if results and params.project and not params.cross_project:
            result_ids = [r["id"] for r in results]
            await asyncio.to_thread(
                store.update_usage_counters, params.project, result_ids, "search_count",
            )
        result_text = format_search_results(results)
        if params.project and not params.cross_project:
            result_text += _project_mismatch_hint(params.project)
        return result_text

    # ── memory_delete ────────────────────────────────────────────

    @mcp.tool(
        name="delete",
        annotations={
            "title": "Kandela: Delete",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_delete(params: Annotated[MemoryDeleteInput, BeforeValidator(_parse_json_str)]) -> str:
        """Delete a specific memory by its ID.

        Args:
            params: MemoryDeleteInput with project and memory_id.

        Returns:
            Confirmation or error message.
        """
        logger.info("TOOL kandela:delete project=%s id=%s", params.project, params.memory_id)
        store = _get_store()
        ok = await asyncio.to_thread(
            store.delete, project=params.project, memory_id=params.memory_id,
        )
        mismatch = _project_mismatch_hint(params.project)
        if ok:
            # Trim trash if over limit
            try:
                await asyncio.to_thread(store.trim_trash, 300)
            except Exception:
                pass  # non-fatal
            return f"Moved memory '{params.memory_id}' to trash (project '{params.project}'). Use restore to recover.{mismatch}"
        return f"Failed to delete memory '{params.memory_id}' — ID may not exist.{mismatch}"

    # ── restore ──────────────────────────────────────────────────

    @mcp.tool(name="restore")
    async def memory_restore(params: Annotated[MemoryRestoreInput, BeforeValidator(_parse_json_str)]) -> str:
        """Restore a memory or project from trash.

        Args:
            params: MemoryRestoreInput with project and optional memory_id.
        """
        from memory_mcp.tools.models import MemoryRestoreInput as _  # noqa: F811
        logger.info("TOOL kandela:restore project=%s id=%s", params.project, params.memory_id)
        store = _get_store()
        if params.memory_id:
            ok = await asyncio.to_thread(store.restore_memory, params.project, params.memory_id)
            if ok:
                return f"Restored memory '{params.memory_id}' in project '{params.project}'."
            return f"Failed to restore — memory may not exist in trash."
        else:
            return f"Restored project '{params.project}' from trash."

    # ── trash_list ──────────────────────────────────────────────

    @mcp.tool(name="trash_list")
    async def memory_trash_list(params: Annotated[TrashListInput, BeforeValidator(_parse_json_str)]) -> str:
        """List memories in trash."""
        from memory_mcp.tools.models import TrashListInput as _  # noqa: F811
        store = _get_store()
        items = await asyncio.to_thread(store.list_trash, params.project, params.limit)
        if not items:
            return "Trash is empty."
        lines = [f"Trash ({len(items)} items):"]
        for item in items:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(item["deleted_ts"], tz=timezone.utc)
            lines.append(
                f"  [{item['type']}] {item['content'][:60]}... "
                f"(project={item['project']}, deleted={dt.strftime('%Y-%m-%d')}, id={item['id']})"
            )
        return "\n".join(lines)

    # ── trash_purge ─────────────────────────────────────────────

    @mcp.tool(name="trash_purge")
    async def memory_trash_purge(params: Annotated[TrashPurgeInput, BeforeValidator(_parse_json_str)]) -> str:
        """Permanently delete memories from trash (cannot be undone)."""
        from memory_mcp.tools.models import TrashPurgeInput as _  # noqa: F811
        store = _get_store()
        if params.memory_id and params.project:
            ok = await asyncio.to_thread(store.purge_memory, params.project, params.memory_id)
            return f"Purged memory '{params.memory_id}'." if ok else "Failed to purge."
        elif params.project:
            items = await asyncio.to_thread(store.list_trash, params.project)
            count = 0
            for item in items:
                await asyncio.to_thread(store.purge_memory, item["project"], item["id"])
                count += 1
            return f"Purged {count} memories from project '{params.project}' trash."
        else:
            items = await asyncio.to_thread(store.list_trash, None, 1000)
            count = 0
            for item in items:
                await asyncio.to_thread(store.purge_memory, item["project"], item["id"])
                count += 1
            return f"Purged {count} memories from all trash."

    # ── memory_update ────────────────────────────────────────────

    @mcp.tool(
        name="update",
        annotations={
            "title": "Kandela: Update",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_update(params: Annotated[MemoryUpdateInput, BeforeValidator(_parse_json_str)]) -> str:
        """Update an existing memory's content, type, importance, tags, or links.

        Can update any subset of fields. If content changes, the embedding
        is automatically re-computed and importance rules are re-evaluated.
        Original creation time, usage counters, and ID are preserved.

        Args:
            params: MemoryUpdateInput with project, memory_id, and at least
                    one of content, memory_type, importance, tags, linked_projects.

        Returns:
            Confirmation message with updated fields, or error if not found.
        """
        logger.info("TOOL kandela:update project=%s id=%s", params.project, params.memory_id)
        store = _get_store()
        try:
            result = await asyncio.to_thread(
                store.update,
                project=params.project,
                memory_id=params.memory_id,
                content=params.content,
                memory_type=params.memory_type,
                importance=params.importance,
                tags=params.tags,
                linked_projects=params.linked_projects,
            )
            fields_str = ", ".join(result["updated_fields"])
            mismatch = _project_mismatch_hint(params.project)
            return (
                f"Updated memory '{result['id']}' in project "
                f"'{params.project}' (fields: {fields_str}, "
                f"importance={result['importance']:.1f}){mismatch}"
            )
        except ValueError as e:
            return f"Update failed: {e}"

    # ── memory_summarize_session ─────────────────────────────────

    @mcp.tool(
        name="summarize_session",
        annotations={
            "title": "Kandela: Summarize Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def memory_summarize_session(
        params: Annotated[SessionSummarizeInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Store a session summary as a 'summary' type memory.

        Call this at the END of every work session to persist what was done,
        key decisions, problems encountered, and next steps. Without this,
        the next session will have no record of the current session's work.

        Args:
            params: SessionSummarizeInput with project, summary, tags, session_id.

        Returns:
            Confirmation with stored memory ID.
        """
        project = params.project
        summary = params.summary
        tags: list[str] = params.tags if isinstance(params.tags, list) else [t.strip() for t in params.tags.split(",") if t.strip()]
        session_id = params.session_id
        logger.info("TOOL kandela:summarize_session project=%s", project)
        store = _get_store()
        doc_id = await asyncio.to_thread(
            store.store,
            project=project,
            content=summary,
            memory_type=MemoryType.SUMMARY,
            tags=tags,
            session_id=session_id,
        )

        # Auto-extract facts from summary text
        extracted = _extract_facts_from_summary(summary)
        extracted_ids: list[str] = []
        for fact_content, importance, fact_tags in extracted:
            try:
                fid = await asyncio.to_thread(
                    store.store,
                    project=project,
                    content=fact_content,
                    memory_type=MemoryType.FACT,
                    tags=fact_tags,
                    importance=importance,
                    session_id=session_id,
                )
                extracted_ids.append(fid)
            except Exception:
                logger.debug("Auto-extract fact failed: %s", fact_content[:50])

        result = f"Session summary stored in project '{project}' (id: {doc_id})"
        if extracted_ids:
            result += f"\nAuto-extracted {len(extracted_ids)} fact(s) from summary."
        result += _project_mismatch_hint(project)
        return result

    # ── memory_list_projects ─────────────────────────────────────

    @mcp.tool(
        name="list_projects",
        annotations={
            "title": "Kandela: List Projects",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_list_projects() -> str:
        """List all projects that have stored memories.

        Returns:
            List of project names with memory counts, or a message if none exist.
        """
        logger.info("TOOL kandela:list_projects")
        store = _get_store()
        projects = await asyncio.to_thread(store.list_projects_with_stats)
        if not projects:
            return "No projects found. Use store to create your first memory."
        total_memories = sum(p["memory_count"] for p in projects)
        lines = [f"Projects ({len(projects)}, total {total_memories} memories):"]
        for p in projects:
            lines.append(f"  [{p['name']}] {p['memory_count']} memories")
        return "\n".join(lines)

    # ── memory_stats ─────────────────────────────────────────────

    @mcp.tool(
        name="stats",
        annotations={
            "title": "Kandela: Statistics",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_stats(params: Annotated[ProjectStatsInput, BeforeValidator(_parse_json_str)]) -> str:
        """Get memory usage statistics.

        If project is specified, returns stats for that project
        including token economy (overhead, benefit, net saving).
        Otherwise returns global stats across all projects.

        Args:
            params: ProjectStatsInput with optional project name.

        Returns:
            Formatted statistics including counts by memory type
            and token economy summary.
        """
        logger.info("TOOL kandela:stats project=%s", params.project)
        store = _get_store()
        if params.project:
            stats = await asyncio.to_thread(store.project_stats, params.project)
            token_stats = await asyncio.to_thread(
                store.get_project_token_stats, params.project
            )
            stats["token_economy"] = token_stats
        else:
            stats = await asyncio.to_thread(store.global_stats)
        return format_stats(stats)

    # ── memory_auto_recall ────────────────────────────────────

    @mcp.tool(
        name="auto_recall",
        annotations={
            "title": "Kandela: Auto-Recall",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_auto_recall(params: Annotated[AutoRecallInput, BeforeValidator(_parse_json_str)], ctx: Context) -> str:
        """Automatically recall relevant memories for the current session.

        IMPORTANT: This should be the FIRST tool called when starting work
        on any project. Without calling this, you will lack critical context
        from previous sessions — risking repeated work or contradictory
        decisions.

        Returns a mix of relevant memories and recent session summaries
        to provide continuity.

        Priority-based recall order:
        0. Global memories (from '_global' project) — always included
        1. CRITICAL memories — always loaded, no exceptions
        1.5. Linked memories — from other projects linked to this one
        2. Recent session summaries (NORMAL priority)
        3. Context-relevant NORMAL memories (semantic search)
        4. Recent memories (non-low, time-sorted)
        5. LOW (auto-saved) — only as fallback when above results are sparse
        6. Cross-project discovery — semantic search across other projects
           (only when context is provided)

        Cross-project features:
        - Memories in the '_global' project are automatically loaded for
          every project (personal preferences, coding style, etc.).
        - Memories with ``linked_projects`` pointing to this project are
          included in the "Linked from Other Projects" section.
        - When context is provided, automatically discovers related memories
          from other projects via semantic search (Step 6).

        Args:
            params: AutoRecallInput with project, context, n_results,
                    include_recent_summaries.

        Returns:
            Formatted memories and summaries for session context.
        """
        logger.info("TOOL kandela:auto_recall project=%s mode=%s", params.project, params.mode)
        _set_active_project(params.project)
        store = _get_store()
        env_store = _get_session_env_store()

        # ── Resolve recall mode ────────────────────────────────
        is_compact = params.recall_source == "compact"
        if params.mode is not None:
            effective_mode = params.mode  # explicit override
        elif is_compact:
            effective_mode = "full"       # compaction recovery → full
        else:
            effective_mode = "brief"      # default → lightweight brief

        # ── Brief mode: minimal project overview ───────────────
        if effective_mode == "brief":
            return await _auto_recall_brief(params, store, env_store)

        # ── Full mode: legacy 9-step recall ────────────────────
        sections: list[str] = []
        seen_ids: set[str] = set()
        continuity_triggered = False  # Whether to force-include infra memories

        # Compact mode: force continuity + increase recall budget
        if is_compact:
            continuity_triggered = True

        def _dedupe(items: list[dict]) -> list[dict]:
            """Remove already-seen items."""
            unique = []
            for item in items:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    unique.append(item)
            return unique

        def _is_low_importance(meta: dict) -> bool:
            """Check if memory is low-importance (should be excluded from general recall)."""
            importance = meta.get("importance")
            if isinstance(importance, (int, float)):
                return float(importance) < IMPORTANCE_LOW_THRESHOLD
            # Fallback for unmigrated data
            return meta.get("priority", "normal") == "low"

        # ── Session Continuity Check ──────────────────────────────────
        # Extract client info from MCP context
        client_name = ""
        client_version = ""
        try:
            if ctx.session.client_params and ctx.session.client_params.clientInfo:
                client_name = ctx.session.client_params.clientInfo.name or ""
                client_version = getattr(
                    ctx.session.client_params.clientInfo, "version", ""
                ) or ""
        except Exception:
            pass

        # Build current environment snapshot
        curr_cwd = params.environment.cwd if params.environment else None
        curr_hostname = params.environment.hostname if params.environment else None

        # Save current environment and get previous for comparison
        record_id = await asyncio.to_thread(
            env_store.save,
            params.project,
            session_id=params.session_id,
            cwd=curr_cwd,
            hostname=curr_hostname,
            client_name=client_name or None,
            client_version=client_version or None,
        )

        prev_env = await asyncio.to_thread(
            env_store.get_previous, params.project, record_id,
        )

        # Run continuity checks
        warnings: list[str] = []
        if prev_env is not None:
            from memory_mcp.db.session_env import SESSION_GAP_WARNING_HOURS

            # C-1: CWD changed
            if (
                curr_cwd and prev_env.cwd
                and curr_cwd.rstrip("/") != prev_env.cwd.rstrip("/")
            ):
                warnings.append(
                    f"**[C-1] CWD Changed**: `{curr_cwd}`\n"
                    f"  was: `{prev_env.cwd}`\n"
                    f"  Last session: {prev_env.recalled_at[:19]}"
                )
                continuity_triggered = True

            # C-2: Hostname changed
            if (
                curr_hostname and prev_env.hostname
                and curr_hostname != prev_env.hostname
            ):
                warnings.append(
                    f"**[C-2] Host Changed**: `{curr_hostname}`\n"
                    f"  was: `{prev_env.hostname}`"
                )
                continuity_triggered = True

            # C-3: Client name changed
            if (
                client_name and prev_env.client_name
                and client_name != prev_env.client_name
            ):
                warnings.append(
                    f"**[C-3] Client Changed**: `{client_name}`\n"
                    f"  was: `{prev_env.client_name}`"
                )
                continuity_triggered = True

            # C-4: Client version changed
            if (
                client_version and prev_env.client_version
                and client_version != prev_env.client_version
            ):
                warnings.append(
                    f"**[C-4] Client Updated**: `{client_name} {client_version}`\n"
                    f"  was: `{prev_env.client_name or client_name} "
                    f"{prev_env.client_version}`\n"
                    f"  Sessions may have been reset. Verify your working context."
                )
                continuity_triggered = True

            # C-5: Long gap between sessions
            try:
                from datetime import datetime as _dt, timezone as _tz
                prev_time = _dt.fromisoformat(prev_env.recalled_at)
                if prev_time.tzinfo is None:
                    prev_time = prev_time.replace(tzinfo=_tz.utc)
                now = _dt.now(_tz.utc)
                gap_hours = (now - prev_time).total_seconds() / 3600
                if gap_hours >= SESSION_GAP_WARNING_HOURS:
                    gap_display = (
                        f"{gap_hours:.0f}h"
                        if gap_hours < 48
                        else f"{gap_hours / 24:.1f}d"
                    )
                    warnings.append(
                        f"**[C-5] Long Gap**: {gap_display} since last session\n"
                        f"  Last: {prev_env.recalled_at[:19]}"
                    )
                    continuity_triggered = True
            except Exception:
                pass

            # C-7: Previous session may not have ended properly
            if prev_env.session_id:
                has_summary = await asyncio.to_thread(
                    env_store.has_session_summary, params.project, store,
                )
                if not has_summary:
                    warnings.append(
                        "**[C-7] No Session Summary**: Previous session "
                        "may not have ended properly. Important context "
                        "could be missing."
                    )
                    continuity_triggered = True

        # C-8: Same CWD used by different project (project ID mismatch)
        if curr_cwd:
            other_projects = await asyncio.to_thread(
                env_store.get_other_projects_at_cwd,
                curr_cwd, params.project,
            )
            if other_projects:
                proj_list = ", ".join(f"`{p}`" for p in other_projects[:3])
                warnings.append(
                    f"**[C-8] CWD Shared by Other Projects**: {proj_list}\n"
                    f"  also used this CWD: `{curr_cwd}`\n"
                    f"  Verify you are using the correct project ID."
                )

        if warnings:
            warn_section = (
                "## \u26a0\ufe0f Session Continuity Warnings\n\n"
                + "\n\n".join(warnings)
                + "\n\n---"
            )
            sections.append(warn_section)

        # Force-include infrastructure memories when continuity is triggered
        if continuity_triggered:
            infra_memories = await asyncio.to_thread(
                store.get_by_tags,
                project=params.project,
                tags=list(INFRASTRUCTURE_TAGS),
                n_results=10,
            )
            infra_memories = _dedupe(infra_memories)
            if infra_memories:
                header = (
                    f"## \U0001f527 Infrastructure Context "
                    f"({len(infra_memories)}, forced by continuity check)"
                )
                sections.append(
                    header + "\n" + format_search_results(infra_memories)
                )

        # 0. Global memories (_global project) — cross-project shared memories
        if params.project != GLOBAL_PROJECT_NAME and store.project_exists(
            GLOBAL_PROJECT_NAME
        ):
            global_memories: list[dict[str, Any]] = []

            # 0a. Global critical (importance >= 9.0)
            global_critical = await asyncio.to_thread(
                store.get_by_importance,
                project=GLOBAL_PROJECT_NAME,
                min_importance=IMPORTANCE_CRITICAL_THRESHOLD,
            )
            global_memories.extend(global_critical)

            # 0b. Global recent normal (non-low)
            global_recent = await asyncio.to_thread(
                store.get_recent,
                project=GLOBAL_PROJECT_NAME,
                n_results=5,
            )
            global_recent = [
                r for r in global_recent
                if not _is_low_importance(r.get("metadata", {}))
            ]
            global_memories.extend(global_recent)

            global_memories = _dedupe(global_memories)
            if global_memories:
                header = f"## Global Memories ({len(global_memories)})"
                sections.append(header + "\n" + format_search_results(global_memories))

        # 1. High-importance memories (importance >= 9.0) — ALWAYS loaded
        critical = await asyncio.to_thread(
            store.get_by_importance,
            project=params.project,
            min_importance=IMPORTANCE_CRITICAL_THRESHOLD,
        )
        # Also include legacy CRITICAL memories (unmigrated data safety)
        legacy_critical = await asyncio.to_thread(
            store.get_by_priority,
            project=params.project,
            priority=MemoryPriority.CRITICAL,
        )
        critical = _dedupe(critical + legacy_critical)
        if critical:
            header = f"## Critical Memories ({len(critical)}) — MUST NOT FORGET"
            sections.append(header + "\n" + format_search_results(critical))

        # 1.5. Linked memories — from other projects linked to this one
        linked = await asyncio.to_thread(
            store.get_linked_memories,
            target_project=params.project,
        )
        linked = _dedupe(linked)
        if linked:
            header = f"## Linked from Other Projects ({len(linked)})"
            sections.append(header + "\n" + format_search_results(linked))

        # 2. Recent session summaries (TIME-based, newest first)
        #    Compact mode: retrieve more summaries for better context recovery
        if params.include_recent_summaries:
            summary_count = 5 if is_compact else 3
            summaries = await asyncio.to_thread(
                store.get_recent,
                project=params.project,
                memory_type=MemoryType.SUMMARY,
                n_results=summary_count,
            )
            summaries = _dedupe(summaries)
            if summaries:
                header = f"## Recent Session Summaries ({len(summaries)})"
                sections.append(header + "\n" + format_search_results(summaries))

        # 2.5. Workflow memories (compact mode only)
        #      Retrieve workflow-tagged memories to restore interrupted procedures
        if is_compact:
            workflow_memories = await asyncio.to_thread(
                store.get_by_tags,
                project=params.project,
                tags=list(WORKFLOW_TAGS),
                n_results=5,
            )
            workflow_memories = _dedupe(workflow_memories)
            if workflow_memories:
                header = (
                    f"## Workflow & Procedures "
                    f"({len(workflow_memories)}, compaction recovery)"
                )
                sections.append(
                    header + "\n" + format_search_results(workflow_memories)
                )

        # 3. Context-relevant memories (SEMANTIC + MMR + time-weighted)
        #    Excludes low-importance via importance_min filter
        #    Uses lower MMR λ for diversity (H-2.4: recall needs topic breadth)
        if params.context:
            relevant = await asyncio.to_thread(
                store.search,
                query=params.context,
                project=params.project,
                n_results=params.n_results,
                importance_min=IMPORTANCE_LOW_THRESHOLD,
                use_mmr=True,
                time_weighted=True,
                mmr_lambda=DEFAULT_MMR_LAMBDA_RECALL,
            )
            # Post-filter for unmigrated data without importance field
            relevant = [r for r in relevant if not _is_low_importance(r.get("metadata", {}))]
            relevant = _dedupe(relevant)
            if relevant:
                header = f"## Context-Relevant Memories ({len(relevant)})"
                sections.append(header + "\n" + format_search_results(relevant))

        # 4. Recent memories (TIME-based, excluding low-importance)
        recent_normal = await asyncio.to_thread(
            store.get_recent,
            project=params.project,
            n_results=params.n_results,
        )
        recent_normal = [r for r in recent_normal if not _is_low_importance(r.get("metadata", {}))]
        recent_normal = _dedupe(recent_normal)
        if recent_normal:
            header = f"## Recent Memories ({len(recent_normal)})"
            sections.append(header + "\n" + format_search_results(recent_normal))

        # 5. Low-importance — only if total results are sparse (< 3 items above)
        items_above = len(seen_ids)
        if items_above < 3:
            low_recent = await asyncio.to_thread(
                store.get_by_importance,
                project=params.project,
                max_importance=IMPORTANCE_LOW_THRESHOLD,
                n_results=5,
            )
            # Also check legacy LOW
            legacy_low = await asyncio.to_thread(
                store.get_by_priority,
                project=params.project,
                priority=MemoryPriority.LOW,
                n_results=5,
            )
            low_recent = _dedupe(low_recent + legacy_low)
            if low_recent:
                header = f"## Auto-Saved (fallback, {len(low_recent)})"
                sections.append(header + "\n" + format_search_results(low_recent))

        # 6. Cross-project discovery — semantic search across other projects
        if params.context and params.project != GLOBAL_PROJECT_NAME:
            exclude_projects: set[str] = {params.project, GLOBAL_PROJECT_NAME}
            # Add linked source projects to exclusion set
            for item in linked:
                src_proj = item.get("metadata", {}).get("project", "")
                if src_proj:
                    exclude_projects.add(src_proj)

            cross_results = await asyncio.to_thread(
                store.discover_cross_project_relevant,
                source_project=params.project,
                query=params.context,
                exclude_projects=exclude_projects,
            )
            cross_results = _dedupe(cross_results)
            if cross_results:
                header = f"## Related from Other Projects ({len(cross_results)})"
                sections.append(header + "\n" + format_search_results(cross_results))

        # 7. Cross-session changes — notify about concurrent modifications
        if params.session_id:
            other_session_memories = await asyncio.to_thread(
                store.get_recent_by_other_sessions,
                project=params.project,
                current_session_id=params.session_id,
                n_results=5,
            )
            other_session_memories = _dedupe(other_session_memories)
            if other_session_memories:
                # Group by session_id for clarity
                by_session: dict[str, list[dict]] = {}
                for mem in other_session_memories:
                    sid = mem.get("metadata", {}).get("session_id", "unknown")
                    by_session.setdefault(sid, []).append(mem)

                lines = [f"## ⚡ Changes from Other Sessions ({len(other_session_memories)})"]
                lines.append("_These memories were created by other concurrent sessions._\n")
                for sid, mems in by_session.items():
                    lines.append(f"**Session: {sid[:12]}...**")
                    lines.append(format_search_results(mems))
                sections.insert(0, "\n".join(lines))  # Show at top for visibility

        if not sections:
            return f"No memories found for project '{params.project}'. This may be a new project."

        # Phase 9C: Track recall usage for all returned memories
        if seen_ids:
            await asyncio.to_thread(
                store.update_usage_counters,
                params.project, list(seen_ids), "recall_count",
            )

        if is_compact:
            intro = f"# Memory Recall for '{params.project}' (Compaction Recovery)\n"
        else:
            intro = f"# Memory Recall for '{params.project}'\n"
        result = intro + "\n\n".join(sections)

        # Non-Claude clients: append workflow guide (replaces CLAUDE.md)
        if "claude" not in client_name.lower():
            result += (
                "\n\n---\n"
                "## Session Workflow\n"
                "- Store important decisions/facts with `store` during this session\n"
                "- At session end, call `summarize_session` to preserve context\n"
            )

        # Checkpoint tip for all clients
        result += (
            "\n\nTip: Call `summarize_session` at major milestones or every ~20 messages."
        )

        return result

    async def _auto_recall_brief(
        params: AutoRecallInput,
        store: MemoryStore,
        env_store: SessionEnvironmentStore | None,
    ) -> str:
        """Brief mode: code-invisible context only.

        Prioritizes information that CANNOT be read from code:
        1. Gotchas — prevent repeated mistakes (highest value)
        2. Decisions — why something was chosen, not what
        3. Infra/deploy — server paths, SSH, deploy commands
        4. Preferences/conventions — human choices, team agreements
        5. Other critical — remaining importance >= 9.0

        Returns ~100-300 tokens focused on code-invisible knowledge.
        """
        from memory_mcp.constants import (
            BRIEF_MAX_CRITICAL,
            COMPACT_RESULT_CONTENT_LEN,
            COMPACT_RESULT_CONTENT_LEN_CRITICAL,
            GLOBAL_PROJECT_NAME,
            IMPORTANCE_CRITICAL_THRESHOLD,
        )
        from memory_mcp.utils.formatting import format_brief_recall_item

        def _content_len(result: dict) -> int:  # type: ignore[type-arg]
            """Return content length based on importance."""
            meta = result.get("metadata", {})
            imp = meta.get("importance")
            if isinstance(imp, (int, float)) and float(imp) >= IMPORTANCE_CRITICAL_THRESHOLD:
                return COMPACT_RESULT_CONTENT_LEN_CRITICAL
            return COMPACT_RESULT_CONTENT_LEN

        project = params.project

        # 1. Get project brief (2 lightweight ChromaDB queries)
        brief = await asyncio.to_thread(store.get_project_brief, project)

        if brief["memory_count"] == 0:
            return (
                f"No memories found for project '{project}'. This may be a new project.\n"
                "Use `store` to save important facts and decisions."
            )

        # 2. Load critical memories (importance >= 9.0)
        critical_memories: list[dict] = []
        try:
            raw = await asyncio.to_thread(
                store.get_by_importance,
                project,
                min_importance=IMPORTANCE_CRITICAL_THRESHOLD,
                n_results=BRIEF_MAX_CRITICAL,
            )
            critical_memories = raw or []
        except Exception:
            logger.debug("Brief recall: failed to load critical memories for %s", project)

        # 2b. Global critical memories
        global_critical: list[dict] = []
        try:
            raw_g = await asyncio.to_thread(
                store.get_by_importance,
                GLOBAL_PROJECT_NAME,
                min_importance=IMPORTANCE_CRITICAL_THRESHOLD,
                n_results=10,
            )
            global_critical = raw_g or []
        except Exception:
            pass

        # 3. Categorize critical memories by code-invisible domain
        gotchas: list[dict] = []
        decisions: list[dict] = []
        infra_deploy: list[dict] = []
        other_critical: list[dict] = []

        seen_ids: set[str] = set()

        for mem in critical_memories:
            mid = mem.get("id", "")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)

            meta = mem.get("metadata", {})
            mtype = meta.get("type", "fact")
            raw_tags = meta.get("tags", "[]")
            if isinstance(raw_tags, str):
                try:
                    tags = set(_json.loads(raw_tags))
                except (_json.JSONDecodeError, TypeError):
                    tags = set()
            elif isinstance(raw_tags, list):
                tags = set(raw_tags)
            else:
                tags = set()

            if "gotcha" in tags:
                gotchas.append(mem)
            elif mtype == "decision":
                decisions.append(mem)
            elif tags & {"deploy", "infra", "ssh", "docker", "server", "file-location"}:
                infra_deploy.append(mem)
            else:
                other_critical.append(mem)

        # 3b. Load additional gotcha-tagged memories (may not all be critical)
        try:
            gotcha_raw = await asyncio.to_thread(
                store.search,
                project=project,
                query="gotcha 주의 실패 에러 warning",
                n_results=10,
                tags=["gotcha"],
            )
            for mem in (gotcha_raw or []):
                mid = mem.get("id", "")
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    gotchas.append(mem)
        except Exception:
            logger.debug("Brief recall: gotcha search failed for %s", project)

        # 3c. Load env/infrastructure resource memories (API keys, config, credentials)
        # Prevents "didn't know the key existed" situations
        env_resources: list[dict] = []
        try:
            env_raw = await asyncio.to_thread(
                store.search,
                project=project,
                query="API key 환경변수 설정 credentials config .env",
                n_results=10,
                tags=["env", "api-key", "credentials", "infrastructure", "config"],
            )
            for mem in (env_raw or []):
                mid = mem.get("id", "")
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    env_resources.append(mem)
        except Exception:
            logger.debug("Brief recall: env resource search failed for %s", project)

        # 4. Build output — code-invisible first, minimal header
        mem_count = brief.get("memory_count", 0)
        last_date = brief.get("last_session_date")
        last_snippet = brief.get("last_summary_snippet")

        lines = [f"# Memory: {project} ({mem_count} memories)"]
        if last_date and last_snippet:
            lines.append(f"Last: {last_date} — \"{last_snippet}\"")
        elif last_date:
            lines.append(f"Last session: {last_date}")

        # Section 1: Gotchas (highest value — prevent repeated mistakes)
        if gotchas:
            lines.append(f"\n## ⚠️ Gotchas ({len(gotchas)})")
            for i, r in enumerate(gotchas, 1):
                lines.append(f"{i}. {format_brief_recall_item(r, max_content_len=_content_len(r))}")

        # Section 2: Decisions (why — can't read from code)
        if decisions:
            lines.append(f"\n## Decisions ({len(decisions)})")
            for i, r in enumerate(decisions, 1):
                lines.append(f"{i}. {format_brief_recall_item(r, max_content_len=_content_len(r))}")

        # Section 3: Infra/Deploy (server paths, SSH, config)
        if infra_deploy:
            lines.append(f"\n## Infra/Deploy ({len(infra_deploy)})")
            for i, r in enumerate(infra_deploy, 1):
                lines.append(f"{i}. {format_brief_recall_item(r, max_content_len=_content_len(r))}")

        # Section 3b: Env/Resources (API keys, config, credentials)
        if env_resources:
            lines.append(f"\n## Env/Resources ({len(env_resources)})")
            for i, r in enumerate(env_resources, 1):
                lines.append(f"{i}. {format_brief_recall_item(r, max_content_len=_content_len(r))}")

        # Section 4: Other critical (remaining high-importance items)
        if other_critical:
            lines.append(f"\n## Critical ({len(other_critical)})")
            for i, r in enumerate(other_critical, 1):
                lines.append(f"{i}. {format_brief_recall_item(r, max_content_len=_content_len(r))}")

        # Section 5: Global critical
        if global_critical:
            lines.append(f"\n## Global ({len(global_critical)})")
            for i, r in enumerate(global_critical, 1):
                lines.append(f"{i}. {format_brief_recall_item(r, max_content_len=_content_len(r))}")

        result = "\n".join(lines)

        # Pending task notification
        pending_tasks = brief.get("pending_task_count", 0)
        if pending_tasks > 0:
            result += f"\n\n📋 대기 작업 {pending_tasks}건 — `context_search(query='task pending')` 또는 `/kd-task`로 확인"

        # Unreviewed memo notification
        unreviewed = brief.get("unreviewed_count", 0)
        if unreviewed > 0:
            result += f"\n\n📬 미확인 메모 {unreviewed}건 — `inbox` 또는 `/kd-inbox`로 확인"

        # Docs map (SessionStart Hook에서 전송된 캐시)
        from memory_mcp.templates.hook_prompts import get_docs_map
        docs_map = get_docs_map(project)
        if docs_map:
            result += docs_map

        # Compact footer
        result += (
            "\n\n---\n"
            "규칙: 실패→report_failure(필수) | "
            "저장→코드에없는것만(Why/Gotcha/인프라) — 저장 전 '코드를 읽으면 알 수 있는가?' 자가검증 | "
            "체크포인트→20메시지 | On-demand→context_search"
        )

        # Track recall usage
        all_recalled = gotchas + decisions + infra_deploy + env_resources + other_critical + global_critical
        all_ids = [m["id"] for m in all_recalled]
        if all_ids:
            await asyncio.to_thread(
                store.update_usage_counters,
                project, all_ids, "recall_count",
            )

        # Save environment for continuity tracking
        if env_store and params.environment:
            try:
                await asyncio.to_thread(
                    env_store.save,
                    project=project,
                    session_id=params.session_id,
                    cwd=params.environment.cwd,
                    hostname=params.environment.hostname,
                )
            except Exception:
                logger.debug("Brief recall: env save failed for %s", project)

        return result

    # ── memory_context_search (Lazy Retrieval) ─────────────────

    @mcp.tool(
        name="context_search",
        description=(
            "Search memories with compact output optimized for mid-conversation use.\n\n"
            "Returns results in a condensed one-liner format (~50 tokens per result "
            "vs ~150 for search). Use this for quick context lookups during "
            "conversation. For detailed results with full metadata, use search instead."
        ),
    )
    async def memory_context_search(
        params: Annotated[ContextSearchInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Compact semantic search — on-demand retrieval for lazy recall."""
        logger.info("TOOL kandela:context_search project=%s query=%.80s", params.project, params.query)
        from memory_mcp.constants import COMPACT_RESULT_CONTENT_LEN
        from memory_mcp.utils.formatting import format_compact_results

        store = _get_store()
        project = params.project

        if not project and not params.cross_project:
            return "Error: project is required unless cross_project=True."

        # Use hybrid search by default — keyword queries (e.g., "Java JDK JAVA_HOME")
        # fail with pure semantic search (cosine distance 0.4+) but BM25 matches exactly.
        results = await asyncio.to_thread(
            store.search,
            query=params.query,
            project=project,
            n_results=params.n_results,
            cross_project=params.cross_project,
            use_hybrid=True,    # BM25 + semantic for keyword queries
            use_mmr=True,       # Always use MMR for diversity
            time_weighted=True,  # Favor recent memories
        )

        if not results:
            return "No memories found."

        # Track search usage
        ids_found = [r["id"] for r in results if "id" in r]
        if ids_found and project:
            await asyncio.to_thread(
                store.update_usage_counters,
                project, ids_found, "search_count",
            )

        result_text = format_compact_results(
            results,
            max_content_len=COMPACT_RESULT_CONTENT_LEN,
            include_content=params.include_content,
        )
        if project and not params.cross_project:
            result_text += _project_mismatch_hint(project)
        return result_text

    # ── memory_project_rename ──────────────────────────────────

    @mcp.tool(
        name="project_rename",
        annotations={
            "title": "Kandela: Rename Project",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def memory_project_rename(params: Annotated[ProjectRenameInput, BeforeValidator(_parse_json_str)]) -> str:
        """Rename an existing project.

        Moves all memories from the old project name to a new one.
        The old project is deleted after all memories are transferred.

        Args:
            params: ProjectRenameInput with old_name and new_name.

        Returns:
            Confirmation with the number of memories moved.
        """
        logger.info("TOOL kandela:project_rename old=%s new=%s", params.old_name, params.new_name)
        store = _get_store()
        try:
            result = await asyncio.to_thread(
                store.rename_project,
                old_name=params.old_name,
                new_name=params.new_name,
            )
            return (
                f"Renamed project '{result['old_name']}' -> '{result['new_name']}' "
                f"({result['memories_moved']} memories moved)"
            )
        except ValueError as e:
            return f"Rename failed: {e}"

    # ── memory_project_delete ──────────────────────────────────

    @mcp.tool(
        name="project_delete",
        annotations={
            "title": "Kandela: Delete Project",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_project_delete(params: Annotated[ProjectDeleteInput, BeforeValidator(_parse_json_str)]) -> str:
        """Delete an entire project and all its memories.

        This is a destructive operation. Set confirm=True to proceed.

        Args:
            params: ProjectDeleteInput with project name and confirm flag.

        Returns:
            Confirmation or warning message.
        """
        logger.info("TOOL kandela:project_delete project=%s", params.project)
        store = _get_store()
        exists = await asyncio.to_thread(store.project_exists, params.project)
        if not exists:
            return f"Project '{params.project}' not found."

        if not params.confirm:
            stats = await asyncio.to_thread(store.project_stats, params.project)
            count = stats["total_memories"]
            return (
                f"Project '{params.project}' has {count} memories. "
                f"To delete permanently, call again with confirm=True. "
                f"This action is irreversible."
            )

        try:
            result = await asyncio.to_thread(store.delete_project, params.project)
            return f"Deleted project '{result['project']}' and all {result['memories_deleted']} memories."
        except ValueError as e:
            return f"Delete failed: {e}"

    # ── memory_inbox ──────────────────────────────────────────

    @mcp.tool(
        name="inbox",
        annotations={
            "title": "Kandela: Inbox",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def memory_inbox(params: Annotated[InboxInput, BeforeValidator(_parse_json_str)]) -> str:
        """View and manage unreviewed memories (inbox).

        Shows memories tagged 'unreviewed' (e.g., from Telegram or auto-save).
        Set mark_reviewed=True to remove the 'unreviewed' tag from all
        returned items (batch confirmation).

        Args:
            params: InboxInput with project, n_results, mark_reviewed.

        Returns:
            Formatted list of unreviewed memories, or empty-inbox message.
        """
        logger.info("TOOL kandela:inbox project=%s", params.project)
        store = _get_store()
        results = await asyncio.to_thread(
            store.get_by_tag, params.project, "unreviewed", params.n_results,
        )

        if not results:
            return "📭 미확인 메모가 없습니다."

        lines = [f"📬 미확인 메모 {len(results)}건:"]
        for i, r in enumerate(results, 1):
            # Show full content (no truncation) — inbox needs complete text
            meta = r.get("metadata", {})
            mtype = meta.get("type", "?")
            imp = meta.get("importance")
            if isinstance(imp, (int, float)):
                imp_str = f"{float(imp):.1f}"
            else:
                _prio = meta.get("priority", "normal")
                imp_str = {"critical": "9.0", "low": "2.0"}.get(_prio, "5.0")
            date_str = str(meta.get("created_at", "?"))[:10]
            content = r.get("content", "").replace("\n", " ").strip()
            lines.append(f"{i}. [{mtype}] {content} ({imp_str}, {date_str})")

        if params.mark_reviewed:
            import json as _json

            for r in results:
                raw_tags = r["metadata"].get("tags", "[]")
                if isinstance(raw_tags, str):
                    try:
                        old_tags = _json.loads(raw_tags)
                    except (ValueError, TypeError):
                        old_tags = []
                else:
                    old_tags = raw_tags if isinstance(raw_tags, list) else []
                new_tags = [t for t in old_tags if t != "unreviewed"]
                await asyncio.to_thread(
                    store.update,
                    params.project,
                    r["id"],
                    tags=new_tags,
                )
            lines.append(f"\n✅ {len(results)}건 확인 처리 완료")
        else:
            lines.append(
                "\n확인 처리: `inbox(mark_reviewed=True)` "
                "또는 개별 `update`로 'unreviewed' 태그 제거"
            )

        result = "\n".join(lines)
        result += _project_mismatch_hint(params.project)
        return result

    # ── memory_infra_* ─────────────────────────────────────────

    _INFRA_DOC_TAG = "project-infra"

    async def _find_infra(project: str) -> dict | None:
        """Return the project infra document, or None."""
        store = _get_store()
        results = await asyncio.to_thread(store.get_by_tag, project, _INFRA_DOC_TAG, 1)
        return results[0] if results else None

    @mcp.tool(
        name="infra_update",
        annotations={
            "title": "Kandela: Infra Update",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_infra_update(
        params: Annotated[InfraUpdateInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Create or overwrite the project infrastructure and test setup document.

        Use this when the user says things like:
        - "테스트 실행 방법 저장해", "인프라 설정 기록해"
        - "여기서 테스트 실행하는 거 기억해", "컨테이너 구조 저장해"
        - "save infra setup", "remember how to run tests"

        This document describes WHERE and HOW to run tests/benchmarks,
        container/server layout, and infra gotchas.
        Stored at importance 9.0 so it loads automatically at every session start —
        Claude will always know the test execution environment without being told again.

        One living document per project. Always overwrites the previous state.
        Call infra_get first to read current state before updating.

        Recommended format (adapt as needed):
          ## 테스트 실행
          - 위치: docker exec memory-mcp-dev pytest tests/ -v
          - 주의: 프로덕션 컨테이너(memory-mcp-server)에서 실행 금지
          ## 벤치마크
          - 위치: 서버 ~/kandela/benchmark_v4/
          - 명령어: python -m benchmark_v4 [옵션]
          ## 주요 컨테이너
          - memory-mcp-server: 프로덕션
          - memory-mcp-dev: 개발/테스트 전용

        project: use the current project ID from CLAUDE.md context.
        """
        logger.info("TOOL kandela:infra_update project=%s", params.project)
        store = _get_store()
        existing = await _find_infra(params.project)

        if existing:
            await asyncio.to_thread(
                store.update,
                params.project,
                existing["id"],
                content=params.content,
                importance=9.0,
            )
            return f"✅ 인프라/테스트 설정 업데이트 완료:\n\n{params.content}"
        else:
            mem_id = await asyncio.to_thread(
                store.store,
                params.project,
                params.content,
                memory_type=MemoryType.FACT,
                tags=[_INFRA_DOC_TAG],
                importance=9.0,
            )
            return f"✅ 인프라/테스트 설정 문서 생성 완료 (id: {mem_id}):\n\n{params.content}"

    @mcp.tool(
        name="infra_get",
        annotations={
            "title": "Kandela: Infra Get",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_infra_get(
        params: Annotated[InfraGetInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Retrieve the project infrastructure and test setup document.

        Use this when the user says things like:
        - "테스트 어디서 돌려?", "인프라 설정 보여줘"
        - "컨테이너 구조가 어떻게 돼?", "how do I run tests?"
        - "벤치마크 어떻게 실행해?"

        Also call before infra_update to read current state first.
        project: use the current project ID from CLAUDE.md context.
        """
        logger.info("TOOL kandela:infra_get project=%s", params.project)
        existing = await _find_infra(params.project)
        if not existing:
            return (
                f"📋 '{params.project}' 프로젝트의 인프라/테스트 설정 문서가 없습니다. "
                "infra_update로 작성해 주세요."
            )
        meta = existing.get("metadata", {})
        updated = str(meta.get("updated_at") or meta.get("created_at", ""))[:10]
        return f"🔧 인프라/테스트 설정 (최종 업데이트: {updated})\n\n{existing['content']}"

    # ── memory_progress_* ──────────────────────────────────────

    _PROGRESS_TAG = "project-progress"

    async def _find_progress(project: str) -> dict | None:
        """Return the project progress document, or None."""
        store = _get_store()
        results = await asyncio.to_thread(store.get_by_tag, project, _PROGRESS_TAG, 1)
        return results[0] if results else None

    @mcp.tool(
        name="progress_update",
        annotations={
            "title": "Kandela: Progress Update",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_progress_update(
        params: Annotated[ProgressUpdateInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Create or overwrite the project progress document.

        Use this when the user says things like:
        - "진행상황 업데이트해", "Phase X 완료 처리해"
        - "현재 상태 저장해", "다음 단계로 넘어가"
        - "update progress", "mark phase X as done"

        One living document per project. Always overwrites the previous state.
        Stored with importance 9.0 so it loads automatically at every session start.
        Use progress_get to read the current state first before updating.

        Recommended content format (free-form, adapt as needed):
          ## 현재 Phase: Phase X
          ## 진행 중: ...
          ## 완료: Phase 1, Phase 2, ...
          ## 다음: ...

        project: use the current project ID from CLAUDE.md context.

        Args:
            params: ProgressUpdateInput with project, content.

        Returns:
            Confirmation with the saved content.
        """
        logger.info("TOOL kandela:progress_update project=%s", params.project)
        store = _get_store()
        existing = await _find_progress(params.project)

        if existing:
            await asyncio.to_thread(
                store.update,
                params.project,
                existing["id"],
                content=params.content,
                importance=9.0,
            )
            return f"✅ 진행상황 업데이트 완료:\n\n{params.content}"
        else:
            mem_id = await asyncio.to_thread(
                store.store,
                params.project,
                params.content,
                memory_type=MemoryType.FACT,
                tags=[_PROGRESS_TAG],
                importance=9.0,
            )
            return f"✅ 진행상황 문서 생성 완료 (id: {mem_id}):\n\n{params.content}"

    @mcp.tool(
        name="progress_get",
        annotations={
            "title": "Kandela: Progress Get",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_progress_get(
        params: Annotated[ProgressGetInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Retrieve the current project progress document.

        Use this when the user says things like:
        - "지금 어디까지 했어?", "진행상황 보여줘", "현재 phase가 뭐야?"
        - "뭐가 완료됐어?", "다음 뭐 해?", "show progress"

        Also call this before progress_update to read current state first.
        project: use the current project ID from CLAUDE.md context.

        Args:
            params: ProgressGetInput with project.

        Returns:
            Current progress document, or a not-found message.
        """
        logger.info("TOOL kandela:progress_get project=%s", params.project)
        existing = await _find_progress(params.project)
        if not existing:
            return (
                f"📋 '{params.project}' 프로젝트의 진행상황 문서가 없습니다. "
                "progress_update로 작성해 주세요."
            )
        meta = existing.get("metadata", {})
        updated = str(meta.get("updated_at") or meta.get("created_at", ""))[:10]
        return f"📋 프로젝트 진행상황 (최종 업데이트: {updated})\n\n{existing['content']}"

    # ── memory_checklist_* ─────────────────────────────────────

    def _checklist_tag(name: str) -> str:
        """Normalized tag for a checklist name."""
        return f"checklist:{name.strip()}"

    async def _find_checklist(project: str, name: str) -> dict | None:
        """Return the first memory tagged with checklist:{name}, or None."""
        store = _get_store()
        tag = _checklist_tag(name)
        results = await asyncio.to_thread(store.get_by_tag, project, tag, 1)
        return results[0] if results else None

    @mcp.tool(
        name="checklist_add",
        annotations={
            "title": "Kandela: Checklist Add",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def memory_checklist_add(
        params: Annotated[ChecklistAddInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Add an action item to a named checklist stored in memory.

        Use this when the user says things like:
        - "~~ 체크리스트에 추가해", "add to checklist", "체크리스트 만들어"
        - "배포 전 확인할 것: ~~", "스프린트 TODO에 추가해"

        Unlike store (for facts/decisions), checklists are for
        actionable to-do items grouped under a named list.
        Creates the checklist automatically if it doesn't exist yet.
        Use checklist_get to view, checklist_done to tick off items.

        project: use the current project ID from CLAUDE.md context.

        Args:
            params: ChecklistAddInput with project, name, item.

        Returns:
            Confirmation with the updated checklist content.
        """
        logger.info("TOOL kandela:checklist_add project=%s name=%s", params.project, params.name)
        store = _get_store()
        existing = await _find_checklist(params.project, params.name)

        new_line = f"- [ ] {params.item}"

        if existing:
            old_content = existing.get("content", "")
            new_content = old_content.rstrip() + f"\n{new_line}"
            await asyncio.to_thread(
                store.update,
                params.project,
                existing["id"],
                content=new_content,
            )
            return f"✅ '{params.name}' 체크리스트에 추가:\n{new_line}\n\n{new_content}"
        else:
            tag = _checklist_tag(params.name)
            content = f"## Checklist: {params.name}\n{new_line}"
            mem_id = await asyncio.to_thread(
                store.store,
                params.project,
                content,
                memory_type=MemoryType.FACT,
                tags=["checklist", tag],
                importance=7.0,
            )
            return f"✅ '{params.name}' 체크리스트 생성 및 항목 추가 (id: {mem_id}):\n{content}"

    @mcp.tool(
        name="checklist_get",
        annotations={
            "title": "Kandela: Checklist Get",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_checklist_get(
        params: Annotated[ChecklistGetInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Retrieve a named checklist from memory.

        Use this when the user says things like:
        - "~~ 체크리스트 보여줘", "show checklist", "체크리스트 확인"
        - "뭐 남았어?", "TODO 목록 보여줘"

        Returns the checklist content with done/total count.
        project: use the current project ID from CLAUDE.md context.

        Args:
            params: ChecklistGetInput with project, name.

        Returns:
            Checklist content with completion status, or a not-found message.
        """
        logger.info("TOOL kandela:checklist_get project=%s name=%s", params.project, params.name)
        existing = await _find_checklist(params.project, params.name)
        if not existing:
            return f"📋 '{params.name}' 체크리스트가 없습니다. checklist_add로 추가하세요."
        content = existing.get("content", "")
        total = content.count("- [")
        done = content.count("- [x]")
        return f"📋 {content}\n\n({done}/{total} 완료)"

    @mcp.tool(
        name="checklist_done",
        annotations={
            "title": "Kandela: Checklist Done",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_checklist_done(
        params: Annotated[ChecklistDoneInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Mark a checklist item as done [x] or undone [ ].

        Use this when the user says things like:
        - "1번 완료", "3번 체크해줘", "done", "✓"
        - "2번 아직 안 됐어", "2번 다시 미완료로"

        Call checklist_get first if you need to see item numbers.
        project: use the current project ID from CLAUDE.md context.

        Args:
            params: ChecklistDoneInput with project, name, item_index, done.

        Returns:
            Updated checklist content.
        """
        logger.info(
            "TOOL kandela:checklist_done project=%s name=%s idx=%d done=%s",
            params.project, params.name, params.item_index, params.done,
        )
        store = _get_store()
        existing = await _find_checklist(params.project, params.name)
        if not existing:
            return f"❌ '{params.name}' 체크리스트가 없습니다."

        content = existing.get("content", "")
        lines = content.split("\n")
        item_lines = [i for i, l in enumerate(lines) if l.startswith("- [")]

        idx = params.item_index - 1  # convert to 0-based
        if idx < 0 or idx >= len(item_lines):
            return f"❌ 항목 번호 {params.item_index}가 범위를 벗어났습니다. 항목 수: {len(item_lines)}"

        line_idx = item_lines[idx]
        if params.done:
            lines[line_idx] = lines[line_idx].replace("- [ ]", "- [x]", 1)
        else:
            lines[line_idx] = lines[line_idx].replace("- [x]", "- [ ]", 1)

        new_content = "\n".join(lines)
        await asyncio.to_thread(
            store.update,
            params.project,
            existing["id"],
            content=new_content,
        )
        status = "완료" if params.done else "미완료"
        return f"✅ {params.item_index}번 항목 {status} 처리:\n{new_content}"

    # ── memory_get_guide ───────────────────────────────────────

    # ── memory_report_failure (Circuit Breaker) ─────────────────

    @mcp.tool(
        name="report_failure",
        annotations={
            "title": "Kandela: Report Failure",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_report_failure(params: Annotated[ReportFailureInput, BeforeValidator(_parse_json_str)]) -> str:
        """Report a tool, command, or API failure for circuit breaker protection.

        MUST be called whenever a tool/command/API fails. The server tracks
        failure patterns and returns:
        - Attempt 1: Related gotchas (if any) to help fix the issue.
        - Attempt 2: Gotchas + strong recommendation to change approach.
        - Attempt 3+: 🛑 STOP directive — must not retry the same approach.

        Also detects circular debugging (same file edited 3+ times).

        Args:
            params: ReportFailureInput with project, tool_name, error_summary,
                    attempt, optional file_path.

        Returns:
            Gotcha results and/or STOP directive based on attempt count.
        """
        logger.info(
            "TOOL kandela:report_failure project=%s tool=%s attempt=%d",
            params.project, params.tool_name, params.attempt,
        )
        store = _get_store()

        # Search for related gotchas
        gotcha_results: list[dict] = []
        try:
            gotcha_results = await asyncio.to_thread(
                store.search,
                project=params.project,
                query=f"{params.tool_name} gotcha 실패 에러",
                n_results=5,
                tags=["gotcha"],
            )
        except Exception:
            logger.debug("report_failure: gotcha search failed for %s", params.tool_name)

        # Also search without tag filter for broader matches
        broader_results: list[dict] = []
        try:
            broader_results = await asyncio.to_thread(
                store.search,
                project=params.project,
                query=f"{params.tool_name} {params.error_summary}",
                n_results=3,
            )
        except Exception:
            pass

        # Merge and deduplicate
        seen_ids = {r["id"] for r in gotcha_results}
        for r in broader_results:
            if r["id"] not in seen_ids:
                gotcha_results.append(r)
                seen_ids.add(r["id"])

        # Format gotcha section
        gotcha_section = ""
        if gotcha_results:
            from memory_mcp.utils.formatting import format_compact_result
            from memory_mcp.constants import COMPACT_RESULT_CONTENT_LEN
            gotcha_section = "\n\n📋 관련 기억:\n"
            for i, r in enumerate(gotcha_results[:5], 1):
                gotcha_section += f"  {i}. {format_compact_result(r, max_content_len=COMPACT_RESULT_CONTENT_LEN * 2)}\n"

        # Circular debug detection
        circular_warning = ""
        if params.file_path:
            circular_warning = (
                f"\n\n📁 파일: {params.file_path} — "
                f"같은 파일을 반복 수정 중이라면 근본 원인을 재분석하세요."
            )

        # Response based on attempt count
        mismatch = _project_mismatch_hint(params.project)
        if params.attempt >= 3:
            return (
                f"🛑 CIRCUIT BREAKER: '{params.tool_name}' {params.attempt}회 연속 실패.\n\n"
                f"■ 즉시 중단하세요. 같은 방법으로 재시도하지 마세요.\n"
                f"■ 접근 방식을 완전히 변경하세요.\n"
                f"■ 해결이 안 되면 사용자에게 도움을 요청하세요.\n"
                f"■ 해결 후 반드시 gotcha로 저장하세요:"
                f" store(content='...', tags=['gotcha','{params.tool_name}'], importance=9.0)"
                f"{gotcha_section}{circular_warning}{mismatch}"
            )
        elif params.attempt == 2:
            return (
                f"⚠️ '{params.tool_name}' 2회 실패 (에러: {params.error_summary})\n\n"
                f"■ 같은 방법으로 한 번 더 실패하면 STOP됩니다.\n"
                f"■ 접근 방식 변경을 강력히 권장합니다.\n"
                f"■ 아래 관련 기억을 확인하세요."
                f"{gotcha_section}{circular_warning}{mismatch}"
            )
        else:
            return (
                f"📝 '{params.tool_name}' 실패 기록 (에러: {params.error_summary})\n\n"
                f"■ 재시도 전에 아래 관련 기억을 확인하세요."
                f"{gotcha_section}{circular_warning}{mismatch}"
            )

    @mcp.tool(
        name="get_guide",
        annotations={
            "title": "Kandela: Get Guide",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_get_guide(params: Annotated[GuideInput, BeforeValidator(_parse_json_str)]) -> str:
        """Get the canonical memory system guide template for CLAUDE.md.

        Returns the latest versioned guide template with the project ID
        substituted. Use with /kd-init to set up a new project or
        /kd-update to update an existing guide section.

        Args:
            params: GuideInput with project_id.

        Returns:
            Guide template text with version markers.
        """
        logger.info("TOOL kandela:get_guide project=%s", params.project_id)
        from memory_mcp.templates.claude_md_guide import get_guide

        result = get_guide(
            params.project_id,
            tier=None,
            current_version=params.current_version,
        )
        return (
            f"GUIDE_VERSION: {result['version']}\n"
            f"NEEDS_UPDATE: {'true' if result['needs_update'] else 'false'}\n"
            f"---\n"
            f"{result['content']}\n"
            f"\n"
            f"--- REFERENCE FILE (.memory-mcp-guide.md) ---\n"
            f"{result['reference_content']}"
        )

    # ── memory_get_command_prompt ────────────────────────────────

    @mcp.tool(
        name="get_command_prompt",
        annotations={
            "title": "Kandela: Get Command Prompt",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_get_command_prompt(
        params: Annotated[CommandPromptInput, BeforeValidator(_parse_json_str)],
    ) -> str:
        """Get the detailed execution prompt for a kd-* slash command.

        Returns the full instruction set for the specified command.
        The client slash command files are thin stubs that call this
        tool to fetch the actual instructions.

        Args:
            params: CommandPromptInput with command, arguments, project.

        Returns:
            Full command prompt text, or error if command not found.
        """
        logger.info(
            "TOOL kandela:get_command_prompt cmd=%s args=%.40s",
            params.command,
            params.arguments,
        )
        from memory_mcp.templates.command_prompts import get_command_prompt

        result = get_command_prompt(
            command=params.command,
            arguments=params.arguments,
            project=params.project,
        )
        if "error" in result:
            return f"ERROR: {result['error']}"
        return result["content"]

    # ── memory_confirm_change (MCP Elicitation) ─────────────────

    @mcp.tool(
        name="confirm_change",
        annotations={
            "title": "Kandela: Confirm Change",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def memory_confirm_change(
        params: Annotated[ConfirmChangeInput, BeforeValidator(_parse_json_str)],
        ctx: Context,
    ) -> str:
        """Ask the user for confirmation before overriding a previous decision.

        Uses MCP elicitation (if supported by the client) to show the user a
        structured form. If elicitation is not available, returns a text prompt
        that the LLM should relay to the user.

        Args:
            params: ConfirmChangeInput with project, proposed_change, related_memory_id.
            ctx: MCP Context for elicitation access.
        """
        logger.info(
            "TOOL kandela:confirm_change project=%s memory_id=%s",
            params.project, params.related_memory_id,
        )

        # Clear the PreToolUse gate (conflict being addressed)
        # Gate files are session-scoped: /tmp/.prompt-guard-conflict-{session_id}
        import os as _os, glob as _glob
        for _gf in _glob.glob("/tmp/.prompt-guard-conflict-*"):
            try:
                _os.unlink(_gf)
                logger.info("PROMPT_GUARD gate CLEARED: %s", _gf)
            except Exception:
                pass

        # action="keep": user chose to keep the existing decision — just clear gate, done
        if params.action.lower() == "keep":
            logger.info(
                "TOOL kandela:confirm_change action=keep project=%s — gate cleared, memory unchanged",
                params.project,
            )
            return "이전 결정을 유지합니다. 원래 결정에 따라 진행하겠습니다."

        # Load existing memory if ID provided
        existing_content = ""
        if params.related_memory_id:
            store = _get_store()
            try:
                mem = await asyncio.to_thread(
                    store.get_by_id, params.project, params.related_memory_id
                )
                if mem:
                    existing_content = mem.get("content", mem.get("document", ""))
            except Exception:
                logger.debug("confirm_change: failed to load memory %s", params.related_memory_id)

        # Try MCP elicitation if available
        try:
            from mcp.server.elicitation import (  # noqa: I001
                AcceptedElicitation,
                elicit_with_validation,
            )
            from pydantic import BaseModel, Field as PydanticField

            class ConfirmForm(BaseModel):
                decision: str = PydanticField(
                    description=(
                        "Choose: 'keep' to keep current decision, "
                        "'change' to accept the proposed change"
                    ),
                )
                reason: str = PydanticField(
                    default="",
                    description="If changing, explain why (optional but recommended)",
                )

            session = ctx.session
            # Check if client supports elicitation
            client_caps = getattr(session, 'client_capabilities', None)
            elicitation_supported = (
                client_caps is not None
                and getattr(client_caps, 'elicitation', None) is not None
            )

            if elicitation_supported:
                message = f"Proposed change: {params.proposed_change}"
                if existing_content:
                    message = (
                        f"Current decision: {existing_content[:500]}\n\n"
                        f"Proposed change: {params.proposed_change}\n\n"
                        f"Do you want to keep the current decision or accept the change?"
                    )

                result = await elicit_with_validation(
                    session=session,
                    message=message,
                    schema=ConfirmForm,
                )

                if isinstance(result, AcceptedElicitation):
                    data = result.data
                    if data.decision.lower().startswith("keep"):
                        return (
                            "USER DECISION: Keep current decision. "
                            "Do NOT proceed with the proposed change."
                        )
                    else:
                        reason_text = f" Reason: {data.reason}" if data.reason else ""
                        return (
                            f"USER DECISION: Accept change.{reason_text} "
                            f"Proceed with: {params.proposed_change}"
                        )
                else:
                    return (
                        "USER DECISION: Declined/cancelled. "
                        "Do NOT proceed with the proposed change."
                    )

        except Exception as e:
            logger.debug("confirm_change: elicitation not available (%s), using text fallback", e)

        # Fallback: return a text prompt for the LLM to relay
        existing_text = ""
        if existing_content:
            existing_text = (
                f"\n\nCurrent decision "
                f"(memory {params.related_memory_id}):\n"
                f"{existing_content[:500]}"
            )

        return (
            f"⚠️ CONFIRMATION REQUIRED — Decision Change\n"
            f"{existing_text}\n\n"
            f"Proposed change: {params.proposed_change}\n\n"
            f"Please ask the user:\n"
            f"1. **Keep current decision** — do not change\n"
            f"2. **Accept change** — explain why the change is needed\n\n"
            f"Wait for the user's response before proceeding."
        )

    # ── MCP Prompts (L2: workflow templates for non-Claude clients) ──

    @mcp.prompt(
        name="session_start",
        description="Start a memory-aware session. Loads previous context and sets up workflow.",
    )
    def session_start_prompt(project: str) -> str:
        return (
            f"Start a memory-aware session for project '{project}'.\n\n"
            f"Steps:\n"
            f"1. Call auto_recall(project='{project}') to load previous context\n"
            f"2. Review the returned memories before proceeding with any work\n"
            f"3. During this session, store important facts and decisions "
            f"with store\n"
            f"4. At the end of the session, call summarize_session "
            f"with a summary of what was done, decisions made, and next steps"
        )

    @mcp.prompt(
        name="session_end",
        description="End the current session and save a summary for next time.",
    )
    def session_end_prompt(project: str) -> str:
        return (
            f"End the current session for project '{project}'.\n\n"
            f"Call summarize_session with:\n"
            f"- project: '{project}'\n"
            f"- summary: A concise summary covering:\n"
            f"  - What was worked on\n"
            f"  - Key decisions made\n"
            f"  - Problems encountered\n"
            f"  - Next steps for the next session\n"
            f"- tags: relevant tags for this session"
        )

    # ── MCP Resource (L4: always-available guide) ─────────────

    @mcp.resource(
        "memory://guide",
        name="Memory System Guide",
        description="How to use the memory system effectively. "
        "Read this at the start of every session.",
        mime_type="text/markdown",
    )
    def memory_usage_guide() -> str:
        return (
            "# Memory System Usage Guide\n\n"
            "## Session Start\n"
            "Always call `auto_recall` FIRST to load previous context.\n\n"
            "## During Session\n"
            "- Store important facts: `store(memory_type='fact')`\n"
            "- Store decisions: `store(memory_type='decision')`\n"
            "- Store code patterns: `store(memory_type='snippet')`\n\n"
            "## Session End\n"
            "Call `summarize_session` with a summary of what was done,\n"
            "key decisions, problems encountered, and next steps.\n"
        )

    # Register REST API + Dashboard routes
    register_dashboard_routes(mcp, _get_store)

    # Register install endpoint (public, no auth required)
    register_install_routes(mcp)

    # ── Compatibility aliases (transition period — remove in R-9) ──
    _COMPAT_ALIASES = {
        "memory_store": memory_store,
        "memory_search": memory_search,
        "memory_context_search": memory_context_search,
        "memory_delete": memory_delete,
        "memory_update": memory_update,
        "memory_summarize_session": memory_summarize_session,
        "memory_list_projects": memory_list_projects,
        "memory_stats": memory_stats,
        "memory_auto_recall": memory_auto_recall,
        "memory_project_rename": memory_project_rename,
        "memory_project_delete": memory_project_delete,
        "memory_inbox": memory_inbox,
        "memory_infra_update": memory_infra_update,
        "memory_infra_get": memory_infra_get,
        "memory_progress_update": memory_progress_update,
        "memory_progress_get": memory_progress_get,
        "memory_checklist_add": memory_checklist_add,
        "memory_checklist_get": memory_checklist_get,
        "memory_checklist_done": memory_checklist_done,
        "memory_report_failure": memory_report_failure,
        "memory_get_guide": memory_get_guide,
        "memory_get_command_prompt": memory_get_command_prompt,
        "memory_confirm_change": memory_confirm_change,
    }
    for _old_name, _new_func in _COMPAT_ALIASES.items():
        mcp.tool(name=_old_name, description=f"[Deprecated] Use '{_new_func.__name__}' instead.")(_new_func)

    # Inline $defs/$ref in tool schemas for Gemini/cross-client compatibility
    dereference_tool_schemas(mcp._tool_manager)

    return mcp
