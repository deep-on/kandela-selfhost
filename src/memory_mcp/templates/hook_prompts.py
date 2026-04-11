"""Server-side hook evaluation logic and prompt templates.

Moves IP-sensitive patterns (danger command regex, adaptive intervals,
error tracking) from client-side bash hooks to the server.

Client hooks become thin wrappers that POST raw data and print the response.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Danger Command Patterns (IP-sensitive) ──────────────────────

_RESTART_PATTERN = re.compile(
    # uvicorn/gunicorn as a launcher (must be at command start, after nohup/python)
    r"(?:^|&&|;|\|\|)\s*(?:nohup\s+)?(?:python[0-9.]?\s+(?:-m\s+)?)?(?:uvicorn|gunicorn)\s+\S"
    r"|(?:pkill|killall)\s+(?:-\S+\s+)*(?:uvicorn|gunicorn)"
    r"|kill\b.*\buvicorn\b"
    r"|systemctl\s+(restart|start)|service\s+\S+\s+(restart|start)"
    r"|docker\s+(restart|compose\s*.*up|compose\s*.*restart)"
    r"|supervisorctl\s+(restart|start)|pm2\s+(restart|start)"
    r"|nginx\s+-s\s+reload|nohup.*python.*run",
    re.IGNORECASE,
)

_DESTRUCTIVE_PATTERN = re.compile(
    r"rm\s+-rf\s+/|rm\s+-rf\s+\."
    r"|DROP\s+TABLE|DROP\s+DATABASE|TRUNCATE\s"
    r"|DELETE\s+FROM\s+.*\s+WHERE\s+1"
    r"|git\s+push.*--force(?:[^-]|$)|git\s+reset\s+--hard|git\s+clean\s+-fd"
    r"|docker\s+system\s+prune|docker\s+volume\s+(rm|prune)"
    r"|format\s+/|mkfs\.|dd\s+if=",
    re.IGNORECASE,
)

_DEPLOY_PATTERN = re.compile(
    r"kubectl\s+apply|terraform\s+apply|ansible-playbook"
    r"|docker.*push|helm\s+(install|upgrade)"
    r"|aws\s+\S+\s+deploy|gcloud\s+\S+\s+deploy|fly\s+deploy"
    r"|cap.*deploy",
    re.IGNORECASE,
)


def classify_danger(command: str) -> str | None:
    """Classify a bash command's danger type.

    Returns 'restart', 'destructive', 'deploy', 'no_deps_missing', or None.
    """
    # Check --no-deps missing BEFORE general restart pattern
    # docker compose up without --no-deps is a specific gotcha
    is_compose_up = re.search(r"docker\s+compose\s+.*up\s", command, re.IGNORECASE)
    if is_compose_up:
        if "--no-deps" not in command:
            return "no_deps_missing"
        # --no-deps present → safe, skip restart classification for compose up
    elif _RESTART_PATTERN.search(command):
        return "restart"
    if _DESTRUCTIVE_PATTERN.search(command):
        return "destructive"
    if _DEPLOY_PATTERN.search(command):
        return "deploy"
    return None


# ── Adaptive Interval Logic ─────────────────────────────────────

def compute_interval(pct: int) -> int:
    """Return check interval in seconds based on context usage %."""
    if pct < 50:
        return 120
    if pct < 70:
        return 60
    if pct < 85:
        return 30
    return 10


# ── Error Tracking (in-memory, per-project) ─────────────────────

# { project -> [(signature, timestamp), ...] }
_error_history: dict[str, list[tuple[str, float]]] = {}
_error_lock = threading.Lock()
_ERROR_WINDOW = 1800  # 30 minutes
_ERROR_THRESHOLD = 3


def track_error(project: str, signature: str) -> int:
    """Record an error and return the count within the window.

    Also prunes entries older than the error window.
    """
    now = time.time()
    cutoff = now - _ERROR_WINDOW

    with _error_lock:
        history = _error_history.setdefault(project, [])
        # Prune old entries
        history[:] = [(sig, ts) for sig, ts in history if ts >= cutoff]
        # Add new
        history.append((signature, now))
        # Count matching
        return sum(1 for sig, _ts in history if sig == signature)


def clear_error_signature(project: str, signature: str) -> None:
    """Clear tracked errors for a specific signature after warning."""
    with _error_lock:
        history = _error_history.get(project, [])
        _error_history[project] = [
            (sig, ts) for sig, ts in history if sig != signature
        ]


# ── Build/Env Tool Failure Detection ────────────────────────────

_ENV_TOOL_PATTERN = re.compile(
    r"\b(java(?:c)?|gradle(?:w)?|mvn|adb|npm|yarn|pnpm|node(?:js)?|"
    r"pip3?|python3?|ruby|gem|bundle|cargo|rustc|go|flutter|dart|"
    r"xcodebuild|swift|pod|make|cmake|bazel|dotnet|msbuild)\b",
    re.IGNORECASE,
)

# Maps tool name → search keywords for env-path memory lookup
_ENV_TOOL_SEARCH: dict[str, str] = {
    "java": "java JAVA_HOME jdk",
    "javac": "java JAVA_HOME jdk",
    "gradle": "gradle JAVA_HOME android build",
    "gradlew": "gradle JAVA_HOME android build",
    "mvn": "maven JAVA_HOME build",
    "adb": "adb android sdk ANDROID_HOME",
    "npm": "npm node nodejs PATH",
    "yarn": "yarn node nodejs PATH",
    "pnpm": "pnpm node nodejs PATH",
    "node": "node nodejs nvm PATH",
    "nodejs": "node nodejs nvm PATH",
    "pip": "pip python env",
    "pip3": "pip python env",
    "python": "python PYTHONPATH env",
    "python3": "python PYTHONPATH env",
    "ruby": "ruby gem rbenv PATH",
    "gem": "ruby gem rbenv PATH",
    "bundle": "ruby bundler gem",
    "cargo": "cargo rust RUST_HOME",
    "rustc": "rust RUST_HOME PATH",
    "go": "go GOPATH GOROOT PATH",
    "flutter": "flutter dart sdk PATH",
    "dart": "dart flutter PATH",
    "xcodebuild": "xcode xcodebuild ios",
    "swift": "swift xcode PATH",
    "pod": "cocoapods pod ruby",
    "make": "make build PATH",
    "cmake": "cmake build",
    "bazel": "bazel build",
    "dotnet": "dotnet .NET PATH",
    "msbuild": "msbuild .NET PATH",
}


def classify_env_failure(command: str, exit_code: int | None) -> str | None:
    """Return search keywords if a build/env tool failed (exit_code != 0).

    Returns None if exit_code is 0/None or command is not a build/env tool.
    """
    if exit_code is None or exit_code == 0:
        return None
    m = _ENV_TOOL_PATTERN.search(command)
    if not m:
        return None
    tool = m.group(1).lower()
    return _ENV_TOOL_SEARCH.get(tool, f"{tool} env path PATH")


# ── Injection Tracking + Utilization (MA-3) ──────────────────────

from collections import deque
from dataclasses import dataclass, field

# In-memory injection registry for real-time resolution
_injection_registry: dict[str, deque[Any]] = {}
_injection_lock = threading.Lock()
_INJECTION_MAXLEN = 100
_INJECTION_RESOLVE_WINDOW = 60  # seconds to match action to injection
_INJECTION_EXPIRE = 300  # 5 min expiry for unresolved events

# Optional: persistent store (set by dashboard at startup)
_utilization_store: Any = None


def set_utilization_store(store: Any) -> None:
    """Set the persistent UtilizationStore instance (called at startup)."""
    global _utilization_store
    _utilization_store = store


@dataclass
class _InjectionEvent:
    memory_ids: list[str]
    injection_type: str
    context: str
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False
    utilized: bool | None = None


def track_injection(
    project: str,
    memory_ids: list[str],
    injection_type: str,
    context: str,
) -> None:
    """Record a gotcha injection event (in-memory + persistent)."""
    evt = _InjectionEvent(
        memory_ids=memory_ids,
        injection_type=injection_type,
        context=context[:200],
    )
    with _injection_lock:
        buf = _injection_registry.setdefault(
            project, deque(maxlen=_INJECTION_MAXLEN),
        )
        buf.append(evt)

    # Persist to SQLite if available
    if _utilization_store is not None:
        try:
            _utilization_store.record_injection(
                project, memory_ids, injection_type, context,
            )
        except Exception:
            logger.debug("Failed to persist injection event for project=%s", project)


def check_injection_utilization(
    project: str,
    command: str,
    exit_code: int | None,
) -> None:
    """Check if a recent injection was utilized or violated.

    Called after each Bash execution in the context-monitor flow.
    Heuristic:
      - gotcha injected + similar command + failure → utilized=False
      - gotcha injected + no similar failure within window → utilized=True (on expiry)
    """
    if not command:
        return

    now = time.time()
    with _injection_lock:
        buf = _injection_registry.get(project)
        if not buf:
            return
        # Copy unresolved events for processing
        pending = [e for e in buf if not e.resolved
                   and (now - e.timestamp) < _INJECTION_EXPIRE]

    if not pending:
        return

    # Simple heuristic: check if command tokens overlap with injection context
    cmd_tokens = set(command.lower().split()[:20])
    for evt in pending:
        ctx_tokens = set(evt.context.lower().split()[:20])
        overlap = len(cmd_tokens & ctx_tokens)
        if overlap < 2:
            continue

        # Overlapping context found — check outcome
        if exit_code is not None and exit_code != 0:
            # Command failed → gotcha was ignored
            evt.resolved = True
            evt.utilized = False
            _persist_resolution(project, evt.memory_ids, utilized=False)
        elif (now - evt.timestamp) > _INJECTION_RESOLVE_WINDOW:
            # Enough time passed without failure → assume utilized
            evt.resolved = True
            evt.utilized = True
            _persist_resolution(project, evt.memory_ids, utilized=True)


def _persist_resolution(
    project: str, memory_ids: list[str], utilized: bool,
) -> None:
    """Persist resolution to SQLite."""
    if _utilization_store is None:
        return
    for mid in memory_ids:
        try:
            _utilization_store.resolve_event(project, mid, utilized)
        except Exception:
            pass


# ── Topic Buffer + Milestone Re-injection (MA-2) ─────────────────

# Rolling buffer of recent commands per project (for milestone topic extraction)
_topic_buffer: dict[str, deque[str]] = {}
_topic_lock = threading.Lock()
_TOPIC_BUFFER_MAXLEN = 20

# Milestone thresholds (context usage %)
_MILESTONES = [(0, 30), (1, 50), (2, 70)]


def append_topic(project: str, command: str) -> None:
    """Append a command snippet to the project's rolling topic buffer."""
    if not command or not command.strip():
        return
    snippet = command.strip()[:100]
    with _topic_lock:
        buf = _topic_buffer.setdefault(
            project, deque(maxlen=_TOPIC_BUFFER_MAXLEN),
        )
        buf.append(snippet)


def get_topic_summary(project: str) -> str:
    """Build a search query from the recent topic buffer.

    Concatenates recent command snippets into a single query string
    suitable for BM25 gotcha matching.
    """
    with _topic_lock:
        buf = _topic_buffer.get(project)
        if not buf:
            return ""
    # Join last 10 unique snippets (dedup while preserving order)
    seen: set[str] = set()
    parts: list[str] = []
    for s in reversed(buf):
        if s not in seen:
            seen.add(s)
            parts.append(s)
        if len(parts) >= 10:
            break
    return " ".join(reversed(parts))


def check_milestones(
    pct: int,
    milestones_hit: int,
) -> tuple[int, list[int]]:
    """Check which milestones are newly crossed.

    Args:
        pct: Current context usage percentage.
        milestones_hit: Bitmask of previously hit milestones.

    Returns:
        (updated_bitmask, list_of_newly_crossed_thresholds)
    """
    new_mask = milestones_hit
    newly_crossed: list[int] = []
    for bit, threshold in _MILESTONES:
        if pct >= threshold and not (milestones_hit & (1 << bit)):
            new_mask |= (1 << bit)
            newly_crossed.append(threshold)
    return new_mask, newly_crossed


def format_milestone_injection(
    project: str,
    pct: int,
    matches: list[dict[str, Any]],
) -> str:
    """Format gotcha re-injection for a context milestone."""
    lines = [f"[Kandela — Memory Refresh ({pct}%)] Project: {project}"]
    lines.append("현재 작업과 관련된 주의사항:")
    for i, m in enumerate(matches, 1):
        content = m.get("content", m.get("document", ""))[:200]
        imp = m.get("metadata", {}).get("importance", "?")
        lines.append(f"  {i}. (imp:{imp}) {content}")
    return "\n".join(lines)


def clear_topic_buffer(project: str | None = None) -> None:
    """Clear topic buffer for a project (or all)."""
    with _topic_lock:
        if project is None:
            _topic_buffer.clear()
        else:
            _topic_buffer.pop(project, None)


# ── Context Monitor Evaluation ──────────────────────────────────

def evaluate_context_monitor(
    *,
    project: str,
    tool_name: str,
    command: str = "",
    exit_code: int | None = None,
    input_tokens: int | None = None,
    ctx_limit: int = 200000,
    last_check_ts: float = 0,
    interval: int = 120,
    warned: bool = False,
    tool_call_count: int = 0,
    session_bloat_warned: bool = False,
) -> dict[str, Any]:
    """Evaluate PostToolUse hook data and return action instructions.

    Returns dict with:
        output: str — text to print (empty = nothing)
        next_interval: int — seconds until next check
        warned: bool — updated warned state
        should_check_context: bool — whether interval has elapsed
        session_bloat_warned: bool — whether session bloat warning was issued
    """
    now = time.time()
    outputs: list[str] = []
    warn_type: str | None = None

    # [1] Bash danger detection
    if tool_name == "Bash" and command:
        warn_type = classify_danger(command)

    # [CB-2] Build/env tool failure → env-path gotcha injection (first failure)
    env_fail_keywords: str | None = None
    if tool_name == "Bash" and exit_code is not None and exit_code != 0 and command:
        env_fail_keywords = classify_env_failure(command, exit_code)

    # [P4] Repeated failure detection
    err_count = 0
    if tool_name == "Bash" and exit_code is not None and exit_code != 0 and command:
        # Use first 80 chars as signature
        sig = command[:80]
        err_count = track_error(project, sig)
        if err_count >= _ERROR_THRESHOLD:
            cmd_preview = command[:40]
            outputs.append("")
            outputs.append(
                f"[Kandela — 반복 실패 감지] 같은 명령이 {err_count}회 실패했습니다."
            )
            outputs.append(
                f"1. context_search(project='{project}', "
                f"query='{cmd_preview} gotcha')로 해결책을 검색하세요."
            )
            outputs.append(
                f"2. 해결 후 store(project='{project}', content='...', "
                "memory_type='fact', tags=['gotcha','repeated-failure'], "
                "importance=9.0)로 저장하세요."
            )
            clear_error_signature(project, sig)

    # [2] Context usage monitoring
    elapsed = now - last_check_ts
    should_check = elapsed >= interval

    next_interval = interval
    new_warned = warned

    if should_check and input_tokens is not None:
        pct = int(input_tokens * 100 / ctx_limit) if ctx_limit > 0 else 0
        next_interval = compute_interval(pct)

        if pct >= 85 and not warned:
            new_warned = True
            # Context warning will be added by the hook via
            # /api/hook-prompt/pre-compact (already serverized)
    elif should_check:
        next_interval = 120

    # [3] Session bloat detection — JNL file grows ~2KB per tool call
    # At 500 calls (~50MB+ JNL), warn and request checkpoint
    new_session_bloat_warned = session_bloat_warned
    _BLOAT_WARN_THRESHOLD = 500
    _BLOAT_CRITICAL_THRESHOLD = 800

    if tool_call_count >= _BLOAT_WARN_THRESHOLD and not session_bloat_warned:
        new_session_bloat_warned = True
        outputs.append("")
        outputs.append(
            f"[Kandela — 세션 비대 경고] 도구 호출 {tool_call_count}회 도달. "
            "JNL 파일이 비대해져 compaction 실패 위험이 있습니다."
        )
        outputs.append(
            f"1. summarize_session(project='{project}')으로 "
            "현재 작업을 체크포인트하세요."
        )
        outputs.append(
            "2. 체크포인트 후 새 세션을 시작하면 기억이 자동 복구됩니다."
        )
    elif tool_call_count >= _BLOAT_CRITICAL_THRESHOLD and session_bloat_warned:
        # Second warning at critical threshold
        outputs.append("")
        outputs.append(
            f"[Kandela — 세션 비대 위험] 도구 호출 {tool_call_count}회. "
            "지금 세션을 저장하고 재시작을 강력히 권장합니다. "
            "compaction 실패 시 작업 내용을 잃을 수 있습니다."
        )

    return {
        "warn_type": warn_type,
        "env_fail_keywords": env_fail_keywords,
        "output": "\n".join(outputs) if outputs else "",
        "next_interval": next_interval,
        "warned": new_warned,
        "err_count": err_count,
        "now": now,
        "should_check_context": should_check,
        "session_bloat_warned": new_session_bloat_warned,
    }


# ── Session Start Evaluation ────────────────────────────────────

def match_workspace(
    cwd: str,
    workspaces: dict[str, str],
) -> list[tuple[str, str]]:
    """Match CWD against registered workspaces.

    Priority: exact match > child of workspace (longest prefix) > parent.

    Returns list of (project_id, workspace_path) matches.
    Single-element list for exact/child, multiple for parent ambiguity.
    """
    import os

    cwd = os.path.normpath(cwd)

    # Priority 1: exact match
    for pid, wpath in workspaces.items():
        if cwd == os.path.normpath(wpath):
            return [(pid, wpath)]

    # Priority 2: CWD is child of workspace (longest prefix)
    children: list[tuple[int, str, str]] = []
    for pid, wpath in workspaces.items():
        nw = os.path.normpath(wpath)
        if cwd.startswith(nw + os.sep):
            children.append((len(nw), pid, wpath))
    if children:
        children.sort(reverse=True)
        _, pid, wpath = children[0]
        return [(pid, wpath)]

    # Priority 3: CWD is parent of workspace
    parents: list[tuple[int, str, str]] = []
    for pid, wpath in workspaces.items():
        nw = os.path.normpath(wpath)
        if nw.startswith(cwd + os.sep):
            parents.append((len(nw), pid, wpath))
    if len(parents) == 1:
        _, pid, wpath = parents[0]
        return [(pid, wpath)]
    if len(parents) > 1:
        return [(pid, wpath) for _, pid, wpath in sorted(parents)]

    return []


def evaluate_session_start(
    *,
    cwd: str,
    hostname: str,
    workspaces: dict[str, str],
    server_guide_version: int,
    server_install_version: int,
    local_guide_version: int | None = None,
    local_install_version: int | None = None,
) -> dict[str, Any]:
    """Evaluate SessionStart hook data and return prompt.

    Returns dict with:
        matched: bool — whether a project was found
        project_id: str — matched project (empty if ambiguous/none)
        multi_match: list — multiple project matches (for ambiguity)
        prompt: str — text to output
        update_hints: list[str] — version update messages
    """
    matches = match_workspace(cwd, workspaces)
    update_hints: list[str] = []

    if not matches:
        return {
            "matched": False,
            "project_id": "",
            "multi_match": [],
            "prompt": "",
            "update_hints": [],
        }

    # Version comparison
    if local_guide_version is not None and server_guide_version > local_guide_version:
        update_hints.append(
            f"⬆️ 가이드 업데이트 필요 (v{local_guide_version} → "
            f"v{server_guide_version}): /kd-update 실행"
        )
    if local_install_version is not None and server_install_version > local_install_version:
        update_hints.append(
            f"⬆️ 명령어/Hook 업데이트 필요 (v{local_install_version} → "
            f"v{server_install_version}): dm-install 실행"
        )

    # Multiple matches — ambiguous
    if len(matches) > 1:
        lines = [f"[Memory] 여러 프로젝트 감지됨 (CWD: {cwd}):"]
        for pid, wpath in matches:
            lines.append(f"  - {pid} ({wpath}/CLAUDE.md)")
        lines.append(
            "CLAUDE.md의 'memory project ID:'와 일치하는 프로젝트로 "
            "auto_recall을 실행하세요:"
        )
        lines.append(
            f"→ auto_recall(project='해당_프로젝트_ID', mode='brief', "
            f"environment={{cwd: '{cwd}', hostname: '{hostname}'}})"
        )
        lines.append("대화 중 context_search(query='주제')로 필요한 기억을 검색.")
        return {
            "matched": True,
            "project_id": "",
            "multi_match": [{"project": p, "path": w} for p, w in matches],
            "prompt": "\n".join(lines),
            "update_hints": update_hints,
        }

    # Single match
    pid, _wpath = matches[0]
    hint_text = ""
    if update_hints:
        hint_text = "\n" + "\n".join(update_hints)

    prompt = (
        f"[Memory] Project: {pid}\n"
        f"→ auto_recall(project='{pid}', mode='brief', "
        f"environment={{cwd: '{cwd}', hostname: '{hostname}'}})\n"
        f"대화 중 context_search(query='주제')로 필요한 기억을 검색."
        f"{hint_text}"
    )

    return {
        "matched": True,
        "project_id": pid,
        "multi_match": [],
        "prompt": prompt,
        "update_hints": update_hints,
    }


# ── Gotcha Cache + Semantic PreToolUse Matching (MA-1) ────────

# In-memory cache: project -> (timestamp, BM25Index | None, raw gotchas)
_gotcha_cache: dict[str, tuple[float, Any, list[dict[str, Any]]]] = {}
_GOTCHA_CACHE_TTL = 300  # 5 minutes
_gotcha_lock = threading.Lock()
_GOTCHA_MATCH_SCORE_THRESHOLD = 2.0


def _get_cached_gotchas(
    project: str,
    search_fn: Callable[..., list[dict[str, Any]]],
) -> tuple[Any, list[dict[str, Any]]]:
    """Get gotcha BM25 index for a project, using cache when available.

    Args:
        project: Project ID.
        search_fn: Callable matching MemoryStore.search signature.

    Returns:
        (MemoryBM25Index or None, list of raw gotcha dicts)
    """
    now = time.time()
    with _gotcha_lock:
        cached = _gotcha_cache.get(project)
        if cached is not None:
            ts, bm25_idx, gotchas = cached
            if now - ts < _GOTCHA_CACHE_TTL:
                return bm25_idx, gotchas

    # Fetch gotchas from store (outside lock to avoid blocking)
    gotchas: list[dict[str, Any]] = []
    try:
        project_gotchas = search_fn(
            query="gotcha 주의 실패 에러 warning 금지",
            project=project,
            n_results=50,
            tags=["gotcha"],
            importance_min=7.0,
            use_hybrid=True,
        )
        gotchas.extend(project_gotchas)
    except Exception:
        logger.debug("gotcha search failed for project=%s", project)

    # Also fetch global gotchas
    try:
        global_gotchas = search_fn(
            query="gotcha 주의 실패 에러 warning 금지",
            project="_global",
            n_results=10,
            tags=["gotcha"],
            importance_min=8.0,
            use_hybrid=True,
        )
        seen_ids = {g.get("id") for g in gotchas}
        for g in global_gotchas:
            if g.get("id") not in seen_ids:
                gotchas.append(g)
    except Exception:
        pass

    # Build BM25 index
    bm25_idx = None
    if gotchas:
        try:
            from memory_mcp.db.bm25 import MemoryBM25Index

            docs = [g.get("document", g.get("content", "")) for g in gotchas]
            ids = [g.get("id", str(i)) for i, g in enumerate(gotchas)]
            metas = [g.get("metadata", {}) for g in gotchas]
            bm25_idx = MemoryBM25Index(docs, ids, metas)
        except Exception:
            logger.debug("BM25 index build failed for gotchas, project=%s", project)

    with _gotcha_lock:
        _gotcha_cache[project] = (time.time(), bm25_idx, gotchas)

    logger.debug("Cached %d gotchas for project=%s", len(gotchas), project)
    return bm25_idx, gotchas


# Prompt keyword expansion: bridge Korean operation terms → English technical terms
# Solves G2 (gotcha not referenced): BM25 can't match "배포" to "docker compose --no-deps"
_PROMPT_EXPANSION: dict[str, str] = {
    "배포": "배포 deploy docker compose up --no-deps 서버 반영",
    "deploy": "deploy 배포 docker compose up --no-deps",
    "재시작": "재시작 restart docker restart 서비스 재기동",
    "restart": "restart 재시작 docker restart",
    "마이그레이션": "마이그레이션 migrate migration 데이터베이스 DB",
    "프로덕션": "프로덕션 production 서버 배포 deploy",
    "올려": "배포 deploy docker compose up 서버 반영",
    "반영": "배포 deploy docker compose up 서버 반영",
}


def match_gotchas_for_command(
    project: str,
    command: str,
    search_fn: Callable[..., list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Match a command against cached project gotchas using BM25.

    Returns list of matching gotcha dicts (with 'score' added), empty if none.
    Fast path: <1ms when cache is warm.

    Applies prompt keyword expansion to bridge Korean↔English term gaps
    (e.g., "배포" → "deploy docker compose --no-deps").
    """
    if not command or not command.strip():
        return []

    # Expand prompt keywords for better BM25 matching (G2 fix)
    expanded = command
    cmd_lower = command.lower()
    for keyword, expansion in _PROMPT_EXPANSION.items():
        if keyword in cmd_lower:
            expanded = f"{command} {expansion}"
            break  # first match only — multi-keyword tested, break OK

    bm25_idx, _raw = _get_cached_gotchas(project, search_fn)
    if bm25_idx is None:
        return []

    try:
        results = bm25_idx.search(expanded[:300], n_results=3)
    except Exception:
        logger.debug("BM25 search failed for command in project=%s", project)
        return []

    return [r for r in results if r.get("score", 0) >= _GOTCHA_MATCH_SCORE_THRESHOLD]


def format_gotcha_warning(
    project: str,
    matches: list[dict[str, Any]],
) -> str:
    """Format matched gotchas as a pre-tool warning message."""
    lines = [f"[Kandela — Gotcha Match] Project: {project}"]
    lines.append("이 명령과 관련된 주의사항이 있습니다:")
    lines.append("")
    for i, m in enumerate(matches, 1):
        content = m.get("content", m.get("document", ""))[:200]
        imp = m.get("metadata", {}).get("importance", "?")
        lines.append(f"  {i}. (imp:{imp}) {content}")
    return "\n".join(lines)


def invalidate_gotcha_cache(project: str | None = None) -> None:
    """Invalidate gotcha cache for a project (or all projects)."""
    with _gotcha_lock:
        if project is None:
            _gotcha_cache.clear()
        else:
            _gotcha_cache.pop(project, None)


# ── Build Warn (file edit → build rule matching) ──────────────

# Regex to extract glob-style extension patterns like *.html, *.js
_EXT_PATTERN = re.compile(r"\*\.(\w+)")

# In-memory cache: project -> (timestamp, rules)
# Each rule: {"content": str, "extensions": set[str]}
_build_rule_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BUILD_RULE_CACHE_TTL = 300  # 5 minutes


def _extract_extensions(content: str) -> set[str]:
    """Extract file extensions from build-rule content (e.g. *.html -> html)."""
    return set(_EXT_PATTERN.findall(content))


def _get_cached_build_rules(
    project: str,
    search_fn: Any,
) -> list[dict[str, Any]]:
    """Get build rules for a project, using cache when available.

    Args:
        project: Project ID.
        search_fn: Callable that takes (query, project, **kwargs) and returns
                   a list of memory dicts. Typically ``store.search``.

    Returns:
        List of dicts with 'content' and 'extensions' keys.
    """
    now = time.time()
    cached = _build_rule_cache.get(project)
    if cached is not None:
        ts, rules = cached
        if now - ts < _BUILD_RULE_CACHE_TTL:
            return rules

    # Search for build-rule tagged memories
    try:
        results = search_fn(
            query="빌드 규칙 build rule",
            project=project,
            n_results=20,
            tags=["build-rule"],
            use_hybrid=True,
        )
    except Exception:
        logger.debug("build-rule search failed for project=%s", project)
        _build_rule_cache[project] = (now, [])
        return []

    rules: list[dict[str, Any]] = []
    for mem in results:
        content = mem.get("document", mem.get("content", ""))
        extensions = _extract_extensions(content)
        if extensions:
            rules.append({"content": content, "extensions": extensions})

    _build_rule_cache[project] = (now, rules)
    logger.debug(
        "Cached %d build rules for project=%s", len(rules), project,
    )
    return rules


def invalidate_build_rule_cache(project: str | None = None) -> None:
    """Invalidate build-rule cache for a project (or all projects)."""
    if project is None:
        _build_rule_cache.clear()
    else:
        _build_rule_cache.pop(project, None)


def evaluate_build_warn(
    tool_name: str,
    file_path: str,
    project: str,
    search_fn: Any,
) -> str | None:
    """Check if a file edit triggers a build rule and return a prompt.

    Args:
        tool_name: MCP tool name ("Edit" or "Write").
        file_path: Absolute path of the modified file.
        project: Project ID.
        search_fn: Callable for searching memories (store.search).

    Returns:
        Warning prompt string if a build rule matches, None otherwise.
    """
    if tool_name not in ("Edit", "Write"):
        return None

    # Extract extension from file path
    _, ext_with_dot = os.path.splitext(file_path)
    if not ext_with_dot:
        return None
    ext = ext_with_dot.lstrip(".")

    rules = _get_cached_build_rules(project, search_fn)
    if not rules:
        return None

    # Match extension against rules
    for rule in rules:
        if ext in rule["extensions"]:
            filename = os.path.basename(file_path)
            return (
                f"\u26a0\ufe0f Build Required: {filename} 수정됨.\n"
                f"{rule['content']}"
            )

    return None


# ── Prompt Guard (UserPromptSubmit) ────────────────────────────

# Change-intent keywords — when present, search memory for related decisions
_CHANGE_KEYWORDS_EN = re.compile(
    # Explicit change verbs
    r"\b(switch(?:ing)?|change|replace|replac(?:e|ing)|"
    r"migrat(?:e|ing)|mov(?:e|ing)\s+to|"
    r"lower|reduce|increase|remove|delete|drop|disable|deprecate|"
    r"upgrade|downgrade|revert|roll\s*back|"
    r"swap|get\s+rid\s+of|stop\s+using|"
    r"instead\s+of|no\s+longer\s+use|"
    # Refactoring / rewriting
    r"refactor|rework|rewrite|rearchitect|"
    # Indirect change intent (suggestions, comparisons)
    r"(?:can|could|should|why\s+not|why\s+don.t)\s+we\s+(?:just\s+)?use|"
    r"let.s\s+(?:just\s+)?use|let.s\s+add|"
    r"simpler|too\s+complex|overcomplicat|overkill|excessive|wasteful|"
    r"rather\s+than|prefer\s+(?:to|using)|better\s+to|"
    r"not\s+(?:need|necessary)|do\s+we\s+(?:really\s+)?need|"
    # Extend / normalize / alongside
    r"extend\s+(?:the|to|session)|normalize|alongside|"
    r"add\s+(?:redis|cache|caching))\b",
    re.IGNORECASE,
)

_CHANGE_KEYWORDS_KO = re.compile(
    r"(바꾸|변경|교체|전환|마이그레이션|줄이|늘리|삭제|제거|비활성|폐기|"
    r"업그레이드|다운그레이드|롤백|되돌|대신|그만)",
)

# Stop-words to filter from topic extraction
_STOP_WORDS = frozenset({
    "the", "a", "an", "to", "from", "with", "for", "of", "in", "on",
    "is", "it", "we", "us", "our", "can", "could", "should", "would",
    "let", "lets", "let's", "do", "did", "does", "will", "shall",
    "that", "this", "those", "these", "and", "or", "but", "not",
    "be", "been", "being", "have", "has", "had", "was", "were", "are",
    "i", "you", "he", "she", "they", "my", "your", "his", "her", "its",
    "want", "need", "think", "know", "like", "use", "using", "get",
    "make", "go", "going", "try", "about", "into", "out", "up",
    "also", "just", "now", "how", "what", "why", "when", "where",
    "some", "any", "all", "each", "every", "much", "more", "less",
    # Change keywords themselves (already detected, don't include as topic)
    "switch", "switching", "change", "replace", "replacing", "migrate",
    "migrating", "move", "moving", "lower", "reduce", "increase",
    "remove", "delete", "drop", "disable", "deprecate", "upgrade",
    "downgrade", "revert", "rollback", "swap", "stop", "instead",
    "longer", "rid",
})


def detect_change_intent(prompt: str) -> bool:
    """Return True if the prompt contains change-related keywords."""
    return bool(
        _CHANGE_KEYWORDS_EN.search(prompt) or _CHANGE_KEYWORDS_KO.search(prompt)
    )


def extract_topics(prompt: str, max_topics: int = 5) -> list[str]:
    """Extract topic keywords from a prompt for memory search.

    Strips stop-words and change keywords, returns the most relevant
    tokens (favoring longer / more specific words).
    """
    # Remove markdown, URLs, code blocks
    cleaned = re.sub(r"```[\s\S]*?```", " ", prompt)
    cleaned = re.sub(r"`[^`]+`", " ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)

    # Tokenize: keep alphanumeric, underscores, hyphens, Korean chars
    tokens = re.findall(r"[a-zA-Z0-9_\-]+|[\uac00-\ud7af]+", cleaned)

    # Filter stop words and very short tokens
    filtered: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        lower = tok.lower()
        if lower in _STOP_WORDS:
            continue
        if len(tok) < 2:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        filtered.append(tok)

    # Sort by length descending (longer words are more specific/useful)
    filtered.sort(key=len, reverse=True)
    return filtered[:max_topics]


# Guard level thresholds: importance minimum to trigger block
# "explore": notify only, never block (차단 없이 알림만)
GUARD_LEVELS = {
    "strong": 5.0,   # 강: importance ≥ 5.0이면 차단 (거의 모든 결정 보호)
    "medium": 8.0,   # 중: importance ≥ 8.0이면 차단 (핵심 결정만 보호, 기본값)
    "weak": 9.5,     # 약: importance ≥ 9.5이면 차단 (치명적 결정만 보호)
    "explore": 999.0,  # 탐색: 실질적으로 차단 없음 (알림만)
}


def _relative_time(created_ts: object) -> str:
    """Convert a Unix timestamp to a human-readable relative time string."""
    import time as _time
    try:
        ts = float(created_ts)
        diff = _time.time() - ts
        if diff < 60:
            return "방금"
        elif diff < 3600:
            return f"{int(diff // 60)}분 전"
        elif diff < 86400:
            return f"{int(diff // 3600)}시간 전"
        elif diff < 86400 * 7:
            return f"{int(diff // 86400)}일 전"
        elif diff < 86400 * 30:
            return f"{int(diff // (86400 * 7))}주 전"
        elif diff < 86400 * 365:
            return f"{int(diff // (86400 * 30))}개월 전"
        else:
            return f"{int(diff // (86400 * 365))}년 전"
    except Exception:
        return ""


def _format_guard_block(
    top_content: str,
    top_imp: object,
    mem_lines: list[str],
    guard_tone: str,
    guard_mode: str,
    project: str = "",
    top_memory_id: str = "",
    top_created_ts: object = None,
) -> str:
    """Format the blocking conflict message according to guard_tone.

    ask mode: compact + Claude instructions for 응/아니 two-way flow.
    auto mode: auto-keep message (no user question).
    """
    # Helper: Claude instruction for the two-way response flow
    def _two_way_instruction(user_msg: str) -> str:
        mem_id_part = f', related_memory_id="{top_memory_id}"' if top_memory_id else ""
        return (
            f"<user-prompt-submit-hook>\n"
            f"{user_msg}\n"
            f"사용자 응답 처리:\n"
            f'- 변경 의사("응"/"바꿀게"/"맞아" 등) → '
            f'confirm_change(project="{project}", proposed_change="변경"{mem_id_part}, action="change") 후 이유도 받아 기억에 포함\n'
            f'- 유지 의사("아니"/"그대로"/"알겠어" 등) → '
            f'confirm_change(project="{project}", proposed_change="", action="keep") 후 원래 결정 존중하며 진행\n'
            f"</user-prompt-submit-hook>"
        )

    # 날짜 표시 (있을 때만)
    age = _relative_time(top_created_ts) if top_created_ts else ""
    age_tag = f", {age}" if age else ""

    if guard_tone == "brief":
        brief = top_content[:80]
        if guard_mode == "auto":
            return f"⚠️ 이전 결정: {brief} (imp:{top_imp}{age_tag})\n자동 유지됩니다."
        user_msg = f"⚠️ 이전 결정: {brief} (imp:{top_imp}{age_tag})\n바꿀까요, 그대로 갈까요?"
        return _two_way_instruction(user_msg)

    elif guard_tone == "formal":
        date_line = f" ({age}에 결정)" if age else ""
        if guard_mode == "auto":
            return "\n".join([
                "⚠️ 이전 결정과 충돌이 감지되었습니다.",
                "",
                f"📌 결정 내용 (중요도 {top_imp}{date_line}): {top_content[:150]}",
                "",
                "이전 결정에 따라 자동으로 유지됩니다.",
                "변경이 필요하신 경우 사유를 알려주시면 기억을 업데이트한 후 진행하겠습니다.",
            ])
        user_msg = "\n".join([
            "⚠️ 이전 결정과 충돌이 감지되었습니다.",
            f"📌 결정 내용 (중요도 {top_imp}{date_line}): {top_content[:150]}",
            "",
            "변경하시겠습니까, 아니면 기존 결정을 유지하시겠습니까?",
        ])
        return _two_way_instruction(user_msg)

    else:
        # friendly (기본)
        date_note = f" ({age})" if age else ""
        if guard_mode == "auto":
            return "\n".join([
                "💡 이전에 함께 정한 내용이 있어요.",
                "",
                f"📌 {top_content[:120]} (중요도 {top_imp}{date_note})",
                "",
                "혹시 놓치셨을까 봐 알려드렸어요.",
                "바꾸실 거라면 이유도 함께 말씀해 주세요 — 기억 업데이트하고 바로 진행할게요.",
            ])
        user_msg = "\n".join([
            "💡 이전에 함께 정한 내용이 있어요.",
            f"📌 {top_content[:120]} (중요도 {top_imp}{date_note})",
            "",
            "바꾸실 건가요, 아니면 그대로 갈까요?",
        ])
        return _two_way_instruction(user_msg)


def _format_guard_warn(mem_lines: list[str], guard_tone: str, is_explore: bool) -> str:
    """Format the non-blocking warning message according to guard_tone."""
    explore_note = " (탐색 모드)" if is_explore else ""
    if guard_tone == "brief":
        lines = [
            "<user-prompt-submit-hook>",
            f"💡 참고{explore_note}:",
            *mem_lines[:2],
            "</user-prompt-submit-hook>",
        ]
    elif guard_tone == "formal":
        lines = [
            "<user-prompt-submit-hook>",
            f"관련 이전 결정이 확인되었습니다{explore_note}.",
            "",
            *mem_lines,
            "",
            "참고하여 진행하시기 바랍니다.",
            "</user-prompt-submit-hook>",
        ]
    else:
        # friendly
        lines = [
            "<user-prompt-submit-hook>",
            f"💡 관련 이전 결정이 있어요, 참고해 주세요{explore_note}.",
            "",
            *mem_lines,
            "</user-prompt-submit-hook>",
        ]
    return "\n".join(lines)


def _format_gate_footer(guard_tone: str) -> str:
    """Format the gate deny footer according to guard_tone."""
    if guard_tone == "brief":
        return "confirm_change 호출 후 진행하세요."
    elif guard_tone == "formal":
        return (
            "변경이 필요하신 경우:\n"
            "1. 사유를 알려주시면 confirm_change로 기억을 업데이트합니다.\n"
            "2. 업데이트 후 작업을 진행하겠습니다."
        )
    else:
        # friendly
        return (
            "바꾸실 거라면 말씀해 주세요.\n"
            "confirm_change로 기억을 업데이트하면 바로 진행할 수 있어요."
        )


def evaluate_prompt_guard(
    prompt: str,
    project: str,
    search_fn: Callable[..., list[dict[str, Any]]],
    *,
    n_results: int = 5,
    importance_min: float = 5.0,
    guard_level: str = "medium",
    guard_mode: str = "ask",
    guard_tone: str = "friendly",
) -> dict[str, Any]:
    """Evaluate a user prompt for change intent and inject relevant memories.

    Args:
        prompt: The user's prompt text.
        project: Project ID.
        search_fn: Memory search function (store.search).
        guard_level: "strong" (≥5.0), "medium" (≥8.0), "weak" (≥9.5), "explore" (알림만).
        guard_mode: "ask" (show choices, user decides) or "auto" (auto-keep, notify).
        guard_tone: "friendly" (친근, 기본), "brief" (간결), "formal" (공식).
        n_results: Max results to return.
        importance_min: Minimum importance threshold.

    Returns:
        dict with:
            has_change_intent: bool
            topics: list[str]
            memories_found: int
            output: str — injection text (empty = no injection)
    """
    if not detect_change_intent(prompt):
        return {
            "has_change_intent": False,
            "topics": [],
            "memories_found": 0,
            "output": "",
        }

    topics = extract_topics(prompt)
    if not topics:
        return {
            "has_change_intent": True,
            "topics": [],
            "memories_found": 0,
            "output": "",
        }

    query = " ".join(topics)

    # Search for related decisions in the project
    all_memories: list[dict[str, Any]] = []

    # 1. Decision-tagged memories (most relevant)
    for tag_filter in (["decision"], ["gotcha"], None):
        try:
            kwargs: dict[str, Any] = {
                "query": query,
                "project": project,
                "n_results": n_results,
                "importance_min": importance_min,
                "use_hybrid": True,
            }
            if tag_filter:
                kwargs["tags"] = tag_filter
            mems = search_fn(**kwargs)
            if mems:
                existing_ids = {m.get("id") for m in all_memories}
                for m in mems:
                    if m.get("id") not in existing_ids:
                        all_memories.append(m)
        except Exception:
            logger.debug(
                "prompt-guard search failed for project=%s tags=%s",
                project, tag_filter,
            )

    # 2. Also search _global for cross-project decisions
    try:
        global_mems = search_fn(
            query=query,
            project="_global",
            n_results=3,
            importance_min=7.0,
            tags=["decision", "gotcha"],
            use_hybrid=True,
        )
        existing_ids = {m.get("id") for m in all_memories}
        for gm in global_mems:
            if gm.get("id") not in existing_ids:
                all_memories.append(gm)
    except Exception:
        pass

    if not all_memories:
        return {
            "has_change_intent": True,
            "topics": topics,
            "memories_found": 0,
            "output": "",
        }

    # Sort: project memories first, then by importance descending
    all_memories.sort(
        key=lambda m: (
            0 if m.get("metadata", {}).get("project", "") == project else 1,
            -m.get("metadata", {}).get("importance", 0),
        ),
    )
    all_memories = all_memories[:n_results]

    # Check if any project-specific memory exceeds the guard threshold
    block_threshold = GUARD_LEVELS.get(guard_level, GUARD_LEVELS["medium"])
    project_mems = [
        m for m in all_memories
        if m.get("metadata", {}).get("project", "") == project
    ]
    high_imp_conflict = any(
        m.get("metadata", {}).get("importance", 0) >= block_threshold
        for m in project_mems
    )

    # Format memories for display
    mem_lines = []
    for i, mem in enumerate(all_memories, 1):
        content = mem.get("document", mem.get("content", ""))
        imp = mem.get("metadata", {}).get("importance", "?")
        src = mem.get("metadata", {}).get("project", project)
        mem_type = mem.get("metadata", {}).get("memory_type", "")
        prefix = "[global] " if src == "_global" else ""
        type_tag = f"[{mem_type}] " if mem_type else ""
        mem_lines.append(
            f"  [{i}] {prefix}{type_tag}(imp:{imp}) {content[:300]}"
        )

    # explore 모드: 차단 없이 알림만
    is_explore = guard_level == "explore"

    if high_imp_conflict and not is_explore:
        top_mem = project_mems[0] if project_mems else all_memories[0]
        top_content = top_mem.get("document", top_mem.get("content", ""))[:200]
        top_imp = top_mem.get("metadata", {}).get("importance", "?")

        top_memory_id = top_mem.get("id", "")
        top_created_ts = top_mem.get("metadata", {}).get("created_ts")
        output = _format_guard_block(
            top_content, top_imp, mem_lines, guard_tone, guard_mode,
            project=project, top_memory_id=top_memory_id,
            top_created_ts=top_created_ts,
        )
        return {
            "has_change_intent": True,
            "topics": topics,
            "memories_found": len(all_memories),
            "output": output,
            "block": True,
            "guard_tone": guard_tone,
            "guard_mode": guard_mode,
        }
    else:
        # WARN mode (낮은 중요도 충돌 또는 explore 모드): 알림만, 차단 없음
        output = _format_guard_warn(mem_lines, guard_tone, is_explore)
        return {
            "has_change_intent": True,
            "topics": topics,
            "memories_found": len(all_memories),
            "output": output,
            "block": False,
            "guard_tone": guard_tone,
        }


# ── Model Selection Suggestion ────────────────────────────────

_SIMPLE_EDIT_PATTERN = re.compile(
    r"\b(fix\s+typo|rename|변수\s*이름|오타\s*수정|간단한\s*수정|simple\s+edit|"
    r"update\s+(?:version|copyright|comment)|주석\s*(?:추가|수정)|"
    r"lint|format|formatting|indent|들여쓰기)\b",
    re.IGNORECASE,
)

_ARCHITECTURE_PATTERN = re.compile(
    r"\b(architect|아키텍처|설계|design\s+(?:pattern|decision|system)|"
    r"시스템\s*설계|마이크로서비스|microservice|scale|확장|"
    r"데이터베이스\s*(?:설계|선택)|database\s+(?:design|choice)|"
    r"trade.?off|비교\s*분석|migration\s+plan|이관\s*계획|"
    r"RFC|ADR|proposal|제안서)\b",
    re.IGNORECASE,
)

_COMPLEX_DEBUG_PATTERN = re.compile(
    r"\b(race\s*condition|동시성|concurrency|deadlock|교착|"
    r"memory\s*leak|메모리\s*누수|segfault|core\s*dump|"
    r"intermittent|간헐적|flaky|재현\s*(?:안|불)|hard.to.reproduce|"
    r"performance\s*(?:issue|degradation|regression)|성능\s*(?:저하|이슈)|"
    r"security\s*(?:vuln|exploit|audit)|보안\s*(?:취약|감사)|"
    r"stack\s*overflow|heap|corruption|undefined\s*behavior)\b",
    re.IGNORECASE,
)


def suggest_model(prompt: str) -> str | None:
    """Suggest an appropriate model based on task complexity in the prompt.

    Returns an optional suggestion string, or None if no strong signal.
    """
    if _COMPLEX_DEBUG_PATTERN.search(prompt):
        return "💡 Model hint: opus recommended (complex debugging/security task detected)"
    if _ARCHITECTURE_PATTERN.search(prompt):
        return "💡 Model hint: sonnet or opus recommended (architecture/design task detected)"
    if _SIMPLE_EDIT_PATTERN.search(prompt):
        return "💡 Model hint: haiku is sufficient (simple edit task detected)"
    return None


# ── Artifact Tracking (Compaction 보완) ──────────────────────────

_artifact_buffer: dict[str, list[dict[str, str]]] = {}
_artifact_lock = threading.Lock()


def _artifact_key(project: str, session_id: str) -> str:
    """session_id가 없으면 project만으로 키 생성."""
    return f"{project}:{session_id}" if session_id else project


def append_artifact(project: str, session_id: str, artifact: dict[str, str]) -> None:
    """PostToolUse에서 호출 — 파일 변경 정보를 인메모리 버퍼에 누적."""
    with _artifact_lock:
        key = _artifact_key(project, session_id)
        if key not in _artifact_buffer:
            _artifact_buffer[key] = []
        # 중복 방지: 같은 path + 같은 type은 마지막만 유지
        path = artifact.get("path", "")
        if path:
            _artifact_buffer[key] = [
                a for a in _artifact_buffer[key]
                if not (a.get("path") == path and a["type"] == artifact["type"])
            ]
        _artifact_buffer[key].append(artifact)
        # 최대 50개
        if len(_artifact_buffer[key]) > 50:
            _artifact_buffer[key] = _artifact_buffer[key][-50:]


def get_artifact_summary(project: str, session_id: str) -> str:
    """PreCompact 시 호출 — 누적 artifact을 텍스트 요약으로 반환."""
    with _artifact_lock:
        key = _artifact_key(project, session_id)
        artifacts = _artifact_buffer.get(key, [])
        if not artifacts and session_id:
            artifacts = _artifact_buffer.get(project, [])
        if not artifacts:
            return ""
        artifacts = list(artifacts)

    lines = ["## 세션 Artifact 요약"]
    modified = [a for a in artifacts if a["type"] == "file_modified"]
    created = [a for a in artifacts if a["type"] == "file_created"]
    deleted = [a for a in artifacts if a["type"] in ("file_deleted", "file_moved")]
    tests = [a for a in artifacts if a["type"] == "test_run"]

    if modified:
        names = [a["path"].rsplit("/", 1)[-1] for a in modified]
        lines.append(f"수정: {', '.join(names)} ({len(modified)}개)")
    if created:
        names = [a["path"].rsplit("/", 1)[-1] for a in created]
        lines.append(f"생성: {', '.join(names)} ({len(created)}개)")
    if deleted:
        lines.append(f"삭제/이동: {len(deleted)}개")
    if tests:
        lines.append(f"테스트 실행: {len(tests)}회")

    return "\n".join(lines)


# ── Docs Map (SessionStart에서 클라이언트가 전송) ─────────────────

_docs_cache: dict[str, list[str]] = {}
_docs_lock = threading.Lock()


def set_docs_cache(project: str, files: list[str]) -> None:
    """SessionStart Hook에서 전송된 docs 목록을 캐시."""
    with _docs_lock:
        _docs_cache[project] = [f for f in files if isinstance(f, str)][:50]


def get_docs_map(project: str, max_per_group: int = 3) -> str:
    """Brief Recall용 docs map 반환. 캐시가 없으면 빈 문자열."""
    with _docs_lock:
        files = list(_docs_cache.get(project, []))
    if not files:
        return ""

    root_files: list[str] = []
    grouped: dict[str, list[str]] = {}
    for f in files:
        if "/" in f:
            folder, name = f.split("/", 1)
            grouped.setdefault(folder, []).append(name)
        else:
            root_files.append(f)

    lines = [f"\n## 📄 Docs ({len(files)}개)"]
    if root_files:
        if len(root_files) <= max_per_group + 2:
            lines.append(f"루트: {', '.join(root_files)}")
        else:
            lines.append(
                f"루트: {', '.join(root_files[:max_per_group])}, "
                f"+{len(root_files) - max_per_group}개"
            )
    for folder, names in sorted(grouped.items()):
        if len(names) <= max_per_group + 2:
            lines.append(f"{folder}/: {', '.join(names)}")
        else:
            lines.append(
                f"{folder}/: {', '.join(names[:max_per_group])}, "
                f"+{len(names) - max_per_group}개"
            )

    return "\n".join(lines)

