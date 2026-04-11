"""REST API endpoints and web dashboard for memory-mcp-server.

Registers HTTP routes on the FastMCP server using @mcp.custom_route().
All routes share the same port as the MCP protocol (default 8321).
"""

from __future__ import annotations

import asyncio
import fnmatch
import gc as _gc
import json
import logging
import os
import re
import resource
import sys
import time
from collections import defaultdict, deque
from datetime import date as _date, timedelta as _timedelta
from typing import Any, Callable
import urllib.request as _urllib_request

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from memory_mcp import __version__
from memory_mcp.constants import MemoryType
from memory_mcp.i18n import detect_lang, detect_lang_from_code, t
from memory_mcp.install import INSTALL_VERSION
from memory_mcp.templates.claude_md_guide import GUIDE_VERSION
from memory_mcp.db.store import MemoryStore

logger = logging.getLogger(__name__)

# Timezone for daily log (default KST = UTC+9)
_TZ_OFFSET_HOURS = int(os.environ.get("TZ_OFFSET_HOURS", "9"))
_LOCAL_TZ = __import__("datetime").timezone(__import__("datetime").timedelta(hours=_TZ_OFFSET_HOURS))

# ── Performance metrics collector ──────────────────────────────
class _MetricsStore:
    """In-memory per-endpoint latency metrics (deque, no persistence)."""

    def __init__(self, maxlen: int = 1000):
        self._data: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )

    def record(self, endpoint: str, duration_ms: float) -> None:
        self._data[endpoint].append((time.time(), duration_ms))

    def get_stats(self, window_seconds: int = 3600) -> dict[str, dict]:
        cutoff = time.time() - window_seconds
        result: dict[str, dict] = {}
        for ep, samples in self._data.items():
            durations = sorted(d for ts, d in samples if ts > cutoff)
            n = len(durations)
            if n == 0:
                continue
            result[ep] = {
                "count": n,
                "p50_ms": round(durations[n // 2], 1),
                "p95_ms": round(durations[int(n * 0.95)], 1),
                "p99_ms": round(durations[min(int(n * 0.99), n - 1)], 1),
                "avg_ms": round(sum(durations) / n, 1),
            }
        return result


_metrics = _MetricsStore()

# ── Journal auto-generation ──────────────────────────────────

_last_journal_date: dict[str, str] = {}  # "user:project" → "2026-03-29"

_LLM_PROVIDERS = [
    {
        "name": "qwen-flash",
        "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus-latest",
        "env_key": "QWEN_API_KEY",
    },
    {
        "name": "groq-llama8b",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.1-8b-instant",
        "env_key": "GROQ_API_KEY",
    },
]


def _date_range(start: str, end: str) -> list[str]:
    """start(포함) ~ end(미포함) 사이 날짜 목록."""
    d = _date.fromisoformat(start)
    e = _date.fromisoformat(end)
    dates = []
    while d < e:
        dates.append(d.isoformat())
        d += _timedelta(days=1)
    return dates


def _to_utc_range(date_str: str, tz_offset_hours: int | None = None) -> tuple[str, str]:
    """유저 로컬 날짜를 UTC 시간 범위로 변환."""
    tz = tz_offset_hours if tz_offset_hours is not None else _TZ_OFFSET_HOURS
    d = _date.fromisoformat(date_str)
    prev = d - _timedelta(days=1)
    start_hour = 24 - tz
    if start_hour >= 24:
        start_hour -= 24
        return (f"{d.isoformat()}T{start_hour:02d}:00:00",
                f"{d.isoformat()}T{start_hour + 23:02d}:59:59")
    end_hour = start_hour - 1
    if end_hour < 0:
        end_hour += 24
    return (f"{prev.isoformat()}T{start_hour:02d}:00:00",
            f"{d.isoformat()}T{end_hour:02d}:59:59")


def _llm_generate_journal(activities: list[dict], project: str, date_str: str) -> str | None:
    """멀티 프로바이더로 구조화된 일지 생성 (Qwen → Groq → None)."""
    if not activities:
        return None

    activity_text = ""
    for a in activities[:10]:
        content = a.get("content", a.get("document", ""))[:200]
        mtype = a.get("metadata", {}).get("type", "fact")
        activity_text += f"[{mtype}] {content}\n\n"

    system_prompt = (
        "당신은 소프트웨어 프로젝트 일지 작성 도우미입니다. "
        "주어진 활동 데이터를 4개 섹션(수행/결정/미완료/다음)으로 정리합니다. "
        "각 섹션은 불릿 포인트로 간결하게 작성합니다. "
        "활동이 없는 섹션은 생략합니다. 마크다운 형식."
    )
    user_prompt = (
        f"프로젝트: {project}\n날짜: {date_str}\n\n"
        f"아래 활동 데이터를 일지로 정리하세요:\n\n{activity_text}\n\n"
        f"형식:\n# 일지 {date_str}\n\n## 수행\n- ...\n\n## 결정\n- ...\n\n## 미완료\n- ...\n\n## 다음\n- ..."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for provider in _LLM_PROVIDERS:
        api_key = os.environ.get(provider["env_key"], "")
        if not api_key:
            continue
        try:
            payload = json.dumps({
                "model": provider["model"],
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 500,
            }).encode()
            req = _urllib_request.Request(
                provider["url"], data=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            resp = _urllib_request.urlopen(req, timeout=30)
            result = json.loads(resp.read().decode())
            journal = result["choices"][0]["message"]["content"]
            logger.info("JOURNAL generated via %s for %s/%s", provider["name"], project, date_str)
            return journal
        except Exception as e:
            logger.warning("Journal LLM failed (%s): %s", provider["name"], e)
            continue
    return None


def _generate_journal_fallback(activities: list[dict], project: str, date_str: str) -> str:
    """모든 LLM 실패 시 서버 로직으로 기본 일지."""
    lines = [f"# 일지 {date_str}"]
    summaries = [a for a in activities if a.get("metadata", {}).get("type") == "summary"]
    decisions = [a for a in activities if a.get("metadata", {}).get("type") == "decision"]
    others = [a for a in activities if a.get("metadata", {}).get("type") not in ("summary", "decision")]
    if summaries or others:
        lines.append("\n## 수행")
        for a in (summaries + others)[:5]:
            lines.append(f"- {a.get('content', a.get('document', ''))[:150]}")
    if decisions:
        lines.append("\n## 결정")
        for a in decisions[:5]:
            lines.append(f"- {a.get('content', a.get('document', ''))[:150]}")
    return "\n".join(lines)


async def _async_generate_journal_range(
    store: Any, project: str, dates: list[str], journal_key: str, today: str,
) -> None:
    """백그라운드: 여러 날짜의 일지를 순차 생성."""
    for date_str in dates:
        try:
            existing = await asyncio.to_thread(
                store.search, query=f"journal {date_str}",
                project=project, n_results=3, tags=["journal"],
            )
            has_exact = any(
                date_str in str(r.get("metadata", {}).get("tags", ""))
                for r in existing
            )
            if has_exact:
                continue

            utc_after, utc_before = _to_utc_range(date_str)
            activities = await asyncio.to_thread(
                store.search, query=f"작업 결정 완료 {date_str}",
                project=project, n_results=15,
                date_after=utc_after, date_before=utc_before,
                use_hybrid=True,
            )
            if not activities:
                continue

            journal = await asyncio.to_thread(
                _llm_generate_journal, activities, project, date_str,
            )
            if not journal:
                journal = _generate_journal_fallback(activities, project, date_str)

            from memory_mcp.constants import MemoryType as _MT
            await asyncio.to_thread(
                store.store, project=project, content=journal,
                memory_type=_MT.SUMMARY, importance=6.0,
                tags=["journal", date_str],
            )
            logger.info("JOURNAL auto-created %s/%s", project, date_str)
        except Exception:
            logger.warning("Journal generation failed for %s/%s", project, date_str)

    _last_journal_date[journal_key] = today


async def _check_and_trigger_journal(
    store: Any, user_key: str, project: str, result: dict,
) -> None:
    """날짜 변경 감지 + 백그라운드 일지 생성 트리거."""
    today = _date.today().isoformat()
    journal_key = f"{user_key}:{project}"
    last_date = _last_journal_date.get(journal_key, "")

    if not last_date:
        try:
            existing = await asyncio.to_thread(
                store.search, query="journal", project=project,
                n_results=1, tags=["journal"],
            )
            if existing:
                tags = existing[0].get("metadata", {}).get("tags", "[]")
                if isinstance(tags, str):
                    tags = json.loads(tags)
                for t in (tags if isinstance(tags, list) else []):
                    if isinstance(t, str) and len(t) == 10 and t[4:5] == "-":
                        last_date = t
                        break
        except Exception:
            pass
        if not last_date:
            last_date = today
        _last_journal_date[journal_key] = last_date

    if last_date != today:
        missing_dates = _date_range(last_date, today)
        if missing_dates:
            asyncio.create_task(
                _async_generate_journal_range(store, project, missing_dates, journal_key, today)
            )
            result["output"] = (result.get("output", "") +
                f"\n[Kandela] {last_date}~{missing_dates[-1]} 일지 생성 중...").strip()


# Module-level state, initialised by register_dashboard_routes()
_server_start_time: float = 0.0
_get_store_fn: Callable[[], MemoryStore] | None = None
_mcp_ref: Any = None

# Session cookie name
SESSION_COOKIE = "mcp_session"


# ── Rate Limiter ─────────────────────────────────────────────────


class RateLimiter:
    """Simple in-memory rate limiter for auth endpoints.

    Tracks attempts per key (typically IP + endpoint) and enforces
    max_attempts within a sliding window of window_seconds.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup: float = time.time()
        self._cleanup_interval: float = 300.0  # cleanup every 5 min

    def check(self, key: str, max_attempts: int, window_seconds: int) -> bool:
        """Return True if within limit, False if rate limited."""
        now = time.time()
        # Periodic cleanup of stale keys to prevent memory leak
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup(now, window_seconds)
        attempts = self._attempts[key]
        # Remove expired entries
        self._attempts[key] = [t for t in attempts if now - t < window_seconds]
        if len(self._attempts[key]) >= max_attempts:
            return False
        self._attempts[key].append(now)
        return True

    def _cleanup(self, now: float, default_window: int) -> None:
        """Remove keys with no recent attempts."""
        self._last_cleanup = now
        stale_keys = [
            k
            for k, v in self._attempts.items()
            if not v or (now - max(v)) > default_window
        ]
        for k in stale_keys:
            del self._attempts[k]


_rate_limiter = RateLimiter()

# Rate limit constants
_RATE_LOGIN_MAX = 10  # max attempts
_RATE_LOGIN_WINDOW = 300  # 5 minutes
_RATE_SIGNUP_MAX = 5  # max attempts
_RATE_SIGNUP_WINDOW = 600  # 10 minutes
_RATE_SET_PW_MAX = 5  # max attempts
_RATE_SET_PW_WINDOW = 600  # 10 minutes


def _get_client_ip(request: Request) -> str:
    """Get client IP. Only trusts X-Forwarded-For behind configured proxy."""
    # When behind Nginx, add proxy IP to TRUSTED_PROXY_IPS env var
    trusted = os.environ.get("TRUSTED_PROXY_IPS", "").split(",")
    trusted = [ip.strip() for ip in trusted if ip.strip()]

    if trusted and request.client and request.client.host in trusted:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


_PW_RULES = [
    (r"[A-Z]", "uppercase letter"),
    (r"[a-z]", "lowercase letter"),
    (r"[0-9]", "digit"),
    (r"[^A-Za-z0-9]", "special character"),
]


def _validate_password(password: str) -> str | None:
    """Return error message if password fails complexity rules, else None."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    for pattern, label in _PW_RULES:
        if not re.search(pattern, password):
            return f"Password must contain at least one {label}"
    return None


def _is_secure_request(request: Request) -> bool:
    """Check if request arrived over HTTPS (direct or via reverse proxy)."""
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )


# ── Helpers ──────────────────────────────────────────────────────


def _format_uptime(seconds: float) -> str:
    """Convert seconds to human-readable uptime string."""
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _get_memory_mb() -> float:
    """Get current process peak RSS in MB (stdlib, no extra deps)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return round(usage.ru_maxrss / (1024 * 1024), 1)
    return round(usage.ru_maxrss / 1024, 1)


def _get_tool_count() -> int | None:
    """Get MCP tool count (best-effort, internal API)."""
    try:
        return len(_mcp_ref._tool_manager.list_tools())  # type: ignore[union-attr]
    except Exception:
        return None


def _store() -> MemoryStore:
    """Get the MemoryStore instance (raises if not initialised)."""
    if _get_store_fn is None:
        raise RuntimeError("Dashboard routes not registered")
    return _get_store_fn()


async def _resolve_store(request: Request) -> tuple[MemoryStore, Any]:
    """Get the MemoryStore for the request (single-user mode)."""
    return _store(), None


async def _authenticate_bearer(request: Request) -> tuple[MemoryStore | None, str | None]:
    """Authenticate a Bearer API key from the request.

    Returns:
        (store, error_message) — store is set on success, error_message on failure.
        Single-user mode: returns the global store without requiring auth
        UNLESS KANDELA_REQUIRE_AUTH is enabled (legacy: MEMORY_MCP_REQUIRE_AUTH).
    """
    from memory_mcp.auth import is_require_auth, verify_single_user_key

    if is_require_auth():
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return None, "Bearer API key required"
        if not verify_single_user_key(auth_header[7:]):
            return None, "invalid API key"
    return _store(), None


async def _check_hook_feature(request: Request, feature: str) -> bool:
    """Check if a feature is enabled. Single-user mode: always True."""
    return True


# ── Route Registration ───────────────────────────────────────────


async def _save_daily_log_all_projects(get_store: Callable[[], MemoryStore]) -> None:
    """Save daily log for all projects (runs at 23:50 local time)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(_LOCAL_TZ)
    date_str = now.strftime("%Y-%m-%d")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_iso = today_start.astimezone(timezone.utc).isoformat()

    try:
        store = get_store()
        projects = store.list_projects()
    except Exception:
        logger.exception("daily_log: failed to list projects")
        return

    saved = 0
    for project in projects:
        try:
            # Skip if already saved today
            existing = store.get_by_tag(project, f"daily_{date_str}", n_results=1)
            if existing:
                continue

            # Get today's non-low memories
            results = store.search(
                query="작업 완료 결정 세션 배포 구현",
                project=project,
                n_results=30,
                date_after=today_start_iso,
            )
            if not results:
                continue

            # Build structured daily log (no LLM dependency)
            decisions = [r for r in results if r.get("metadata", {}).get("type") == "decision"]
            summaries = [r for r in results if r.get("metadata", {}).get("type") == "summary"]
            facts = [r for r in results if r.get("metadata", {}).get("type") == "fact"]
            other = [r for r in results if r not in decisions + summaries + facts]

            lines = [f"# 일일 회의록 — {date_str} [{project}]\n"]

            if summaries:
                lines.append("## 세션 요약")
                for r in summaries[:3]:
                    lines.append(f"- {r['content'][:300]}")

            if decisions:
                lines.append("\n## 주요 결정사항")
                for r in decisions[:5]:
                    lines.append(f"- {r['content'][:200]}")

            if facts:
                lines.append("\n## 기록된 사항")
                for r in (facts + other)[:5]:
                    lines.append(f"- {r['content'][:150]}")

            content = "\n".join(lines)
            store.store(
                project=project,
                content=content,
                memory_type=MemoryType.SUMMARY,
                tags=["daily_log", f"daily_{date_str}", "auto"],
                importance=6.0,
            )
            saved += 1
            logger.info("daily_log saved: project=%s date=%s", project, date_str)

        except Exception:
            logger.exception("daily_log error for project=%s", project)

    logger.info("daily_log cron done: %d/%d projects saved", saved, len(projects))


async def _daily_log_cron(get_store: Callable[[], MemoryStore]) -> None:
    """Background task: run daily log save at 23:50 local time every day."""
    from datetime import datetime, timedelta

    logger.info("Daily log cron task started")
    while True:
        now = datetime.now(_LOCAL_TZ)
        target = now.replace(hour=23, minute=50, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_secs = (target - now).total_seconds()
        logger.debug("daily_log cron: next run in %.0fs (%s)", sleep_secs, target.strftime("%Y-%m-%d %H:%M"))
        await asyncio.sleep(sleep_secs)
        await _save_daily_log_all_projects(get_store)


def start_cron_tasks() -> None:
    """Start background cron tasks. Must be called from within a running async context.

    Called from ``_run_http()`` in ``__main__.py`` after uvicorn starts,
    so the asyncio event loop is guaranteed to be running.
    """
    if _get_store_fn is None:
        logger.warning("start_cron_tasks: store not initialized, skipping")
        return
    asyncio.create_task(_daily_log_cron(_get_store_fn))
    logger.info("Daily log cron scheduled at 23:50 local (UTC+%d)", _TZ_OFFSET_HOURS)


def register_dashboard_routes(
    mcp: Any,
    get_store: Callable[[], MemoryStore],
) -> None:
    """Register REST API and dashboard routes on the FastMCP server.

    Called once from ``create_server()`` in server.py.

    Args:
        mcp: The FastMCP server instance.
        get_store: Callable returning the global MemoryStore.
    """
    global _server_start_time, _get_store_fn, _mcp_ref  # noqa: PLW0603
    _server_start_time = time.time()
    _get_store_fn = get_store
    _mcp_ref = mcp

    # Daily log cron is started later via start_cron_tasks() once the event loop is running.

    # ── GET /api/health ──────────────────────────────────────────

    @mcp.custom_route("/api/health", methods=["GET"])
    async def api_health(request: Request) -> JSONResponse:
        uptime = time.time() - _server_start_time
        # ready: use holder event if available, else fall back to store fn presence
        ready_holder = getattr(mcp, "_ready_holder", None)
        ev = ready_holder.get("event") if ready_holder is not None else None
        if ev is not None:
            ready = ev.is_set()
        else:
            # _get_store_fn is set by register_dashboard_routes = create_server done
            ready = _get_store_fn is not None
        data: dict[str, Any] = {
            "status": "healthy",
            "version": __version__,
            "guide_version": GUIDE_VERSION,
            "install_version": INSTALL_VERSION,
            "uptime_seconds": round(uptime, 1),
            "uptime_human": _format_uptime(uptime),
            "ready": ready,
        }
        # Expose internal metrics (single-user mode — always available)
        if True:
            data["tool_count"] = _get_tool_count()
            data["memory_mb"] = _get_memory_mb()
        return JSONResponse(data)

    # ── GET /api/hook-prompt/{hook_name} ─────────────────────────

    _HOOK_PROMPTS: dict[str, str] = {
        "pre-compact": """\
[Kandela — Compaction 임박] 프로젝트: {project}
컨텍스트가 곧 압축됩니다. 아래 3가지를 **지금 즉시** 수행하세요.

**[1] summarize_session** — 세션 요약 저장 (필수)
summarize_session(project='{project}')을 호출하세요.
포함: 수행한 작업, 결정, 현재 진행중인 작업과 다음 단계, 미완성 작업(있으면).

**[2] gotcha 저장** — 실패→교정이 있었다면 (필수)
이 세션에서 도구/API/셸 명령이 실패 후 다른 방법으로 성공한 경험이 있었다면,
각각 store로 저장하세요.
- memory_type='fact', tags=['gotcha', '도구명'], importance=9.0
- 포함: 실패한 호출, 에러 핵심, 성공한 방법
- 예: "adb: command not found → 전체 경로 사용"
- 예: "Notion API Cloudflare 차단 → 콘텐츠 분할 전송"
- 경로, 환경변수, 컨테이너 제약, API 제한 관련 실패는 반드시 저장
- 같은 도구를 3회+ 재시도한 해매기 패턴이 있었다면 반드시 저장

**[3] 환경 경로 & 반복 실패 패턴 저장** (필수)
(A) 빌드/런타임 환경 경로: 이 세션에서 발견/설정한 도구 경로나 환경변수가 있다면 저장.
- JAVA_HOME, ANDROID_HOME, SDK 경로, nvm/node 경로, 전체경로(/usr/lib/jvm/... 등)
- 환경변수 설정으로 문제가 해결된 경우 (export XXX=YYY)
- 예: "JAVA_HOME=/usr/lib/jvm/java-17-openjdk 설정 필요 (gradle build 실패 원인)"
- tags=['gotcha','env-path'], importance=9.0

(B) 반복 실패 패턴: 같은 에러가 3회+ 나타났다면 그 자체가 gotcha.
- 성공 여부와 무관하게, 반복된 에러 메시지 + 시도한 방법들을 저장
- 예: "Unable to locate Java Runtime 14회 반복 → JAVA_HOME 미설정이 근본 원인"
- tags=['gotcha','repeated-failure'], importance=9.0

3가지 모두 완료하기 전에 다른 작업을 하지 마세요. Compaction 후 이 정보는 사라집니다.""",

        "post-compact": """\
[Kandela — Compaction 후 복구 지시] 프로젝트: {project}
컨텍스트가 압축되었습니다. 위의 auto_recall 호출 시 recall_source='compact' 파라미터를 추가하세요:
  auto_recall(project='{project}', recall_source='compact', mode='full', ...)

복구 후 반드시 다음을 수행하세요:
1. 복구된 기억 중 '워크플로우' 태그가 붙은 기억과 최근 세션 요약을 우선적으로 검토
2. 'unfinished' 태그 기억이 있다면 미완성 작업을 최우선으로 확인
3. 중단된 작업의 현재 상태를 파악하고 바로 이어서 진행
4. 사용자에게 "어디까지 했었나요?"라고 묻지 말고, 복구된 정보를 기반으로 작업을 계속 진행
5. 배포/인프라 작업 중이었다면 context_search(query='deployment gotcha')로 운영 규칙 확인 후 진행
6. 복구된 작업 상태를 사용자에게 간략히 보고 ("이전 세션에서 X 작업을 진행 중이었습니다. 계속합니다.")""",
    }

    _PRECOMPACT_EXTRA = """

**[4] Artifact 요약 확인**
아래는 이 세션에서 수정/생성/삭제한 파일 요약입니다:
{artifact_text}
이 정보가 세션 요약에 포함되었는지 확인하세요.

**[5] 코드외적 결정 재확인**
이 세션에서 다음과 같은 코드외적 결정이 있었다면 별도 저장하세요:
- 팀/클라이언트 합의, 규제/감사 결정, 성능 측정 결과, 외부 제약
→ store(memory_type='decision', importance=9.0, tags=['code-external'])

**[6] 미완성 작업 체크포인트**
진행 중이던 작업이 있다면 저장: 완료 범위, 다음 단계, 막힌 부분
→ store(tags=['unfinished', 'checkpoint'], importance=8.0)"""

    def _build_precompact_prompt(project: str, session_id: str = "") -> str:
        """PreCompact 프롬프트 생성. 기존 [1][2][3] + 신규 [4][5][6]."""
        base = _HOOK_PROMPTS["pre-compact"].format(project=project)
        from memory_mcp.templates.hook_prompts import get_artifact_summary
        artifact_text = get_artifact_summary(project, session_id)
        if not artifact_text:
            artifact_text = "(이 세션에서 추적된 파일 변경 없음)"
        extra = _PRECOMPACT_EXTRA.replace("{artifact_text}", artifact_text)
        return base + extra

    @mcp.custom_route("/api/metrics", methods=["GET"])
    async def api_metrics(request: Request) -> JSONResponse:
        """Server performance metrics."""
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        uptime = time.time() - _server_start_time

        return JSONResponse({
            "uptime_seconds": round(uptime),
            "endpoints": _metrics.get_stats(window_seconds=3600),
            "memory_rss_mb": rss_kb // 1024,
            "gc_counts": list(_gc.get_count()),
            "active_stores": 0,
        })

    @mcp.custom_route("/api/hook-prompt/{hook_name}", methods=["GET"])
    async def api_hook_prompt(request: Request) -> PlainTextResponse:
        """Return dynamic hook prompt text.

        Requires Bearer API key in multi-user mode for ops-warn (accesses user data).
        Query params: project (required).
        """
        hook_name = request.path_params["hook_name"]
        project = request.query_params.get("project", "")
        if not project:
            return PlainTextResponse("project parameter required", status_code=400)

        # ops-warn: 동적으로 프로젝트의 ops/gotcha 기억을 검색
        if hook_name == "ops-warn":
            authed_store, auth_err = await _authenticate_bearer(request)
            if auth_err:
                return PlainTextResponse(auth_err, status_code=401)
            warn_type = request.query_params.get("type", "restart")
            return await _ops_warn_response(project, warn_type, store=authed_store)

        # pre-compact: 동적 artifact 주입
        if hook_name == "pre-compact":
            session_id = request.query_params.get("session_id", "")
            return PlainTextResponse(_build_precompact_prompt(project, session_id))

        template = _HOOK_PROMPTS.get(hook_name)
        if template is None:
            return PlainTextResponse("Unknown hook", status_code=404)
        return PlainTextResponse(template.format(project=project))

    def _find_store_for_project(project: str) -> MemoryStore | None:
        """프로젝트가 존재하는 store를 찾는다."""
        s = _store()
        if s.project_exists(project):
            return s
        return None

    # type별 검색 쿼리 + 헤더 + 태그 + 조언
    _OPS_WARN_CONFIG: dict[str, dict[str, Any]] = {
        "restart": {
            "header": "서비스 재시작 감지",
            "query": "서비스 재시작 환경변수 .env API키 누락 프로세스 restart source",
            "tags": ["gotcha", "ops", "env-path", "deploy", "checklist"],
            "advice": "환경변수(.env), API 키, DB 연결이 모두 로드되었는지 반드시 확인하세요.",
        },
        "destructive": {
            "header": "파괴적 명령 감지",
            "query": "삭제 백업 복구 rollback force push 주의 금지 destructive",
            "tags": ["gotcha", "ops", "security", "checklist"],
            "advice": "이 명령은 되돌릴 수 없습니다. 백업 여부, 대상 경로/DB, dev/prod 환경을 재확인하세요.",
        },
        "deploy": {
            "header": "배포 감지",
            "query": "배포 deploy 환경변수 마이그레이션 staging production 체크리스트",
            "tags": ["gotcha", "ops", "deploy", "env-path", "checklist"],
            "advice": "배포 대상(dev/staging/prod), 환경변수, DB 마이그레이션 상태를 확인하세요.",
        },
        "no_deps_missing": {
            "header": "⚠️ --no-deps 누락 감지",
            "query": "docker compose up no-deps gotcha 서비스 재시작 중단",
            "tags": ["gotcha", "deploy", "docker", "no-deps"],
            "advice": "docker compose up에 --no-deps가 없습니다! "
                      "다른 서비스(postgres, redis 등)가 재시작되어 활성 연결이 끊기고 장애가 발생할 수 있습니다. "
                      "올바른 명령: docker compose up -d --no-deps <서비스명>",
        },
    }

    async def _env_fail_inject(project: str, keywords: str, store: MemoryStore | None = None) -> str:
        """빌드/환경 도구 실패 시 env-path/gotcha 기억을 검색하여 주입 텍스트 반환."""
        if store is None:
            store = _find_store_for_project(project)
        if store is None:
            return ""

        all_memories: list[dict[str, Any]] = []

        # 1. 프로젝트 gotcha/env-path 검색 (tight → loose)
        for tag_filter in (["env-path", "gotcha"], ["env-path"], ["gotcha"]):
            try:
                mems = await asyncio.to_thread(
                    store.search,
                    keywords,
                    project=project,
                    n_results=5,
                    importance_min=7.0,
                    tags=tag_filter,
                    use_hybrid=True,
                )
                if mems:
                    all_memories.extend(mems)
                    break
            except Exception:
                pass

        # 2. _global 검색
        try:
            if await asyncio.to_thread(store.project_exists, "_global"):
                global_mems = await asyncio.to_thread(
                    store.search,
                    keywords,
                    project="_global",
                    n_results=3,
                    importance_min=7.0,
                    tags=["env-path", "gotcha"],
                    use_hybrid=True,
                )
                existing_ids = {m.get("id") for m in all_memories}
                for gm in global_mems:
                    if gm.get("id") not in existing_ids:
                        all_memories.append(gm)
        except Exception:
            pass

        if not all_memories:
            return ""

        all_memories.sort(
            key=lambda m: m.get("metadata", {}).get("importance", 0),
            reverse=True,
        )
        all_memories = all_memories[:3]

        lines = ["[Kandela — 빌드/환경 실패] 관련 기억:"]
        for mem in all_memories:
            content = mem.get("document", mem.get("content", ""))
            imp = mem.get("metadata", {}).get("importance", "?")
            src = mem.get("metadata", {}).get("project", project)
            prefix = "[global] " if src == "_global" else ""
            lines.append(f"• {prefix}(imp:{imp}) {content[:250]}")
        return "\n".join(lines)

    async def _ops_warn_response(project: str, warn_type: str, store: MemoryStore | None = None) -> PlainTextResponse:
        """위험 명령 감지 시 프로젝트+글로벌 gotcha를 검색하여 반환."""
        if store is None:
            store = _find_store_for_project(project)
        if store is None:
            return PlainTextResponse("No ops gotcha found", status_code=404)

        config = _OPS_WARN_CONFIG.get(warn_type, _OPS_WARN_CONFIG["restart"])

        # 프로젝트별 gotcha 검색
        all_memories: list[dict[str, Any]] = []
        try:
            project_mems = store.search(
                query=config["query"],
                project=project,
                n_results=10,
                importance_min=5.0,
                tags=config["tags"],
                use_hybrid=True,
            )
            all_memories.extend(project_mems)
        except Exception:
            pass

        # P3: _global 프로젝트에서도 보안/ops gotcha 검색
        try:
            if store.project_exists("_global"):
                global_mems = store.search(
                    query=config["query"],
                    project="_global",
                    n_results=5,
                    importance_min=7.0,
                    tags=["gotcha", "security", "ops"],
                    use_hybrid=True,
                )
                # 중복 제거 (id 기준)
                existing_ids = {m.get("id") for m in all_memories}
                for gm in global_mems:
                    if gm.get("id") not in existing_ids:
                        all_memories.append(gm)
        except Exception:
            pass

        if not all_memories:
            return PlainTextResponse("No ops gotcha found", status_code=404)

        # importance 높은 순 → 상위 5건
        all_memories.sort(
            key=lambda m: m.get("metadata", {}).get("importance", 0),
            reverse=True,
        )
        all_memories = all_memories[:5]

        lines = [
            f"[Kandela — {config['header']}] 프로젝트: {project}",
            "아래 주의사항을 확인하세요:",
            "",
        ]
        for i, mem in enumerate(all_memories, 1):
            content = mem.get("document", mem.get("content", ""))
            imp = mem.get("metadata", {}).get("importance", "?")
            src = mem.get("metadata", {}).get("project", project)
            prefix = "[global] " if src == "_global" else ""
            lines.append(f"  [{i}] {prefix}(imp:{imp}) {content[:200]}")
        lines.append("")
        lines.append(config["advice"])
        return PlainTextResponse("\n".join(lines))

    # ── POST /api/hook-eval/session-start ───────────────────────

    @mcp.custom_route("/api/hook-eval/session-start", methods=["POST"])
    async def api_hook_eval_session_start(request: Request) -> JSONResponse:
        """Evaluate SessionStart hook data server-side.

        Moves workspace matching algorithm, version comparison,
        and prompt generation to the server.

        Requires Bearer API key in multi-user mode.
        Accepts optional ?lang= query param or Accept-Language header.
        """
        from memory_mcp.templates.hook_prompts import evaluate_session_start

        _t0 = time.monotonic()

        # Readiness gate: return empty response if store not ready
        # (defense-in-depth; after Part 1 this is normally never triggered)
        store_check = _get_store_fn() if _get_store_fn else None
        if store_check is None:
            return JSONResponse({"prompt": "", "project_id": ""}, status_code=200)

        # Language detection: query param takes priority over Accept-Language header
        lang_param = request.query_params.get("lang", "")
        lang = detect_lang_from_code(lang_param) if lang_param else detect_lang(request)

        # Auth check
        store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": t("err_invalid_json", lang)}, status_code=400)

        cwd = data.get("cwd", "")
        if not cwd:
            return JSONResponse({"error": "cwd required"}, status_code=400)

        hostname = data.get("hostname", "")

        # Get workspaces from authenticated user's store
        assert store is not None
        workspaces = await asyncio.to_thread(store.get_all_workspace_paths)

        result = evaluate_session_start(
            cwd=cwd,
            hostname=hostname,
            workspaces=workspaces,
            server_guide_version=GUIDE_VERSION,
            server_install_version=INSTALL_VERSION,
            local_guide_version=data.get("local_guide_version"),
            local_install_version=data.get("local_install_version"),
        )

        # Append inbox (unreviewed) count if a single project matched
        if result.get("matched") and result.get("project_id"):
            pid = result["project_id"]
            try:
                brief = await asyncio.to_thread(store.get_project_brief, pid)
                unreviewed = brief.get("unreviewed_count", 0)
                if unreviewed > 0:
                    result["inbox_count"] = unreviewed
                    result["prompt"] += (
                        f"\n📬 미확인 메모 {unreviewed}건 — "
                        f"inbox 또는 /kd-inbox로 확인"
                    )
            except Exception:
                logger.debug("session-start: inbox count failed for %s", pid)

        _metrics.record("session-start", (time.monotonic() - _t0) * 1000)
        return JSONResponse(result)

    # ── POST /api/hook-eval/context-monitor ─────────────────────

    @mcp.custom_route("/api/hook-eval/context-monitor", methods=["POST"])
    async def api_hook_eval_context_monitor(request: Request) -> JSONResponse:
        """Evaluate PostToolUse data server-side (context-monitor hook).

        Moves IP-sensitive logic (danger regex, adaptive intervals,
        error tracking) to the server.  The client hook is a thin wrapper
        that sends raw data and prints the response output.

        Requires Bearer API key in multi-user mode.
        Accepts optional ?lang= query param or Accept-Language header.
        """
        _t0 = time.monotonic()
        from memory_mcp.templates.hook_prompts import evaluate_context_monitor

        # Language detection: query param takes priority over Accept-Language header
        lang_param = request.query_params.get("lang", "")
        lang = detect_lang_from_code(lang_param) if lang_param else detect_lang(request)

        # Auth check
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": t("err_invalid_json", lang)}, status_code=400)

        project = data.get("project", "")
        if not project:
            return JSONResponse({"error": t("err_project_required", lang)}, status_code=400)

        result = evaluate_context_monitor(
            project=project,
            tool_name=data.get("tool_name", ""),
            command=data.get("command", ""),
            exit_code=data.get("exit_code"),
            input_tokens=data.get("input_tokens"),
            ctx_limit=data.get("ctx_limit", 200000),
            last_check_ts=data.get("last_check_ts", 0),
            interval=data.get("interval", 120),
            warned=data.get("warned", False),
            tool_call_count=data.get("tool_call_count", 0),
            session_bloat_warned=data.get("session_bloat_warned", False),
        )

        # MA-2: Topic buffer + Milestone re-injection
        # MA-3: Utilization check
        from memory_mcp.templates.hook_prompts import (
            append_topic,
            check_injection_utilization,
            check_milestones,
            format_milestone_injection,
            get_topic_summary,
            match_gotchas_for_command,
            track_injection,
        )

        # ?ma=off disables MA features (for A/B benchmarking)
        ma_enabled = request.query_params.get("ma", "on") != "off"

        cmd = data.get("command", "")
        if cmd and ma_enabled:
            append_topic(project, cmd)

        # MA-3: Check if recent injections were utilized/violated
        tool_name_val = data.get("tool_name", "")
        if tool_name_val == "Bash" and cmd and ma_enabled:
            check_injection_utilization(project, cmd, data.get("exit_code"))

        milestones_hit = data.get("milestones_hit", 0)
        input_tokens = data.get("input_tokens")
        ctx_limit = data.get("ctx_limit", 200000)

        if ma_enabled and result.get("should_check_context") and input_tokens is not None and ctx_limit > 0:
            pct = int(input_tokens * 100 / ctx_limit)
            new_mask, newly_crossed = check_milestones(pct, milestones_hit)
            if newly_crossed and authed_store is not None:
                topics = get_topic_summary(project)
                if topics:
                    matches = await asyncio.to_thread(
                        match_gotchas_for_command, project, topics, authed_store.search,
                    )
                    if matches:
                        inject = format_milestone_injection(
                            project, newly_crossed[-1], matches,
                        )
                        existing_out = result.get("output", "")
                        result["output"] = (
                            (inject + "\n" + existing_out).strip()
                            if existing_out else inject
                        )
                        # MA-3: Track milestone injection
                        track_injection(
                            project,
                            [m.get("id", "") for m in matches if m.get("id")],
                            "milestone",
                            f"milestone_{newly_crossed[-1]}%",
                        )
            result["milestones_hit"] = new_mask

        # If danger detected, fetch ops-warn from store
        warn_type = result.get("warn_type")
        if warn_type:
            ops_resp = await _ops_warn_response(project, warn_type, store=authed_store)
            if ops_resp.status_code == 200:
                result["ops_warn_output"] = ops_resp.body.decode()
            else:
                # Fallback warning
                config = _OPS_WARN_CONFIG.get(warn_type, _OPS_WARN_CONFIG["restart"])
                result["ops_warn_output"] = (
                    f"[Kandela — {config['header']}] 프로젝트: {project}\n"
                    f"{config['advice']}\n"
                    f"context_search(project='{project}', "
                    f"query='{warn_type} gotcha')로 관련 주의사항을 검색하세요."
                )

        # [CB-2] If build/env tool failed, inject env-path/gotcha memories
        env_fail_keywords = result.get("env_fail_keywords")
        if env_fail_keywords:
            inject = await _env_fail_inject(project, env_fail_keywords, store=authed_store)
            if inject:
                existing_out = result.get("output", "")
                result["output"] = (inject + "\n" + existing_out).strip() if existing_out else inject

        # If file edit detected, check build-warn rules
        tool_name = data.get("tool_name", "")
        file_path = data.get("file_path", "")
        if tool_name in ("Edit", "Write") and file_path:
            from memory_mcp.templates.hook_prompts import evaluate_build_warn

            if authed_store is not None:
                build_warn = evaluate_build_warn(
                    tool_name=tool_name,
                    file_path=file_path,
                    project=project,
                    search_fn=authed_store.search,
                )
                if build_warn:
                    result["build_warn_output"] = build_warn

        # Artifact tracking — collect file changes for Compaction resilience
        session_id = data.get("session_id", "")
        if tool_name in ("Edit", "Write") and file_path:
            from memory_mcp.templates.hook_prompts import append_artifact
            append_artifact(project, session_id, {
                "type": "file_modified" if tool_name == "Edit" else "file_created",
                "path": file_path,
            })
        elif tool_name == "Bash":
            cmd = data.get("command", "")
            if cmd and ("pytest" in cmd or "test" in cmd.split()[:2]):
                from memory_mcp.templates.hook_prompts import append_artifact
                append_artifact(project, session_id, {
                    "type": "test_run",
                    "command": cmd[:200],
                })

        # If context warning needed, fetch pre-compact prompt
        if result.get("should_check_context") and result.get("warned") and not data.get("warned", False):
            session_id = data.get("session_id", "")
            result["context_warn_output"] = _build_precompact_prompt(project, session_id)

        # ── Journal auto-generation (날짜 변경 감지) ──
        if project and authed_store is not None:
            _j_user = getattr(authed_store, '_db_path', 'single')[:20]
            await _check_and_trigger_journal(authed_store, _j_user, project, result)

        _metrics.record("context-monitor", (time.monotonic() - _t0) * 1000)
        return JSONResponse(result)

    # ── POST /api/hook-eval/pre-tool ──────────────────────────────

    @mcp.custom_route("/api/hook-eval/pre-tool", methods=["POST"])
    async def api_hook_eval_pre_tool(request: Request) -> JSONResponse:
        """PreToolUse hook: inject gotchas BEFORE dangerous Bash commands run.

        Called before the Bash tool executes. Unlike PostToolUse, this fires
        while Claude can still adjust its next command (e.g., add .env).

        Returns {"output": str} — empty string means no injection.
        Requires Bearer API key in multi-user mode.
        """
        _t0 = time.monotonic()
        from memory_mcp.templates.hook_prompts import classify_danger

        # Auth check
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"output": ""})

        project = data.get("project", "")
        command = data.get("command", "")
        if not project or not command:
            return JSONResponse({"output": ""})

        warn_type = classify_danger(command)
        if warn_type:
            # Existing ops-warn flow (unchanged)
            ops_resp = await _ops_warn_response(project, warn_type, store=authed_store)
            if ops_resp.status_code != 200:
                return JSONResponse({"output": "", "block": False})

            body = ops_resp.body.decode()
            has_memories = any(line.strip().startswith("[") and "(imp:" in line
                               for line in body.splitlines())
            # Block destructive commands AND --no-deps missing
            should_block = warn_type in ("destructive", "no_deps_missing")
            return JSONResponse({"output": body, "block": should_block})

        # MA-1: Semantic gotcha matching for non-dangerous commands
        # ?ma=off disables MA features (for A/B benchmarking)
        ma_enabled = request.query_params.get("ma", "on") != "off"

        from memory_mcp.templates.hook_prompts import (
            format_gotcha_warning,
            match_gotchas_for_command,
        )

        if authed_store is not None and ma_enabled:
            matches = await asyncio.to_thread(
                match_gotchas_for_command, project, command, authed_store.search,
            )
            if matches:
                output = format_gotcha_warning(project, matches)
                # MA-3: Track injection for utilization measurement
                from memory_mcp.templates.hook_prompts import track_injection
                track_injection(
                    project,
                    [m.get("id", "") for m in matches if m.get("id")],
                    "pre_tool",
                    command[:200],
                )
                _metrics.record("pre-tool", (time.monotonic() - _t0) * 1000)
                return JSONResponse({"output": output, "block": False})

        _metrics.record("pre-tool", (time.monotonic() - _t0) * 1000)
        return JSONResponse({"output": "", "block": False})

    # ── POST /api/hook-eval/build-warn ────────────────────────────

    @mcp.custom_route("/api/hook-eval/build-warn", methods=["POST"])
    async def api_hook_eval_build_warn(request: Request) -> JSONResponse:
        """Evaluate file edit and return build-rule warning if applicable.

        Standalone endpoint for direct build-warn checks.
        Requires Bearer API key in multi-user mode.
        Accepts optional ?lang= query param or Accept-Language header.
        """
        from memory_mcp.templates.hook_prompts import evaluate_build_warn

        # Language detection: query param takes priority over Accept-Language header
        lang_param = request.query_params.get("lang", "")
        lang = detect_lang_from_code(lang_param) if lang_param else detect_lang(request)

        # Auth check
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": t("err_invalid_json", lang)}, status_code=400)

        project = data.get("project", "")
        if not project:
            return JSONResponse({"error": t("err_project_required", lang)}, status_code=400)

        tool_name = data.get("tool_name", "")
        file_path = data.get("file_path", "")
        if not tool_name or not file_path:
            return JSONResponse({"output": ""})

        if authed_store is None:
            return JSONResponse({"output": ""})

        build_warn = evaluate_build_warn(
            tool_name=tool_name,
            file_path=file_path,
            project=project,
            search_fn=authed_store.search,
        )

        return JSONResponse({"output": build_warn or ""})

    # ── POST /api/hook-eval/prompt-guard-hook — HTTP hook endpoint ──

    @mcp.custom_route("/api/hook-eval/prompt-guard-hook", methods=["POST"])
    async def api_hook_eval_prompt_guard_hook(request: Request) -> JSONResponse:
        """HTTP hook endpoint for UserPromptSubmit.

        Receives Claude Code hook JSON directly (no bash script needed).
        Returns JSON in Claude Code HTTP hook response format.
        """
        _t0 = time.monotonic()
        # Feature gating: Prompt Guard is Pro-only
        if not await _check_hook_feature(request, "prompt_guard"):
            return JSONResponse({})  # silent pass for free tier

        from memory_mcp.templates.hook_prompts import evaluate_prompt_guard
        import re as _re

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({})  # empty = no action

        prompt = data.get("prompt", "")
        logger.info(
            "PROMPT_GUARD_HOOK received: keys=%s prompt_len=%d prompt=%.80s",
            list(data.keys()), len(prompt), prompt,
        )
        if not prompt:
            return JSONResponse({})

        # Get project ID: header → body → workspace path matching
        project = request.headers.get("x-project-id", "")
        if not project:
            project = data.get("project", "")
        if not project:
            cwd = data.get("cwd", "")
            if cwd:
                # Try workspace path matching via server API
                store_for_lookup = _store()
                try:
                    paths = await asyncio.to_thread(
                        store_for_lookup.get_all_workspace_paths
                    )
                    # Exact match or prefix match
                    for proj, ws_path in paths.items():
                        if cwd == ws_path or cwd.startswith(ws_path + "/"):
                            project = proj
                            break
                    if not project:
                        # Partial match: workspace path is substring of cwd
                        for proj, ws_path in paths.items():
                            if ws_path in cwd:
                                project = proj
                                break
                except Exception as e:
                    logger.debug("workspace lookup failed: %s", e)
        logger.info("PROMPT_GUARD_HOOK project=%s", project or "EMPTY")
        if not project:
            return JSONResponse({})

        # Get store (try Bearer auth, fallback to default)
        authed_store, _ = await _authenticate_bearer(request)
        if authed_store is None:
            authed_store = _store()

        # Guard settings from headers or defaults
        guard_level = request.headers.get("x-guard-level", "medium")
        guard_mode = request.headers.get("x-guard-mode", "ask")
        guard_tone = request.headers.get("x-guard-tone", "friendly")

        result = await asyncio.to_thread(
            evaluate_prompt_guard,
            prompt,
            project,
            authed_store.search,
            guard_level=guard_level,
            guard_mode=guard_mode,
            guard_tone=guard_tone,
        )

        output = result.get("output", "")
        block = result.get("block", False)

        # MA-1b: Gotcha matching on user prompt (not just Bash commands)
        # Catches cases where Claude answers with text only (no tool use)
        # e.g., "마이그레이션 끝났어. 다른 할 일?" → cache-invalidate gotcha
        ma_enabled = request.query_params.get("ma", "on") != "off"
        if not output and ma_enabled and authed_store is not None:
            from memory_mcp.templates.hook_prompts import (
                format_gotcha_warning,
                match_gotchas_for_command,
                track_injection,
            )
            gotcha_matches = await asyncio.to_thread(
                match_gotchas_for_command, project, prompt, authed_store.search,
            )
            if gotcha_matches:
                output = format_gotcha_warning(project, gotcha_matches)
                track_injection(
                    project,
                    [m.get("id", "") for m in gotcha_matches if m.get("id")],
                    "user_prompt",
                    prompt[:200],
                )

        if not output:
            # 원격 결과 알림 (prompt-guard/gotcha 결과 없을 때만)
            try:
                from memory_mcp.templates.hook_prompts import check_remote_results
                session_id = data.get("session_id", "")
                client_ip = request.client.host if request.client else ""
                remote_notice = await asyncio.to_thread(
                    check_remote_results, project, session_id, client_ip
                )
                if remote_notice:
                    output = remote_notice
            except Exception:
                pass  # 원격 알림 실패 시 무시

        if not output:
            return JSONResponse({})

        if block:
            # Set gate keyed by session_id (fallback: client IP hash)
            session_id = data.get("session_id", "")
            if not session_id:
                import hashlib
                client_ip = request.client.host if request.client else "unknown"
                session_id = hashlib.md5(client_ip.encode()).hexdigest()[:12]
            gate_file = f"/tmp/.prompt-guard-conflict-{session_id}"
            with open(gate_file, "w") as gf:
                # Prepend tone so gate endpoint can use tone-appropriate footer
                gf.write(f"TONE:{guard_tone}\n{output[:2000]}")
            logger.info("PROMPT_GUARD gate SET for project=%s session=%s tone=%s", project, session_id, guard_tone)

            # Context injection with conflict warning
            from starlette.responses import PlainTextResponse
            _metrics.record("prompt-guard-hook", (time.monotonic() - _t0) * 1000)
            return PlainTextResponse(output)
        else:
            from starlette.responses import PlainTextResponse
            _metrics.record("prompt-guard-hook", (time.monotonic() - _t0) * 1000)
            return PlainTextResponse(output)

    # ── POST /api/hook-eval/prompt-guard-gate — PreToolUse gate ──

    @mcp.custom_route("/api/hook-eval/prompt-guard-gate", methods=["POST"])
    async def api_hook_eval_prompt_guard_gate(request: Request) -> JSONResponse:
        """PreToolUse gate: block all tools until conflict is resolved.

        When prompt-guard-hook detects a conflict, it sets a gate file.
        This endpoint checks the gate and blocks tool use until
        confirm_change is called (which clears the gate).
        """
        # Feature gating: Prompt Guard is Pro-only
        if not await _check_hook_feature(request, "prompt_guard"):
            return JSONResponse({})  # silent pass for free tier

        import os, glob as _glob, hashlib as _hashlib

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({})

        tool_name = data.get("tool_name", "")

        # Resolve gate file by session_id (fallback: client IP hash)
        session_id = data.get("session_id", "")
        if not session_id:
            client_ip = request.client.host if request.client else "unknown"
            session_id = _hashlib.md5(client_ip.encode()).hexdigest()[:12]
        gate_file = f"/tmp/.prompt-guard-conflict-{session_id}"

        if not os.path.exists(gate_file):
            # No active conflict — allow all tools
            return JSONResponse({})

        # Allow confirm_change to pass through (escape hatch)
        if "confirm_change" in tool_name:
            return JSONResponse({})

        # Allow memory tools (auto_recall, context_search) to pass
        if tool_name.startswith(("mcp__memory__", "mcp__kandela__")):
            return JSONResponse({})

        # Allow built-in read-only tools (Claude Code internals)
        _BUILTIN_ALLOW = {
            "ToolSearch",   # needed to load confirm_change schema
            "Read",
            "Glob",
            "Grep",
            "WebFetch",
            "WebSearch",
            "Agent",
            "TaskGet",
            "TaskList",
            "TaskOutput",
        }
        if tool_name in _BUILTIN_ALLOW:
            return JSONResponse({})

        # Block all other tools — force Claude to address the conflict
        try:
            from memory_mcp.templates.hook_prompts import _format_gate_footer
            with open(gate_file) as f:
                raw = f.read()
            # Extract stored tone and conflict text
            first_line, _, rest = raw.partition("\n")
            if first_line.startswith("TONE:"):
                stored_tone = first_line[5:].strip()
                conflict_text = rest
            else:
                stored_tone = "friendly"
                conflict_text = raw
            # Strip <user-prompt-submit-hook> wrapper tags if present
            import re as _re
            conflict_text = _re.sub(
                r"</?user-prompt-submit-hook>\n?", "", conflict_text
            ).strip()[:500]
            footer = _format_gate_footer(stored_tone)
        except Exception:
            conflict_text = "이전에 함께 결정한 내용과 충돌이 감지되었습니다."
            footer = "바꾸실 거라면 말씀해 주세요 — confirm_change로 기억을 업데이트하면 바로 진행할 수 있어요."

        return JSONResponse({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{conflict_text}\n\n---\n{footer}",
            }
        })

    # ── POST /api/hook-eval/prompt-guard — legacy command hook ────

    @mcp.custom_route("/api/hook-eval/prompt-guard", methods=["POST"])
    async def api_hook_eval_prompt_guard(request: Request) -> JSONResponse:
        """UserPromptSubmit hook: detect change intent and inject related decisions.

        When the user says "switch to RabbitMQ" or "let's lower pool_size",
        searches memory for related decisions/gotchas and injects them as
        system context so Claude sees previous reasoning before responding.

        Returns {"output": str} — empty string means no injection.
        Requires Bearer API key in multi-user mode.
        """
        from memory_mcp.templates.hook_prompts import evaluate_prompt_guard

        # Auth check
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"output": ""})

        project = data.get("project", "")
        prompt = data.get("prompt", "")
        if not project or not prompt:
            return JSONResponse({"output": ""})

        if authed_store is None:
            return JSONResponse({"output": ""})

        guard_level = data.get("guard_level", "medium")
        guard_mode = data.get("guard_mode", "ask")

        result = await asyncio.to_thread(
            evaluate_prompt_guard,
            prompt,
            project,
            authed_store.search,
            guard_level=guard_level,
            guard_mode=guard_mode,
        )

        return JSONResponse(result)

    # ── POST /api/store — Store a memory via REST API ────────────

    @mcp.custom_route("/api/store", methods=["POST"])
    async def api_store(request: Request) -> JSONResponse:
        """Store a memory via REST API.

        Public API for external integrations (CI/CD, scripts, benchmarks).
        Requires Bearer API key in multi-user mode.

        Body: {project, content, memory_type?, importance?, tags?}
        Returns: {stored: true, id: "..."}
        """
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "invalid JSON"}, status_code=400
            )

        project = data.get("project", "")
        content = data.get("content", "")
        if not project or not content:
            return JSONResponse(
                {"error": "project and content required"}, status_code=400
            )

        if authed_store is None:
            return JSONResponse(
                {"error": "no store"}, status_code=500
            )

        try:
            from memory_mcp.constants import MemoryType
            mt_str = data.get("memory_type", "fact")
            try:
                mt = MemoryType(mt_str)
            except ValueError:
                mt = MemoryType.FACT

            result = await asyncio.to_thread(
                authed_store.store,
                project=project,
                content=content,
                memory_type=mt,
                importance=data.get("importance", 5.0),
                tags=data.get("tags", []),
            )
            return JSONResponse({"stored": True, "id": result})
        except Exception as e:
            return JSONResponse(
                {"error": str(e)}, status_code=500
            )

    # ── POST /api/project-reset — Delete all memories in a project ──

    @mcp.custom_route("/api/project-reset", methods=["POST"])
    async def api_project_reset(request: Request) -> JSONResponse:
        """Delete all memories in a project via REST API.

        Public API for project cleanup (benchmarks, testing, data migration).
        Requires Bearer API key in multi-user mode.

        Body: {project: "project_name"}
        Returns: {deleted_count: N}
        """
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        project = data.get("project", "")
        if not project:
            return JSONResponse({"error": "project required"}, status_code=400)

        if authed_store is None:
            return JSONResponse({"error": "no store"}, status_code=500)

        try:
            result = await asyncio.to_thread(
                authed_store.delete_project, project
            )
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    # ── GET /api/export/{project} ────────────────────────────────

    @mcp.custom_route("/api/export/{project}", methods=["GET"])
    async def api_export_project(request: Request) -> JSONResponse:
        """Export all memories for a project as JSON array.

        Requires Bearer auth. Returns content, memory_type, importance,
        tags, and created_at for each memory.
        """
        authed_store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        project = request.path_params["project"]
        if not project:
            return JSONResponse({"error": "project required"}, status_code=400)

        if authed_store is None:
            return JSONResponse({"error": "no store"}, status_code=500)

        exists = await asyncio.to_thread(authed_store.project_exists, project)
        if not exists:
            return JSONResponse(
                {"error": f"Project '{project}' not found"}, status_code=404
            )

        # Fetch all memories (use get_recent with large limit)
        raw = await asyncio.to_thread(
            authed_store.get_recent, project, n_results=100000
        )

        memories: list[dict[str, Any]] = []
        for r in raw:
            meta = r.get("metadata", {})
            tags_raw = meta.get("tags", "[]")
            if isinstance(tags_raw, str):
                try:
                    tags_parsed = json.loads(tags_raw)
                except json.JSONDecodeError:
                    tags_parsed = []
            else:
                tags_parsed = tags_raw

            importance_raw = meta.get("importance")
            if isinstance(importance_raw, (int, float)):
                importance_val = float(importance_raw)
            else:
                _prio = meta.get("priority", "normal")
                importance_val = {"critical": 9.0, "normal": 5.0, "low": 2.0}.get(
                    _prio, 5.0
                )

            memories.append({
                "id": r["id"],
                "content": r.get("content", ""),
                "memory_type": meta.get("type", "fact"),
                "importance": importance_val,
                "tags": tags_parsed,
                "created_at": meta.get("created_at", ""),
            })

        return JSONResponse({
            "project": project,
            "count": len(memories),
            "memories": memories,
        })

    # ── GET /api/workspaces ───────────────────────────────────────

    @mcp.custom_route("/api/workspaces", methods=["GET"])
    async def api_workspaces(request: Request) -> JSONResponse:
        """Return project→workspace path mappings for SessionStart hook.

        Unauthenticated (like /api/health) — called by hook before
        MCP session is established.
        """
        store = _store()
        paths = await asyncio.to_thread(store.get_all_workspace_paths)
        return JSONResponse({"workspaces": paths})

    # ── GET /api/projects ────────────────────────────────────────

    @mcp.custom_route("/api/projects", methods=["GET"])
    async def api_projects(request: Request) -> JSONResponse:
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        projects = await asyncio.to_thread(store.list_projects_with_stats)
        # Enrich with storage info per project
        for p in projects:
            try:
                si = await asyncio.to_thread(store.get_project_storage_info, p["name"])
                p["content_bytes"] = si["content_bytes"]
                p["embedding_bytes"] = si["embedding_bytes"]
                p["total_estimated_bytes"] = si["total_estimated_bytes"]
            except Exception:
                p["content_bytes"] = 0
                p["embedding_bytes"] = 0
                p["total_estimated_bytes"] = 0
        return JSONResponse({"projects": projects, "count": len(projects)})

    # ── GET /api/stats ───────────────────────────────────────────

    @mcp.custom_route("/api/stats", methods=["GET"])
    async def api_stats(request: Request) -> JSONResponse:
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        stats = await asyncio.to_thread(store.global_stats)
        return JSONResponse(stats)

    # ── GET /api/stats/utilization ────────────────────────────────

    @mcp.custom_route("/api/stats/utilization", methods=["GET"])
    async def api_stats_utilization(request: Request) -> JSONResponse:
        """Memory Activation utilization statistics (Phase MA-3)."""
        project = request.query_params.get("project", "")
        if not project:
            return JSONResponse({"error": "project query param required"}, status_code=400)
        days_str = request.query_params.get("days", "7")
        try:
            days = int(days_str)
        except ValueError:
            days = 7

        from memory_mcp.templates.hook_prompts import _utilization_store

        if _utilization_store is None:
            return JSONResponse({"error": "Utilization tracking not initialized"}, status_code=503)

        stats = await asyncio.to_thread(_utilization_store.get_stats, project, days)
        return JSONResponse(stats)

    # ── GET /api/projects/{name} ─────────────────────────────────

    @mcp.custom_route("/api/projects/{name}", methods=["GET"])
    async def api_project_detail(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        exists = await asyncio.to_thread(store.project_exists, name)
        if not exists:
            return JSONResponse(
                {"error": f"Project '{name}' not found"}, status_code=404
            )
        stats = await asyncio.to_thread(store.project_stats, name)
        recent = await asyncio.to_thread(store.get_recent, name, n_results=20)
        memories: list[dict[str, Any]] = []
        for r in recent:
            meta = r.get("metadata", {})
            tags_raw = meta.get("tags", "[]")
            if isinstance(tags_raw, str):
                try:
                    tags = json.loads(tags_raw)
                except json.JSONDecodeError:
                    tags = []
            else:
                tags = tags_raw
            # Importance: prefer float, fallback from priority
            importance_raw = meta.get("importance")
            if isinstance(importance_raw, (int, float)):
                importance_val = float(importance_raw)
            else:
                _prio = meta.get("priority", "normal")
                importance_val = {"critical": 9.0, "normal": 5.0, "low": 2.0}.get(_prio, 5.0)

            memories.append({
                "id": r["id"],
                "content": r["content"][:300],
                "type": meta.get("type", "unknown"),
                "priority": meta.get("priority", "normal"),
                "importance": importance_val,
                "tags": tags,
                "created_at": meta.get("created_at", ""),
            })
        # Storage info
        try:
            storage = await asyncio.to_thread(store.get_project_storage_info, name)
        except Exception as e:
            logger.warning("Failed to get storage info for %s: %s", name, e)
            storage = {}
        return JSONResponse({**stats, "storage": storage, "recent_memories": memories})

    # ── POST /api/projects/{name}/rename ──────────────────────────

    @mcp.custom_route("/api/projects/{name}/rename", methods=["POST"])
    async def api_project_rename(request: Request) -> JSONResponse:
        old_name = request.path_params["name"]
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        try:
            body = await request.json()
            new_name = body.get("new_name", "").strip()
        except Exception:
            return JSONResponse({"error": "Invalid request body"}, status_code=400)
        if not new_name:
            return JSONResponse({"error": "new_name is required"}, status_code=400)
        try:
            result = await asyncio.to_thread(store.rename_project, old_name, new_name)
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    # ── POST /api/projects/{name}/delete ──────────────────────────

    @mcp.custom_route("/api/projects/{name}/delete", methods=["POST"])
    async def api_project_delete(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        try:
            store, user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        # Hard delete for single-user mode
        try:
            result = await asyncio.to_thread(store.delete_project, name)
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    # ── Trash API ──────────────────────────────────────────────

    @mcp.custom_route("/api/trash", methods=["GET"])
    async def api_trash_list(request: Request) -> JSONResponse:
        """List soft-deleted memories and projects."""
        try:
            store, user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        project = request.query_params.get("project")
        limit = int(request.query_params.get("limit", "50"))
        # Individual deleted memories
        items = await asyncio.to_thread(store.list_trash, project, limit)
        return JSONResponse({
            "items": items,
            "deleted_projects": [],
            "count": len(items),
        })

    @mcp.custom_route("/api/trash/restore", methods=["POST"])
    async def api_trash_restore(request: Request) -> JSONResponse:
        """Restore a memory from trash."""
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        project = body.get("project")
        memory_id = body.get("memory_id")
        if not project or not memory_id:
            return JSONResponse({"error": "project and memory_id required"}, status_code=400)
        ok = await asyncio.to_thread(store.restore_memory, project, memory_id)
        if ok:
            return JSONResponse({"status": "restored", "id": memory_id})
        return JSONResponse({"error": "restore failed"}, status_code=400)

    @mcp.custom_route("/api/trash/purge", methods=["POST"])
    async def api_trash_purge(request: Request) -> JSONResponse:
        """Permanently delete a memory from trash."""
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        project = body.get("project")
        memory_id = body.get("memory_id")
        if memory_id and project:
            ok = await asyncio.to_thread(store.purge_memory, project, memory_id)
            return JSONResponse({"status": "purged" if ok else "failed"})
        # Purge all trash for project or all
        items = await asyncio.to_thread(store.list_trash, project, 1000)
        count = 0
        for item in items:
            await asyncio.to_thread(store.purge_memory, item["project"], item["id"])
            count += 1
        return JSONResponse({"status": "purged", "count": count})

    # ── Archive API ───────────────────────────────────────────

    @mcp.custom_route("/api/projects/{name}/archive", methods=["POST"])
    async def api_project_archive(request: Request) -> JSONResponse:
        """Archive a project (not available in single-user mode)."""
        return JSONResponse({"error": "not available in single-user mode"}, status_code=400)

    @mcp.custom_route("/api/projects/{name}/unarchive", methods=["POST"])
    async def api_project_unarchive(request: Request) -> JSONResponse:
        """Unarchive a project (not available in single-user mode)."""
        return JSONResponse({"error": "not available in single-user mode"}, status_code=400)

    @mcp.custom_route("/api/projects/{name}/restore", methods=["POST"])
    async def api_project_restore(request: Request) -> JSONResponse:
        """Restore a soft-deleted project from trash (not available in single-user mode)."""
        return JSONResponse({"error": "not available in single-user mode"}, status_code=400)

    # ── PUT /api/project-settings/{project} ────────────────────

    @mcp.custom_route("/api/project-settings/{project}", methods=["POST"])
    async def api_project_settings(request: Request) -> JSONResponse:
        """Update project visibility settings (searchable flag)."""
        project = request.path_params["project"]
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        searchable = body.get("searchable")
        if searchable is not None:
            await asyncio.to_thread(store.set_project_searchable, project, bool(searchable))
        current = await asyncio.to_thread(store.get_project_searchable, project)
        return JSONResponse({"project": project, "searchable": current})

    # ── POST /api/bulk-visibility ─────────────────────────────

    _bulk_rate_limit: dict[str, float] = {}
    _bulk_daily_count: dict[str, tuple[str, int]] = {}

    @mcp.custom_route("/api/bulk-visibility", methods=["POST"])
    async def api_project_settings_bulk(request: Request) -> JSONResponse:
        """Bulk update project visibility settings."""
        # 인증: Bearer 우선, 세션 쿠키 fallback
        store, auth_err = await _authenticate_bearer(request)
        user = None
        if auth_err:
            try:
                store, user = await _resolve_store(request)
            except PermissionError:
                return JSONResponse({"error": "Authentication required"}, status_code=401)

        user_key = getattr(user, 'user_id', 'single') if user and hasattr(user, 'user_id') else 'single'
        now = time.time()

        last_call = _bulk_rate_limit.get(user_key, 0)
        if now - last_call < 60:
            return JSONResponse(
                {"error": "Rate limit: bulk API는 1분에 1회만 가능합니다."},
                status_code=429,
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        confirm = body.get("confirm", False)
        restore_data = body.get("restore")

        # ── restore 모드 (daily 제한 제외) ──
        if restore_data and isinstance(restore_data, dict):
            if not confirm:
                return JSONResponse({
                    "mode": "dry-run",
                    "would_restore": len(restore_data),
                    "projects": list(restore_data.keys())[:20],
                })
            restore_previous = await asyncio.to_thread(store.bulk_set_searchable, restore_data)
            _bulk_rate_limit[user_key] = now
            logger.info("BULK_RESTORE user=%s projects=%d", user_key[:8], len(restore_data))
            return JSONResponse({
                "mode": "restored",
                "updated": len(restore_data),
                "restore_previous": restore_previous,
            })

        # ── 일괄 변경 모드 ──
        searchable = body.get("searchable")
        if searchable is None:
            return JSONResponse({"error": "searchable or restore required"}, status_code=400)

        today = _date.today().isoformat()
        day_info = _bulk_daily_count.get(user_key, ("", 0))
        daily_count = day_info[1] if day_info[0] == today else 0
        if daily_count >= 10:
            return JSONResponse(
                {"error": "Daily limit: bulk 변경은 하루 10회까지 가능합니다."},
                status_code=429,
            )

        exclude_patterns = body.get("exclude_patterns", ["_global", "bench_*", "_test*"])
        SYSTEM_EXCLUDE = {"_global"}

        all_projects = await asyncio.to_thread(store.list_projects)

        target, skipped = [], []
        for proj in all_projects:
            if proj in SYSTEM_EXCLUDE or any(fnmatch.fnmatch(proj, pat) for pat in exclude_patterns):
                skipped.append(proj)
            else:
                target.append(proj)

        if not confirm:
            return JSONResponse({
                "mode": "dry-run",
                "would_update": len(target),
                "would_skip": len(skipped),
                "skipped_projects": skipped[:20],
                "target_projects": target[:20],
                "total_projects": len(all_projects),
            })

        changes = {proj: bool(searchable) for proj in target}
        previous_state = await asyncio.to_thread(store.bulk_set_searchable, changes)

        _bulk_rate_limit[user_key] = now
        _bulk_daily_count[user_key] = (today, daily_count + 1)
        logger.info(
            "BULK_VISIBILITY user=%s searchable=%s updated=%d skipped=%d daily=%d/10",
            user_key[:8], searchable, len(target), len(skipped), daily_count + 1,
        )

        return JSONResponse({
            "mode": "executed",
            "updated": len(target),
            "skipped": len(skipped),
            "previous_state": previous_state,
            "skipped_projects": skipped[:20],
        })

    # ── POST /api/journal-sync ────────────────────────────────

    @mcp.custom_route("/api/journal-sync", methods=["POST"])
    async def api_journal_sync(request: Request) -> JSONResponse:
        """전체 프로젝트 일지 일괄 생성."""
        store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        target_date = body.get("date", _date.today().isoformat())
        all_projects = await asyncio.to_thread(store.list_projects)
        created, skipped, already = [], [], []

        for proj in all_projects:
            if proj.startswith("_") or proj.startswith("bench_"):
                continue
            existing = await asyncio.to_thread(
                store.search, query=f"journal {target_date}",
                project=proj, n_results=3, tags=["journal"],
            )
            has_exact = any(
                target_date in str(r.get("metadata", {}).get("tags", ""))
                for r in existing
            )
            if has_exact:
                already.append(proj)
                continue

            utc_after, utc_before = _to_utc_range(target_date)
            activities = await asyncio.to_thread(
                store.search, query=f"작업 결정 {target_date}",
                project=proj, n_results=15,
                date_after=utc_after, date_before=utc_before,
                use_hybrid=True,
            )
            if not activities:
                skipped.append(proj)
                continue

            journal = await asyncio.to_thread(
                _llm_generate_journal, activities, proj, target_date,
            )
            if not journal:
                journal = _generate_journal_fallback(activities, proj, target_date)

            await asyncio.to_thread(
                store.store, project=proj, content=journal,
                memory_type=MemoryType.SUMMARY, importance=6.0,
                tags=["journal", target_date],
            )
            created.append(proj)

        return JSONResponse({
            "date": target_date,
            "created": created,
            "skipped": skipped,
            "already_exists": already,
        })

    # ── GET /api/search ──────────────────────────────────────────

    @mcp.custom_route("/api/search", methods=["GET"])
    async def api_search(request: Request) -> JSONResponse:
        query = request.query_params.get("q", "")
        project = request.query_params.get("project") or None
        n_results = min(int(request.query_params.get("n", "10")), 20)

        if not query:
            return JSONResponse(
                {"error": "Query parameter 'q' is required"}, status_code=400
            )

        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        cross_project = project is None
        results = await asyncio.to_thread(
            store.search,
            query=query,
            project=project,
            n_results=n_results,
            cross_project=cross_project,
        )
        formatted: list[dict[str, Any]] = []
        for r in results:
            meta = r.get("metadata", {})
            # Importance: prefer float, fallback from priority
            importance_raw = meta.get("importance")
            if isinstance(importance_raw, (int, float)):
                importance_val = float(importance_raw)
            else:
                _prio = meta.get("priority", "normal")
                importance_val = {"critical": 9.0, "normal": 5.0, "low": 2.0}.get(_prio, 5.0)

            entry: dict[str, Any] = {
                "id": r["id"],
                "content": r["content"],
                "distance": round(r.get("distance", 0), 4),
                "type": meta.get("type", "unknown"),
                "priority": meta.get("priority"),
                "importance": importance_val,
                "project": meta.get("project"),
                "created_at": meta.get("created_at"),
            }
            # Include retrieval_score if available
            if "retrieval_score" in r:
                entry["retrieval_score"] = round(r["retrieval_score"], 4)
            formatted.append(entry)
        return JSONResponse({
            "query": query,
            "results": formatted,
            "count": len(formatted),
        })

    # ── GET /api/storage ──────────────────────────────────────────

    @mcp.custom_route("/api/storage", methods=["GET"])
    async def api_storage(request: Request) -> JSONResponse:
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        info = await asyncio.to_thread(store.get_all_storage_info)

        def _fmt_bytes(b: int) -> str:
            if b < 1024:
                return f"{b} B"
            elif b < 1024 * 1024:
                return f"{b / 1024:.1f} KB"
            else:
                return f"{b / (1024 * 1024):.1f} MB"

        # Add human-readable sizes
        for proj_info in info["projects"].values():
            proj_info["content_human"] = _fmt_bytes(proj_info["content_bytes"])
            proj_info["embedding_human"] = _fmt_bytes(proj_info["embedding_bytes"])
            proj_info["total_human"] = _fmt_bytes(proj_info["total_estimated_bytes"])

        info["totals"]["content_human"] = _fmt_bytes(info["totals"]["content_bytes"])
        info["totals"]["embedding_human"] = _fmt_bytes(info["totals"]["embedding_bytes"])
        info["totals"]["total_human"] = _fmt_bytes(info["totals"]["total_estimated_bytes"])
        info["disk_usage_human"] = _fmt_bytes(info["disk_usage_bytes"])

        return JSONResponse(info)

    # ── GET /api/quota ─────────────────────────────────────────────

    @mcp.custom_route("/api/quota", methods=["GET"])
    async def api_quota(request: Request) -> JSONResponse:
        """Return quota usage summary — single-user mode, no quotas."""
        return JSONResponse({"tier": "unlimited", "message": "Single-user mode — no quotas"})

    # ── GET /api/token-usage ──────────────────────────────────────

    @mcp.custom_route("/api/token-usage", methods=["GET"])
    async def api_token_usage(request: Request) -> JSONResponse:
        try:
            store, _user = await _resolve_store(request)
        except PermissionError:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        info = await asyncio.to_thread(store.get_all_token_stats)

        def _fmt_tokens(t: int) -> str:
            if t < 1000:
                return str(t)
            elif t < 1_000_000:
                return f"{t / 1000:.1f}K"
            else:
                return f"{t / 1_000_000:.1f}M"

        for proj_info in info["projects"].values():
            proj_info["stored_tokens_human"] = _fmt_tokens(
                proj_info["estimated_tokens_stored"]
            )
            proj_info["benefit_tokens_human"] = _fmt_tokens(
                proj_info["estimated_benefit_tokens"]
            )
            proj_info["overhead_tokens_human"] = _fmt_tokens(
                proj_info.get("overhead_tokens", 0)
            )
            # ROI based on USD
            cost_usd = proj_info.get("cost_usd", 0)
            benefit_usd = proj_info.get("benefit_usd", 0)
            proj_info["roi"] = round(benefit_usd / cost_usd, 1) if cost_usd > 0 else 0.0
            proj_info["cost_usd_human"] = f"${cost_usd:.3f}"
            proj_info["benefit_usd_human"] = f"${benefit_usd:.3f}"
            proj_info["net_saving_usd_human"] = f"${proj_info.get('net_saving_usd', 0):.3f}"

        info["totals"]["stored_tokens_human"] = _fmt_tokens(
            info["totals"]["estimated_tokens_stored"]
        )
        info["totals"]["benefit_tokens_human"] = _fmt_tokens(
            info["totals"]["estimated_benefit_tokens"]
        )
        info["totals"]["overhead_tokens_human"] = _fmt_tokens(
            info["totals"].get("overhead_tokens", 0)
        )
        info["totals"]["cost_usd_human"] = f"${info['totals'].get('cost_usd', 0):.3f}"
        info["totals"]["benefit_usd_human"] = f"${info['totals'].get('benefit_usd', 0):.3f}"
        info["totals"]["net_saving_usd_human"] = f"${info['totals'].get('net_saving_usd', 0):.3f}"

        return JSONResponse(info)


    # ── Legal document pages (PIPA / GDPR) ─────────────────────────

    @mcp.custom_route("/docs/privacy-policy", methods=["GET"])
    async def docs_privacy_policy(request: Request) -> HTMLResponse:
        """Serve the privacy policy page."""
        return HTMLResponse(PRIVACY_POLICY_HTML)

    @mcp.custom_route("/docs/terms", methods=["GET"])
    async def docs_terms(request: Request) -> HTMLResponse:
        """Serve the terms of service page."""
        return HTMLResponse(TERMS_HTML)

    @mcp.custom_route("/docs/operator-info", methods=["GET"])
    async def docs_operator_info(request: Request) -> HTMLResponse:
        """Serve the operator information page."""
        return HTMLResponse(OPERATOR_INFO_HTML)

    logger.info("Legal doc routes registered (/docs/privacy-policy, /docs/terms, /docs/operator-info)")

    # ── POST /api/docs-ingest ─────────────────────────────────────

    @mcp.custom_route("/api/docs-ingest", methods=["POST"])
    async def api_docs_ingest(request: Request) -> JSONResponse:
        """Receive docs file list from SessionStart Hook for Brief Recall."""
        store, auth_err = await _authenticate_bearer(request)
        if auth_err:
            return JSONResponse({"error": auth_err}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"ok": False}, status_code=400)

        project = data.get("project", "")
        files = data.get("files", [])
        if not project or not isinstance(files, list):
            return JSONResponse({"ok": False}, status_code=400)

        from memory_mcp.templates.hook_prompts import set_docs_cache
        set_docs_cache(project, files)
        return JSONResponse({"ok": True, "count": len(files[:50])})

    # ── POST /api/cache-ingest ────────────────────────────────────

    @mcp.custom_route("/api/cache-ingest", methods=["POST"])
    async def api_cache_ingest(request: Request) -> JSONResponse:
        """Ingest local JSONL cache entries into memory store.

        Accepts raw assistant responses from the Stop Hook's local cache
        and stores them as auto-cached facts.

        Multi-user mode: requires Bearer API key (same as MCP auth).
        Single-user mode: no auth needed.

        Body: {"project": "...", "entries": [{"content": "...", "ts": "..."}]}
        """
        _t0 = time.monotonic()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        project = body.get("project", "").strip()
        entries = body.get("entries", [])
        if not project or not entries:
            return JSONResponse({"error": "project and entries required"}, status_code=400)

        # Single-user mode: use global store
        store: MemoryStore = _store()

        stored = 0
        for entry in entries[:50]:  # Cap at 50 per request
            content = entry.get("content", "").strip()
            ts = entry.get("ts", "")
            if not content or len(content) < 50:
                continue  # Skip trivial entries
            if len(content) > 3000:
                content = content[:3000] + "..."

            # importance_hint from Stop Hook's keyword detection
            # 7.0+ = important (decision/gotcha/infra), 2.0 = normal
            imp_hint = entry.get("importance_hint", 2.0)
            try:
                imp_hint = float(imp_hint)
            except (TypeError, ValueError):
                imp_hint = 2.0
            imp_hint = max(1.0, min(imp_hint, 9.0))  # clamp

            tags = ["auto-cached"]
            if imp_hint >= 7.0:
                tags.append("auto-important")

            try:
                await asyncio.to_thread(
                    store.store,
                    project=project,
                    content=content,
                    memory_type=MemoryType.FACT,
                    importance=imp_hint,
                    tags=tags,
                )
                stored += 1
            except Exception as e:
                logger.warning("cache-ingest store failed: %s", e)

        logger.info("CACHE_INGEST project=%s entries=%d stored=%d", project, len(entries), stored)
        _metrics.record("cache-ingest", (time.monotonic() - _t0) * 1000)
        return JSONResponse({"stored": stored, "total": len(entries)})

    # ── GET /dashboard ───────────────────────────────────────────

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard_page(request: Request) -> HTMLResponse:
        lang = detect_lang(request)
        return HTMLResponse(_render_dashboard_html(lang))

    # ── GET /launch-dashboard — admin only ──────────────────────

    @mcp.custom_route("/launch-dashboard", methods=["GET"])
    async def launch_dashboard_page(request: Request) -> HTMLResponse:
        """Launch preparation dashboard."""
        try:
            import os
            html_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "docs", "launch_dashboard.html"
            )
            if os.path.exists(html_path):
                with open(html_path, encoding="utf-8") as f:
                    return HTMLResponse(f.read())
            # Fallback: bundled in package
            pkg_path = os.path.join(os.path.dirname(__file__), "launch_dashboard.html")
            if os.path.exists(pkg_path):
                with open(pkg_path, encoding="utf-8") as f:
                    return HTMLResponse(f.read())
            return HTMLResponse("<h1>Launch dashboard not found</h1>", status_code=404)
        except Exception as e:
            return HTMLResponse(f"<h1>Error: {e}</h1>", status_code=500)

    logger.info("Dashboard routes registered (/api/health, /api/workspaces, /api/projects, /api/stats, /api/storage, /api/token-usage, /dashboard, /launch-dashboard)")


# ── Dashboard HTML ───────────────────────────────────────────────

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{i18n_title}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%25' stop-color='%236366f1'/><stop offset='100%25' stop-color='%2322d3ee'/></linearGradient></defs><circle cx='32' cy='32' r='30' fill='%231a1d29' stroke='url(%23g)' stroke-width='3'/><circle cx='32' cy='28' r='12' fill='none' stroke='url(%23g)' stroke-width='2.5'/><path d='M20 28c0-6.6 5.4-12 12-12s12 5.4 12 12' fill='none' stroke='%236366f1' stroke-width='2' opacity='.5'/><circle cx='28' cy='26' r='2' fill='%2322d3ee'/><circle cx='36' cy='26' r='2' fill='%2322d3ee'/><circle cx='32' cy='32' r='2' fill='%236366f1'/><line x1='28' y1='26' x2='32' y2='32' stroke='%2322d3ee' stroke-width='1' opacity='.6'/><line x1='36' y1='26' x2='32' y2='32' stroke='%2322d3ee' stroke-width='1' opacity='.6'/><line x1='28' y1='26' x2='36' y2='26' stroke='%236366f1' stroke-width='1' opacity='.4'/><rect x='22' y='42' rx='3' width='20' height='6' fill='url(%23g)' opacity='.8'/><rect x='25' y='49' rx='2' width='14' height='4' fill='url(%23g)' opacity='.5'/></svg>">
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d29;
  --surface2: #242736;
  --border: #2e3144;
  --text: #e4e4e7;
  --text2: #9ca3af;
  --accent: #6366f1;
  --accent2: #818cf8;
  --green: #22c55e;
  --yellow: #eab308;
  --red: #ef4444;
  --blue: #3b82f6;
  --cyan: #06b6d4;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }

/* Header */
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 0;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.header h1 {
  font-size: 1.5rem;
  font-weight: 600;
  background: linear-gradient(135deg, var(--accent2), var(--cyan));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.header-right { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.user-nav { display: flex; align-items: center; gap: 12px; font-size: 0.85rem; }
.user-nav .user-name { color: var(--accent2); font-weight: 500; }
.user-nav a { color: var(--text2); text-decoration: none; }
.user-nav a:hover { color: var(--accent); text-decoration: underline; }
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 500;
}
.status-badge.healthy { background: rgba(34,197,94,0.15); color: var(--green); }
.status-badge.error { background: rgba(239,68,68,0.15); color: var(--red); }
.status-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.last-updated { color: var(--text2); font-size: 0.8rem; }

/* Cards Grid */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.card-title {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text2);
  margin-bottom: 12px;
}
.card-value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--text);
}
.card-sub { font-size: 0.85rem; color: var(--text2); margin-top: 4px; }
.metric-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.metric-row:last-child { border-bottom: none; }
.metric-label { color: var(--text2); font-size: 0.85rem; }
.metric-value { font-weight: 500; font-size: 0.85rem; }

/* Section */
.section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 24px;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.section-title { font-size: 1.1rem; font-weight: 600; }

/* Table */
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left;
  padding: 10px 12px;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text2);
  border-bottom: 1px solid var(--border);
}
td {
  padding: 12px;
  font-size: 0.9rem;
  border-bottom: 1px solid var(--border);
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--surface2); }
.project-name {
  color: var(--accent2);
  cursor: pointer;
  font-weight: 500;
}
.project-name:hover { text-decoration: underline; }
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 500;
}
.badge-fact { background: rgba(59,130,246,0.15); color: var(--blue); }
.badge-decision { background: rgba(234,179,8,0.15); color: var(--yellow); }
.badge-summary { background: rgba(6,182,212,0.15); color: var(--cyan); }
.badge-snippet { background: rgba(99,102,241,0.15); color: var(--accent2); }
.badge-critical { background: rgba(239,68,68,0.15); color: var(--red); }
.badge-normal { background: rgba(34,197,94,0.15); color: var(--green); }
.badge-low { background: rgba(156,163,175,0.15); color: var(--text2); }

/* Search */
.search-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.search-bar input {
  flex: 1;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface2);
  color: var(--text);
  font-size: 0.9rem;
  outline: none;
}
.search-bar input:focus { border-color: var(--accent); }
.search-bar select {
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface2);
  color: var(--text);
  font-size: 0.9rem;
  outline: none;
}
.search-bar button {
  padding: 10px 20px;
  border-radius: 8px;
  border: none;
  background: var(--accent);
  color: white;
  font-weight: 500;
  cursor: pointer;
  font-size: 0.9rem;
}
.search-bar button:hover { background: var(--accent2); }

/* Search Results */
.search-result {
  padding: 12px;
  border-bottom: 1px solid var(--border);
}
.search-result:last-child { border-bottom: none; }
.search-result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.search-result-content {
  font-size: 0.85rem;
  color: var(--text2);
  white-space: pre-wrap;
  word-break: break-word;
}
.distance { color: var(--text2); font-size: 0.75rem; }

/* Modal */
.modal-overlay {
  display: none;
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.7);
  z-index: 100;
  justify-content: center;
  align-items: center;
}
.modal-overlay.active { display: flex; }
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  max-width: 800px;
  width: 90%;
  max-height: 80vh;
  overflow-y: auto;
}
.modal-close {
  float: right;
  background: none;
  border: none;
  color: var(--text2);
  font-size: 1.5rem;
  cursor: pointer;
}
.modal-close:hover { color: var(--text); }
.modal-title { font-size: 1.2rem; font-weight: 600; margin-bottom: 16px; }

/* Sortable headers */
th.sortable {
  cursor: pointer;
  user-select: none;
  position: relative;
  padding-right: 20px;
}
th.sortable:hover { color: var(--accent2); }
th.sortable .sort-icon {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 0.65rem;
  color: var(--text2);
}
th.sortable.sort-active .sort-icon { color: var(--accent2); }

/* Project action buttons */
.proj-actions { display: flex; gap: 4px; }
.proj-actions button {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text2);
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
}
.proj-actions button:hover { color: var(--text); border-color: var(--text2); }
.proj-actions button.btn-danger:hover { color: var(--red); border-color: var(--red); }

/* Batch selection bar */
.selection-bar {
  display: none;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  margin-bottom: 8px;
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: 6px;
  font-size: 0.85rem;
}
.selection-bar.visible { display: flex; }
.selection-bar .sel-count { color: var(--accent); font-weight: 600; }
.selection-bar button { font-size: 0.8rem; padding: 3px 10px; cursor: pointer; border-radius: 4px; }
.selection-bar .btn-cancel { background: transparent; border: 1px solid var(--border); color: var(--text2); }
.batch-cb { width: 16px; height: 16px; cursor: pointer; accent-color: var(--accent); }

/* Empty state */
.empty { text-align: center; padding: 40px; color: var(--text2); }

/* Footer */
.footer {
  text-align: center;
  padding: 16px;
  color: var(--text2);
  font-size: 0.75rem;
}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <h1 style="display:flex;align-items:center;gap:8px;"><img src="https://kandela.ai/logo.png" alt="" style="height:1.6em;width:auto;">{i18n_title}</h1>
    <div class="header-right">
      <span id="status-badge" class="status-badge healthy">
        <span class="status-dot"></span>
        <span id="status-text">{i18n_connecting}</span>
      </span>
      <span id="last-updated" class="last-updated"></span>
    </div>
  </div>

  <!-- Metric Cards -->
  <div class="cards">
    <div class="card">
      <div class="card-title">Server</div>
      <div id="server-info">
        <div class="metric-row">
          <span class="metric-label">Version</span>
          <span class="metric-value" id="m-version">-</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Uptime</span>
          <span class="metric-value" id="m-uptime">-</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Tools</span>
          <span class="metric-value" id="m-tools">-</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Memory (RSS)</span>
          <span class="metric-value" id="m-memory">-</span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Overview</div>
      <div class="card-value" id="total-memories">-</div>
      <div class="card-sub">total memories</div>
      <div style="margin-top: 12px;">
        <div class="metric-row">
          <span class="metric-label">Projects</span>
          <span class="metric-value" id="total-projects">-</span>
        </div>
        <div class="metric-row" id="type-breakdown"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Storage</div>
      <div class="card-value" id="disk-usage">-</div>
      <div class="card-sub">disk usage</div>
      <div style="margin-top: 12px;">
        <div class="metric-row">
          <span class="metric-label">Content</span>
          <span class="metric-value" id="storage-content">-</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Embeddings</span>
          <span class="metric-value" id="storage-embeddings">-</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Metadata</span>
          <span class="metric-value" id="storage-metadata">-</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Embed Dim</span>
          <span class="metric-value" id="storage-dim">-</span>
        </div>
      </div>
    </div>
    <!-- Token Economy card removed — Kandela focus is on long-term memory, not token savings -->
  </div>

  <!-- Projects Table -->
  <div class="section">
    <div class="section-header" style="cursor:pointer" onclick="toggleSection('projects-body')">
      <span class="section-title">Projects <span id="projects-toggle" style="font-size:0.75rem;color:var(--text2)">▼</span></span>
      <span style="font-size:0.85rem;margin-left:16px" onclick="event.stopPropagation()">
        <button id="btn-active" onclick="setProjectFilter('active')" style="padding:2px 10px;font-size:0.8rem;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer">Active</button>
        <button id="btn-archived" onclick="setProjectFilter('archived')" style="padding:2px 10px;font-size:0.8rem;background:var(--surface);color:var(--text2);border:1px solid var(--border);border-radius:4px;cursor:pointer">📦 Archived</button>
      </span>
    </div>
    <div id="projects-body">
      <div id="selection-bar-active" class="selection-bar">
        <span class="sel-count" data-count data-label="Selected">Selected (0)</span>
        <button onclick="batchArchive()" data-count data-label="📦 Archive">📦 Archive (0)</button>
        <button class="btn-danger" onclick="batchDelete()" data-count data-label="🗑 Delete">🗑 Delete (0)</button>
        <button class="btn-cancel" onclick="clearSelection('active')">Cancel</button>
      </div>
      <div id="selection-bar-archived" class="selection-bar">
        <span class="sel-count" data-count data-label="Selected">Selected (0)</span>
        <button onclick="batchUnarchive()" data-count data-label="Unarchive">Unarchive (0)</button>
        <button class="btn-cancel" onclick="clearSelection('archived')">Cancel</button>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width:30px"><input type="checkbox" class="batch-cb select-all" data-context="active" onchange="toggleSelectAll('active', this.checked)"></th>
            <th class="sortable" onclick="sortProjects('name')">Name <span id="sort-name"></span></th>
            <th class="sortable" onclick="sortProjects('memory_count')">Memories <span id="sort-memory_count"></span></th>
            <th class="sortable" onclick="sortProjects('storage')">Storage <span id="sort-storage"></span></th>
            <th>Types</th>
            <th class="sortable" onclick="sortProjects('last_changed')">Last Changed <span id="sort-last_changed"></span></th>
            <th>Searchable</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="projects-tbody">
          <tr><td colspan="8" class="empty">{i18n_loading}</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Performance Metrics -->
  <div class="section">
    <div class="section-header">
      <span class="section-title">Performance (last 1h)</span>
      <span id="perf-refresh" style="font-size:0.8rem;color:var(--text2);cursor:pointer" onclick="loadMetrics()">↻ refresh</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Endpoint</th><th>Count</th><th>p50 (ms)</th><th>p95 (ms)</th><th>p99 (ms)</th><th>Avg (ms)</th>
        </tr>
      </thead>
      <tbody id="perf-tbody">
        <tr><td colspan="6" class="empty">Loading...</td></tr>
      </tbody>
    </table>
    <div id="perf-meta" style="font-size:0.8rem;color:var(--text2);margin-top:8px"></div>
  </div>

  <!-- Trash -->
  <div class="section">
    <div class="section-header">
      <span class="section-title">🗑️ Trash</span>
      <span style="font-size:0.8rem;color:var(--text2);cursor:pointer" onclick="loadTrash()">↻ refresh</span>
    </div>
    <div id="trash-content" style="color:var(--text2);font-size:0.9rem;">Loading...</div>
  </div>

  <!-- Search -->
  <div class="section">
    <div class="section-header">
      <span class="section-title">Search</span>
    </div>
    <div class="search-bar">
      <input type="text" id="search-input" placeholder="{i18n_search_placeholder}">
      <select id="search-project">
        <option value="">{i18n_all_projects}</option>
      </select>
      <button onclick="doSearch()">{i18n_search_btn}</button>
    </div>
    <div id="search-results"></div>
  </div>

  <div class="footer">
    {i18n_auto_refresh} &middot; Kandela
  </div>
</div>

<!-- Project Detail Modal -->
<div class="modal-overlay" id="modal-overlay" onclick="closeModal(event)">
  <div class="modal" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <div class="modal-title" id="modal-title">Project Detail</div>
    <div id="modal-body"></div>
  </div>
</div>

<script>
{i18n_js_block}
const API = '';
let projectsCache = [];
let statsCache = {};
let tokenUsageCache = {};
let projSort = { key: 'name', dir: 'asc' };
let modalMemories = [];
let modalSort = { key: 'created_at', dir: 'desc' };

// ── Fetch helpers ────────────────────────────────

async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function typeBadge(t) {
  const safe = escapeHtml(t);
  return `<span class="badge badge-${safe}">${safe}</span>`;
}
function priorityBadge(p) {
  const safe = escapeHtml(p);
  return `<span class="badge badge-${safe}">${safe}</span>`;
}
function importanceBadge(imp) {
  if (imp == null) return '';
  const v = parseFloat(imp);
  if (isNaN(v)) return '';
  if (v >= 9.0) return '<span class="badge badge-critical">imp:' + v.toFixed(1) + '</span>';
  if (v < 3.0) return '<span class="badge badge-low">imp:' + v.toFixed(1) + '</span>';
  return '<span class="badge badge-normal">imp:' + v.toFixed(1) + '</span>';
}

function formatBytes(bytes) {
  if (bytes == null || bytes === 0) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function formatDateTime(isoStr) {
  if (!isoStr) return '-';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    // Force KST (UTC+9) regardless of browser locale
    const kst = new Date(d.getTime() + 9 * 3600000);
    const pad = n => String(n).padStart(2, '0');
    return `${kst.getUTCFullYear()}-${pad(kst.getUTCMonth()+1)}-${pad(kst.getUTCDate())} ${pad(kst.getUTCHours())}:${pad(kst.getUTCMinutes())}`;
  } catch(e) { return isoStr; }
}

function formatTokens(t) {
  if (t < 1000) return String(t);
  if (t < 1000000) return (t / 1000).toFixed(1) + 'K';
  return (t / 1000000).toFixed(1) + 'M';
}

function fmtNum(n) { return n.toLocaleString(); }

function buildFormulaBreakdown(pt) {
  const ob = pt.overhead_breakdown;
  const bb = pt.benefit_basis;
  const pr = pt.pricing;
  if (!ob || !bb) return '';
  const ar = ob.auto_recall || {};
  const gs = ob.guide_section || {};
  const ss = ob.session_summary || {};
  const cs = ob.context_search || {};
  const overTotal = pt.overhead_tokens || 0;
  const benTotal = bb.total_tokens || 0;
  const costUsd = pt.cost_usd || 0;
  const benUsd = pt.benefit_usd || 0;
  const netUsd = pt.net_saving_usd || 0;
  return `
    <details style="margin-top:8px;font-size:0.78rem;color:var(--text2);">
      <summary style="cursor:pointer;color:var(--accent2);font-weight:600;font-size:0.82rem;padding:6px 0;">Calculation Breakdown</summary>
      <div style="background:var(--surface1);border-radius:8px;padding:14px;margin-top:6px;font-family:monospace;line-height:1.9;">
        <div style="color:var(--red);font-weight:600;margin-bottom:4px;">Overhead (Memory System Cost)</div>
        <div>auto_recall&nbsp;&nbsp;&nbsp;${fmtNum(ar.per_unit||0)} tok x ${ar.count||0} sessions = <strong>${fmtNum(ar.total||0)}</strong> tok</div>
        <div>guide_section&nbsp;${fmtNum(gs.per_unit||0)} tok x ${gs.count||0} sessions = <strong>${fmtNum(gs.total||0)}</strong> tok</div>
        <div>session_save&nbsp;&nbsp;${fmtNum(ss.per_unit||0)} tok x ${ss.count||0} sessions = <strong>${fmtNum(ss.total||0)}</strong> tok</div>
        <div>context_search ${fmtNum(cs.per_unit||0)} tok x ${cs.count||0} searches = <strong>${fmtNum(cs.total||0)}</strong> tok</div>
        <div style="border-top:1px solid var(--border);margin:6px 0;padding-top:6px;">
          Total Overhead: <strong style="color:var(--red);">${fmtNum(overTotal)}</strong> tok x $${pr?.overhead_per_m||3}/M = <strong style="color:var(--red);">$${costUsd.toFixed(3)}</strong>
        </div>

        <div style="color:var(--green);font-weight:600;margin:10px 0 4px;">Estimated Saving</div>
        <div>V2 benchmark measured: <strong>19,560</strong> tok/session saved</div>
        <div>Conservative 60%: <strong>${fmtNum(bb.applied_per_session||0)}</strong> tok/session</div>
        <div>${fmtNum(bb.applied_per_session||0)} tok x ${bb.session_count||0} sessions = <strong>${fmtNum(benTotal)}</strong> tok</div>
        <div style="border-top:1px solid var(--border);margin:6px 0;padding-top:6px;">
          Total Saving: <strong style="color:var(--green);">${fmtNum(benTotal)}</strong> tok x $${pr?.benefit_per_m||9}/M = <strong style="color:var(--green);">$${benUsd.toFixed(3)}</strong>
        </div>

        <div style="border-top:2px solid var(--border);margin:8px 0;padding-top:8px;font-size:0.85rem;">
          <strong style="color:var(--green);">Net Saving: $${benUsd.toFixed(3)} - $${costUsd.toFixed(3)} = $${netUsd.toFixed(3)}</strong>
        </div>

        <div style="margin-top:10px;padding:8px;background:var(--surface2);border-radius:6px;font-family:inherit;font-size:0.72rem;line-height:1.6;opacity:0.8;">
          <strong>V2 A/B Benchmark (6 runs, seeds 42/123/456)</strong><br>
          A(memory ON): 290,700 tok, $2.18, accuracy 98.3%<br>
          B(memory OFF): 408,057 tok, $3.28, accuracy 83.7%<br>
          Token reduction: 28.8% | Cost reduction: 33.7% | Accuracy: +14.6pp
        </div>
      </div>
    </details>`;
}

function updateProjectTokenCells() {
  // Token economy columns removed — no-op
}

const PRIORITY_ORDER = { critical: 0, normal: 1, low: 2 };
const TYPE_ORDER = { fact: 0, decision: 1, snippet: 2, summary: 3 };

function sortMemories(memories, key, dir) {
  return [...memories].sort((a, b) => {
    let va, vb;
    if (key === 'type') {
      va = TYPE_ORDER[a.type] ?? 99;
      vb = TYPE_ORDER[b.type] ?? 99;
    } else if (key === 'priority') {
      va = PRIORITY_ORDER[a.priority] ?? 99;
      vb = PRIORITY_ORDER[b.priority] ?? 99;
    } else if (key === 'importance') {
      va = a.importance ?? 5.0;
      vb = b.importance ?? 5.0;
    } else {
      va = a.created_at || '';
      vb = b.created_at || '';
    }
    if (va < vb) return dir === 'asc' ? -1 : 1;
    if (va > vb) return dir === 'asc' ? 1 : -1;
    return 0;
  });
}

// ── Password validation ───────────────────────────
function validatePassword(pw) {
  if (pw.length < 8) return 'Password must be at least 8 characters';
  if (!/[A-Z]/.test(pw)) return 'Password must contain at least one uppercase letter';
  if (!/[a-z]/.test(pw)) return 'Password must contain at least one lowercase letter';
  if (!/[0-9]/.test(pw)) return 'Password must contain at least one digit';
  if (!/[^A-Za-z0-9]/.test(pw)) return 'Password must contain at least one special character';
  return null;
}

// ── Update functions ─────────────────────────────

async function updateHealth() {
  try {
    const d = await fetchJSON('/api/health');
    document.getElementById('m-version').textContent = 'v' + d.version;
    document.getElementById('m-uptime').textContent = d.uptime_human;
    document.getElementById('m-tools').textContent = d.tool_count ?? '-';
    document.getElementById('m-memory').textContent = d.memory_mb + ' MB';
    document.getElementById('status-text').textContent = I18N.healthy;
    const badge = document.getElementById('status-badge');
    badge.className = 'status-badge healthy';
  } catch(e) {
    document.getElementById('status-text').textContent = I18N.error;
    document.getElementById('status-badge').className = 'status-badge error';
  }
}

async function updateProjects() {
  try {
    const d = await fetchJSON('/api/projects');
    projectsCache = d.projects;
    document.getElementById('total-projects').textContent = d.count;

    // Update project select in search
    const sel = document.getElementById('search-project');
    const curVal = sel.value;
    sel.innerHTML = '<option value="">' + I18N.all_projects + '</option>';
    d.projects.forEach(p => {
      sel.innerHTML += `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`;
    });
    sel.value = curVal;

    renderProjectsTable();
  } catch(e) {
    console.error('Failed to fetch projects:', e);
  }
}

function renderProjectsTable() {
    const tbody = document.getElementById('projects-tbody');
    // Reset selection bar when table re-renders
    const _activeBar = document.getElementById('selection-bar-active');
    if (_activeBar) _activeBar.classList.remove('visible');
    _batchSelecting = false;
    const _selectAllCb = document.querySelector('.batch-cb.select-all[data-context="active"]');
    if (_selectAllCb) _selectAllCb.checked = false;
    // Show active header checkbox, hide archived
    if (_selectAllCb) _selectAllCb.closest('th').style.display = '';
    if (projectsCache.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">' + I18N.no_projects + '</td></tr>';
      return;
    }
    // Sort
    const sorted = [...projectsCache].sort((a, b) => {
      let va, vb;
      const k = projSort.key;
      if (k === 'name') { va = a.name.toLowerCase(); vb = b.name.toLowerCase(); }
      else if (k === 'memory_count') { va = a.memory_count; vb = b.memory_count; }
      else if (k === 'storage') { va = a.total_estimated_bytes || 0; vb = b.total_estimated_bytes || 0; }
      else if (k === 'overhead') {
        const ai = tokenUsageCache[a.name]; const bi = tokenUsageCache[b.name];
        va = ai ? ai.overhead_tokens || 0 : 0; vb = bi ? bi.overhead_tokens || 0 : 0;
      } else if (k === 'net_saving') {
        const ai = tokenUsageCache[a.name]; const bi = tokenUsageCache[b.name];
        va = ai ? ai.net_saving_usd || 0 : 0; vb = bi ? bi.net_saving_usd || 0 : 0;
      } else if (k === 'last_changed') { va = a.last_changed || ''; vb = b.last_changed || ''; }
      else { va = a.name; vb = b.name; }
      if (va < vb) return projSort.dir === 'asc' ? -1 : 1;
      if (va > vb) return projSort.dir === 'asc' ? 1 : -1;
      return 0;
    });
    // Update sort indicators
    ['name','memory_count','storage','overhead','net_saving','last_changed'].forEach(k => {
      const el = document.getElementById('sort-' + k);
      if (el) el.textContent = projSort.key === k ? (projSort.dir === 'asc' ? '\u25b2' : '\u25bc') : '';
    });
    tbody.innerHTML = '';
    sorted.forEach(p => {
      const tr = document.createElement('tr');
      const storageStr = formatBytes(p.total_estimated_bytes || 0);
      const lcStr = p.last_changed ? timeAgo(p.last_changed) : '-';
      const eName = p.name.replace(/'/g, "\\'");
      const safeName = escapeHtml(p.name);
      const safeEName = escapeHtml(eName);
      tr.innerHTML = `
        <td><input type="checkbox" class="batch-cb" data-context="active" data-project="${safeName}" onchange="updateSelectionBar('active')"></td>
        <td><span class="project-name" onclick="showProject('${safeEName}')">${safeName}</span></td>
        <td>${parseInt(p.memory_count) || 0}</td>
        <td><span style="color:var(--cyan);font-size:0.85rem;">${escapeHtml(storageStr)}</span></td>
        <td>-</td>
        <td style="font-size:0.85rem;color:var(--text2);">${escapeHtml(lcStr)}</td>
        <td><input type="checkbox" checked onchange="toggleSearchable('${safeEName}', this.checked)" title="Cross-project searchable"></td>
        <td><div class="proj-actions">
          <button onclick="renameProject('${safeEName}')">${I18N.rename}</button>
          <button onclick="archiveProject('${safeEName}')" title="Archive">📦</button>
          <button class="btn-danger" onclick="deleteProject('${safeEName}')">${I18N.delete_btn}</button>
        </div></td>
      `;
      tbody.appendChild(tr);
    });
    updateProjectTokenCells();
    // Re-apply type badges from statsCache
    if (statsCache.projects) {
      const rows = tbody.querySelectorAll('tr');
      Object.entries(statsCache.projects).forEach(([projName, projStats]) => {
        rows.forEach(row => {
          const nameEl = row.querySelector('.project-name');
          if (nameEl && nameEl.textContent === projName) {
            const byType = projStats.by_type || {};
            const badges = Object.entries(byType).map(([t, c]) => `${typeBadge(t)} ${parseInt(c) || 0}`).join('  ');
            row.cells[3].innerHTML = badges || '-';
          }
        });
      });
    }
}

function sortProjects(key) {
  if (projSort.key === key) { projSort.dir = projSort.dir === 'asc' ? 'desc' : 'asc'; }
  else { projSort.key = key; projSort.dir = key === 'name' ? 'asc' : 'desc'; }
  renderProjectsTable();
}

function timeAgo(iso) {
  const d = new Date(iso);
  const now = new Date();
  const sec = Math.floor((now - d) / 1000);
  if (sec < 60) return 'just now';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  if (sec < 604800) return Math.floor(sec/86400) + 'd ago';
  return d.toLocaleDateString();
}

async function renameProject(name) {
  const newName = prompt('Rename project "' + name + '" to:', name);
  if (!newName || newName === name) return;
  try {
    const res = await fetch('/api/projects/' + encodeURIComponent(name) + '/rename', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ new_name: newName })
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Rename failed'); return; }
    updateProjects();
    updateStats();
  } catch { alert('Network error'); }
}

async function deleteProject(name) {
  if (!confirm('Move project "' + name + '" to trash? You can restore it later.')) return;
  // Instant UI feedback: remove row
  document.querySelectorAll('#projects-tbody .project-name').forEach(el => {
    if (el.textContent === name) el.closest('tr').style.display = 'none';
  });
  try {
    const res = await fetch('/api/projects/' + encodeURIComponent(name) + '/delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'}
    });
    if (!res.ok) { const d = await res.json(); alert(d.error || 'Delete failed'); }
  } catch { alert('Network error'); }
  // Background refresh
  updateProjects(); updateStats(); loadTrash();
}

async function updateStats() {
  try {
    const d = await fetchJSON('/api/stats');
    statsCache = d;
    document.getElementById('total-memories').textContent = d.total_memories ?? 0;
    renderProjectsTable();
  } catch(e) {
    console.error('Failed to fetch stats:', e);
  }
}

async function updateStorage() {
  try {
    const d = await fetchJSON('/api/storage');
    document.getElementById('disk-usage').textContent = d.disk_usage_human || '-';
    document.getElementById('storage-content').textContent = d.totals?.content_human || '-';
    document.getElementById('storage-embeddings').textContent = d.totals?.embedding_human || '-';
    document.getElementById('storage-metadata').textContent =
      formatBytes(d.totals?.metadata_bytes || 0);
    document.getElementById('storage-dim').textContent = d.totals?.embedding_dim || '-';
  } catch(e) {
    console.error('Failed to fetch storage:', e);
  }
}

async function updateTokenUsage() {
  try {
    const d = await fetchJSON('/api/token-usage');
    // Token Economy UI removed — only cache data for project detail view
    tokenUsageCache = d.projects || {};
  } catch(e) {
    console.error('Failed to fetch token usage:', e);
  }
}

// ── Batch selection utilities ──────────────────────
let _batchSelecting = false;

function toggleSelectAll(context, checked) {
  const cbs = document.querySelectorAll(`.batch-cb[data-context="${context}"]`);
  cbs.forEach(cb => { cb.checked = checked; });
  updateSelectionBar(context);
}

function updateSelectionBar(context) {
  const cbs = document.querySelectorAll(`.batch-cb[data-context="${context}"]:not(.select-all)`);
  const checked = document.querySelectorAll(`.batch-cb[data-context="${context}"]:checked:not(.select-all)`);
  const count = checked.length;
  const bar = document.getElementById('selection-bar-' + context);
  if (!bar) return;
  if (count === 0) {
    bar.classList.remove('visible');
    _batchSelecting = false;
  } else {
    bar.classList.add('visible');
    _batchSelecting = true;
  }
  // Update count in buttons
  bar.querySelectorAll('[data-count]').forEach(el => {
    el.textContent = el.getAttribute('data-label') + ' (' + count + ')';
  });
  // Sync select-all checkbox
  const selectAll = document.querySelector(`.batch-cb.select-all[data-context="${context}"]`);
  if (selectAll) selectAll.checked = cbs.length > 0 && count === cbs.length;
}

function getSelectedItems(context) {
  const checked = document.querySelectorAll(`.batch-cb[data-context="${context}"]:checked:not(.select-all)`);
  if (context === 'trash-mem') {
    return Array.from(checked).map(cb => ({ project: cb.dataset.project, id: cb.dataset.id }));
  }
  return Array.from(checked).map(cb => cb.dataset.project);
}

function clearSelection(context) {
  toggleSelectAll(context, false);
  const selectAll = document.querySelector(`.batch-cb.select-all[data-context="${context}"]`);
  if (selectAll) selectAll.checked = false;
}

async function runBatch(items, apiFn) {
  // Chunked parallel: max 5 concurrent
  const results = [];
  for (let i = 0; i < items.length; i += 5) {
    const chunk = items.slice(i, i + 5);
    const r = await Promise.allSettled(chunk.map(apiFn));
    results.push(...r);
  }
  const failed = results.filter(r => r.status === 'rejected');
  if (failed.length > 0) {
    alert(failed.length + ' operation(s) failed.');
  }
  return results;
}

async function refreshAll() {
  // Skip refresh while user is selecting items
  if (_batchSelecting) {
    document.getElementById('last-updated').textContent =
      I18N.last_updated + ': ' + new Date().toLocaleTimeString() + ' (selection active)';
    return;
  }
  await Promise.all([updateHealth(), updateStorage(), updateTokenUsage()]);
  await updateProjects();   // builds rows first
  await updateStats();      // then fills type badges into those rows
  // Respect current filter — don't overwrite archived view
  if (projectFilter === 'archived') loadArchivedProjects();
  document.getElementById('last-updated').textContent =
    I18N.last_updated + ': ' + new Date().toLocaleTimeString();
}

// ── Search ───────────────────────────────────────

async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  const project = document.getElementById('search-project').value;
  let url = `/api/search?q=${encodeURIComponent(q)}`;
  if (project) url += `&project=${encodeURIComponent(project)}`;

  const container = document.getElementById('search-results');
  container.innerHTML = '<div class="empty">' + I18N.searching + '</div>';

  try {
    const d = await fetchJSON(url);
    if (d.count === 0) {
      container.innerHTML = '<div class="empty">' + I18N.no_results + '</div>';
      return;
    }
    container.innerHTML = d.results.map(r => {
      const scoreStr = r.retrieval_score != null
        ? `score: ${r.retrieval_score}`
        : `distance: ${r.distance}`;
      return `
      <div class="search-result">
        <div class="search-result-header">
          <span>
            ${typeBadge(r.type || 'unknown')}
            ${importanceBadge(r.importance)}
            <span style="color:var(--text2);font-size:0.8rem;margin-left:8px;">${escapeHtml(r.project || '')}</span>
          </span>
          <span class="distance">${escapeHtml(scoreStr)}</span>
        </div>
        <div class="search-result-content">${escapeHtml(r.content)}</div>
        <div style="margin-top:4px;font-size:0.75rem;color:var(--text2);">${escapeHtml(formatDateTime(r.created_at))}</div>
      </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = '<div class="empty">' + I18N.search_failed + '</div>';
  }
}

document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

// ── Project Detail Modal ─────────────────────────

function renderModalTable() {
  const body = document.getElementById('modal-body');
  const sorted = sortMemories(modalMemories, modalSort.key, modalSort.dir);

  function sortHeader(label, key) {
    const active = modalSort.key === key;
    const icon = active ? (modalSort.dir === 'asc' ? '&#9650;' : '&#9660;') : '&#8693;';
    const cls = active ? 'sortable sort-active' : 'sortable';
    return `<th class="${cls}" onclick="toggleSort('${key}')">
      ${label}<span class="sort-icon">${icon}</span>
    </th>`;
  }

  const summaryEl = body.querySelector('.modal-summary');
  const summaryHTML = summaryEl ? summaryEl.outerHTML : '';

  let html = summaryHTML + `
    <table>
      <thead><tr>
        ${sortHeader('Type', 'type')}
        ${sortHeader('Importance', 'importance')}
        <th>Content</th>
        ${sortHeader('Created', 'created_at')}
      </tr></thead>
      <tbody>
  `;
  sorted.forEach(m => {
    html += `<tr>
      <td>${typeBadge(m.type || 'unknown')}</td>
      <td>${importanceBadge(m.importance)}</td>
      <td style="max-width:400px;word-break:break-word;font-size:0.85rem;">${escapeHtml(m.content)}</td>
      <td style="font-size:0.75rem;color:var(--text2);white-space:nowrap;">${escapeHtml(formatDateTime(m.created_at))}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  body.innerHTML = html;
}

function toggleSort(key) {
  if (modalSort.key === key) {
    modalSort.dir = modalSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    modalSort.key = key;
    modalSort.dir = key === 'created_at' ? 'desc' : 'asc';
  }
  renderModalTable();
}

async function showProject(name) {
  const overlay = document.getElementById('modal-overlay');
  const title = document.getElementById('modal-title');
  const body = document.getElementById('modal-body');

  title.textContent = name;
  body.innerHTML = '<div class="empty">' + I18N.loading + '</div>';
  overlay.classList.add('active');
  modalSort = { key: 'created_at', dir: 'desc' };

  try {
    const [d, tokenData] = await Promise.all([
      fetchJSON(`/api/projects/${encodeURIComponent(name)}`),
      fetchJSON('/api/token-usage').catch(() => null),
    ]);
    modalMemories = d.recent_memories || [];

    const st = d.storage || {};
    const projToken = tokenData?.projects?.[name] || {};

    // Compute token overhead vs saving for pie chart (V2 benchmark-verified)
    const overheadTokens = projToken.overhead_tokens || 0;
    const savingTokens = projToken.estimated_benefit_tokens || 0;
    const totalTokens = overheadTokens + savingTokens;
    const overheadPct = totalTokens > 0 ? (overheadTokens / totalTokens * 100).toFixed(1) : 0;
    const savingPct = totalTokens > 0 ? (savingTokens / totalTokens * 100).toFixed(1) : 0;
    const costUsd = projToken.cost_usd_human || '$0';
    const benefitUsd = projToken.benefit_usd_human || '$0';
    const netUsd = projToken.net_saving_usd_human || '$0';

    const pieChartHTML = totalTokens > 0 ? `
      <div style="display:flex;align-items:center;gap:24px;margin-top:12px;padding:16px;background:var(--surface2);border-radius:10px;">
        <div style="position:relative;width:110px;height:110px;flex-shrink:0;">
          <div style="width:110px;height:110px;border-radius:50%;background:conic-gradient(var(--red) 0% ${overheadPct}%, var(--green) ${overheadPct}% 100%);box-shadow:0 0 12px rgba(34,197,94,0.15);"></div>
          <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:64px;height:64px;border-radius:50%;background:var(--surface2);display:flex;align-items:center;justify-content:center;flex-direction:column;">
            <span style="font-size:1rem;font-weight:700;color:var(--green);">${escapeHtml(String(projToken.roi || 0))}x</span>
            <span style="font-size:0.55rem;color:var(--text2);">ROI</span>
          </div>
        </div>
        <div style="font-size:0.82rem;color:var(--text2);line-height:1.8;">
          <div style="font-weight:600;margin-bottom:6px;color:var(--text);font-size:0.9rem;">Token Economy</div>
          <div><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--red);margin-right:8px;vertical-align:middle;"></span>Overhead: <strong style="color:var(--text);">${formatTokens(overheadTokens)}</strong> tok <span style="opacity:0.7;">(${overheadPct}%)</span> ≈ ${escapeHtml(costUsd)}</div>
          <div><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--green);margin-right:8px;vertical-align:middle;"></span>Saving: <strong style="color:var(--green);">${formatTokens(savingTokens)}</strong> tok <span style="opacity:0.7;">(${savingPct}%)</span> ≈ ${escapeHtml(benefitUsd)}</div>
          <div style="margin-top:4px;font-size:0.75rem;"><strong style="color:var(--green);">Net: ${escapeHtml(netUsd)}</strong></div>
        </div>
      </div>
      ${buildFormulaBreakdown(projToken)}
    ` : '<div style="margin-top:12px;padding:14px;background:var(--surface2);border-radius:10px;font-size:0.85rem;color:var(--text2);">No token data yet — use auto_recall to start tracking benefits.</div>';

    body.innerHTML = `
      <div class="modal-summary" style="margin-bottom:16px;">
        <strong>Total memories:</strong> ${parseInt(d.total_memories) || 0}
        &nbsp;&middot;&nbsp;
        ${Object.entries(d.by_type || {}).map(([t,c]) => `${typeBadge(t)} ${parseInt(c) || 0}`).join('  ')}
        <div style="margin-top:8px;font-size:0.85rem;color:var(--text2);">
          <span style="color:var(--cyan);">Storage:</span>
          Content ${escapeHtml(formatBytes(st.content_bytes||0))}
          &middot; Embeddings ${escapeHtml(formatBytes(st.embedding_bytes||0))}
          &middot; Metadata ${escapeHtml(formatBytes(st.metadata_bytes||0))}
          &middot; <strong>Total ≈ ${escapeHtml(formatBytes(st.total_estimated_bytes||0))}</strong>
        </div>
        <div style="margin-top:6px;font-size:0.85rem;color:var(--text2);">
          <span style="color:var(--accent2);">Tokens:</span>
          Stored ≈ ${escapeHtml(projToken.stored_tokens_human || '-')}
          &middot; Overhead ${escapeHtml(projToken.overhead_tokens_human || '0')} tok
          &middot; Recalls ${parseInt(projToken.total_recalls) || 0}
          &middot; Searches ${parseInt(projToken.total_searches) || 0}
          &middot; <span style="color:var(--green);">Net ${escapeHtml(projToken.net_saving_usd_human || '-')}</span>
          &middot; ROI: ${escapeHtml(String(projToken.roi || '-'))}x
        </div>
        ${pieChartHTML}
      </div>
    `;
    renderModalTable();
  } catch(e) {
    body.innerHTML = '<div class="empty">' + I18N.load_failed + '</div>';
  }
}

function closeModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('modal-overlay').classList.remove('active');
}

// ── Utils ────────────────────────────────────────

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML.replace(/'/g, '&#39;');
}

// ── Performance Metrics ──────────────────────────
async function loadMetrics() {
  try {
    const d = await fetchJSON('/api/metrics');
    const tbody = document.getElementById('perf-tbody');
    const eps = d.endpoints || {};
    if (Object.keys(eps).length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No data yet</td></tr>';
    } else {
      tbody.innerHTML = '';
      Object.entries(eps).sort((a,b) => b[1].count - a[1].count).forEach(([ep, s]) => {
        const p95color = s.p95_ms > 2000 ? 'var(--err)' : s.p95_ms > 500 ? 'var(--yellow)' : 'var(--green)';
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${escapeHtml(ep)}</td><td>${s.count}</td><td>${s.p50_ms}</td><td style="color:${p95color}">${s.p95_ms}</td><td>${s.p99_ms}</td><td>${s.avg_ms}</td>`;
        tbody.appendChild(tr);
      });
    }
    const meta = document.getElementById('perf-meta');
    meta.textContent = `Uptime: ${Math.round(d.uptime_seconds/60)}m | RSS: ${d.memory_rss_mb}MB | Stores: ${d.active_stores} | GC: ${(d.gc_counts||[]).join('/')}`;
  } catch(e) { console.error('metrics load failed', e); }
}

// ── Searchable Toggle ────────────────────────────
async function toggleSearchable(project, checked) {
  try {
    await fetch('/api/project-settings/' + encodeURIComponent(project), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({searchable: checked})
    });
  } catch(e) { console.error('toggle searchable failed', e); }
}

// ── Init ─────────────────────────────────────────

// ── Trash / Archive ─────────────────────────────
async function loadTrash() {
  const el = document.getElementById('trash-content');
  try {
    const r = await fetch('/api/trash?limit=50');
    if (r.status === 401) { el.innerHTML = '<div style="color:var(--text2)">Login required</div>'; return; }
    const d = await r.json();
    const hasItems = d.items && d.items.length > 0;
    const hasProjects = d.deleted_projects && d.deleted_projects.length > 0;
    if (!hasItems && !hasProjects) {
      el.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text2);">Trash is empty</div>';
      return;
    }
    let html = '';
    // Deleted projects
    if (hasProjects) {
      html += '<div style="margin-bottom:12px"><strong style="color:var(--text2)">Deleted Projects</strong>';
      html += ' <span id="selection-bar-trash-proj" class="selection-bar" style="display:inline-flex;margin-left:12px;padding:3px 8px">';
      html += '<button onclick="batchRestoreProjects()" data-count data-label="Restore" style="font-size:0.75rem;padding:2px 8px">Restore (0)</button>';
      html += '</span></div>';
      html += '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px">';
      d.deleted_projects.forEach(p => {
        const dt = new Date(p.deleted_at).toLocaleDateString();
        html += `<div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 12px;display:flex;align-items:center;gap:8px">
          <input type="checkbox" class="batch-cb" data-context="trash-proj" data-project="${escapeHtml(p.project)}" onchange="updateSelectionBar('trash-proj')">
          <span style="font-weight:600">${escapeHtml(p.project)}</span>
          <span style="font-size:0.75rem;color:var(--text2)">${dt}</span>
          <button onclick="restoreProject('${escapeHtml(p.project)}')" style="font-size:0.75rem;padding:2px 8px">Restore</button>
        </div>`;
      });
      html += '</div>';
    }
    if (!hasItems) { el.innerHTML = html; return; }
    // Individual deleted memories — selection bar
    html += '<div id="selection-bar-trash-mem" class="selection-bar" style="margin-bottom:8px">';
    html += '<span class="sel-count" data-count data-label="Selected">Selected (0)</span>';
    html += '<button onclick="batchRestoreMemories()" data-count data-label="Restore">Restore (0)</button>';
    html += '<button class="btn-danger" onclick="batchPurgeMemories()" data-count data-label="Purge">Purge (0)</button>';
    html += '</div>';
    html += '<table><thead><tr>';
    html += '<th style="width:30px"><input type="checkbox" class="batch-cb select-all" data-context="trash-mem" onchange="toggleSelectAll(&quot;trash-mem&quot;, this.checked)"></th>';
    html += '<th>Project</th><th>Content</th><th>Type</th><th>Deleted</th><th>Actions</th></tr></thead><tbody>';
    d.items.forEach(item => {
      const dt = new Date(item.deleted_ts * 1000).toLocaleDateString();
      const content = escapeHtml((item.content || '').substring(0, 60));
      html += `<tr>
        <td><input type="checkbox" class="batch-cb" data-context="trash-mem" data-project="${escapeHtml(item.project)}" data-id="${escapeHtml(item.id)}" onchange="updateSelectionBar('trash-mem')"></td>
        <td style="font-size:0.85rem">${escapeHtml(item.project)}</td>
        <td style="font-size:0.85rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${content}</td>
        <td><span class="badge">${escapeHtml(item.type)}</span></td>
        <td style="font-size:0.8rem;color:var(--text2)">${dt}</td>
        <td>
          <button onclick="restoreMemory('${escapeHtml(item.project)}','${escapeHtml(item.id)}')" style="font-size:0.75rem;padding:2px 8px;">Restore</button>
          <button class="btn-danger" onclick="purgeMemory('${escapeHtml(item.project)}','${escapeHtml(item.id)}')" style="font-size:0.75rem;padding:2px 8px;">Purge</button>
        </td>
      </tr>`;
    });
    html += '</tbody></table>';
    html += `<div style="margin-top:8px;text-align:right"><button class="btn-danger" onclick="purgeAllTrash()" style="font-size:0.8rem">Empty Trash (${d.count})</button></div>`;
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<div style="color:var(--error)">Failed to load trash</div>';
  }
}

async function restoreMemory(project, id) {
  // Instant: hide row
  event?.target?.closest('tr')?.remove();
  fetch('/api/trash/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project, memory_id:id})})
    .then(() => { loadTrash(); refreshAll(); });
}

async function purgeMemory(project, id) {
  if (!confirm('Permanently delete this memory? This cannot be undone.')) return;
  event?.target?.closest('tr')?.remove();
  fetch('/api/trash/purge', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project, memory_id:id})})
    .then(() => loadTrash());
}

async function purgeAllTrash() {
  if (!confirm('Empty all trash? This permanently deletes everything in trash.')) return;
  await fetch('/api/trash/purge', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})});
  loadTrash();
  refreshAll();
}

let projectFilter = 'active';
function setProjectFilter(filter) {
  projectFilter = filter;
  // Clear selections on view switch
  clearSelection('active');
  clearSelection('archived');
  // Toggle selection bar visibility
  document.getElementById('selection-bar-active').style.display = filter === 'active' ? '' : 'none';
  document.getElementById('selection-bar-archived').style.display = filter === 'archived' ? '' : 'none';
  const btnA = document.getElementById('btn-active');
  const btnB = document.getElementById('btn-archived');
  btnA.style.background = filter === 'active' ? 'var(--accent)' : 'var(--surface)';
  btnA.style.color = filter === 'active' ? '#fff' : 'var(--text2)';
  btnA.style.border = filter === 'active' ? 'none' : '1px solid var(--border)';
  btnB.style.background = filter === 'archived' ? 'var(--accent)' : 'var(--surface)';
  btnB.style.color = filter === 'archived' ? '#fff' : 'var(--text2)';
  btnB.style.border = filter === 'archived' ? 'none' : '1px solid var(--border)';
  if (filter === 'archived') {
    loadArchivedProjects();
  } else {
    renderProjectsTable();
  }
}

async function loadArchivedProjects() {
  const tbody = document.getElementById('projects-tbody');
  try {
    const allData = await fetchJSON('/api/projects?include_archived=true');
    const activeData = await fetchJSON('/api/projects');
    const activeNames = new Set(activeData.projects.map(p => p.name));
    const archived = allData.projects.filter(p => !activeNames.has(p.name));
    if (archived.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No archived projects</td></tr>';
      return;
    }
    tbody.innerHTML = '';
    archived.forEach(p => {
      const tr = document.createElement('tr');
      tr.style.opacity = '0.7';
      const safeName = escapeHtml(p.name);
      tr.innerHTML = `
        <td><input type="checkbox" class="batch-cb" data-context="archived" data-project="${safeName}" onchange="updateSelectionBar('archived')"></td>
        <td><span style="color:var(--text2)">📦 ${safeName}</span></td>
        <td>${parseInt(p.memory_count) || 0}</td>
        <td>-</td>
        <td style="color:var(--text2)">Archived</td>
        <td>-</td>
        <td></td>
        <td><button onclick="unarchiveProject('${safeName}')" style="font-size:0.8rem;padding:2px 10px">Unarchive</button></td>
      `;
      tbody.appendChild(tr);
    });
    // Update header: show archived select-all checkbox
    const thCb = document.querySelector('.batch-cb.select-all[data-context="active"]');
    if (thCb) {
      thCb.dataset.context = 'archived';
      thCb.checked = false;
      thCb.onchange = function() { toggleSelectAll('archived', this.checked); };
    }
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">Failed to load</td></tr>';
  }
}

async function restoreProject(name) {
  // Instant: hide from trash
  event?.target?.closest('div')?.remove();
  fetch(`/api/projects/${encodeURIComponent(name)}/restore`, {method:'POST'})
    .then(() => { loadTrash(); refreshAll(); });
}

async function archiveProject(name) {
  if (!confirm(`Archive project "${name}"? It will be hidden from lists and search.`)) return;
  // Instant: hide row
  document.querySelectorAll('#projects-tbody .project-name').forEach(el => {
    if (el.textContent === name) el.closest('tr').style.display = 'none';
  });
  fetch(`/api/projects/${encodeURIComponent(name)}/archive`, {method:'POST'})
    .then(r => r.json()).then(d => { if (d.error) alert(d.error); })
    .finally(() => refreshAll());
}

async function unarchiveProject(name) {
  event?.target?.closest('tr')?.remove();
  fetch(`/api/projects/${encodeURIComponent(name)}/unarchive`, {method:'POST'})
    .then(r => r.json()).then(d => { if (d.error) alert(d.error); })
    .finally(() => refreshAll());
}

// ── Batch operations ──────────────────────────────

async function batchArchive() {
  const names = getSelectedItems('active');
  if (names.length === 0) return;
  if (names.length >= 5 && !confirm('Archive ' + names.length + ' projects?')) return;
  // Instant UI: hide selected rows
  document.querySelectorAll('.batch-cb[data-context="active"]:checked:not(.select-all)').forEach(cb => {
    cb.closest('tr').style.display = 'none';
  });
  clearSelection('active');
  await runBatch(names, name =>
    fetch(`/api/projects/${encodeURIComponent(name)}/archive`, {method:'POST'}).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || 'Failed'); });
    })
  );
  _batchSelecting = false;
  refreshAll();
}

async function batchDelete() {
  const names = getSelectedItems('active');
  if (names.length === 0) return;
  if (!confirm('Move ' + names.length + ' project(s) to trash?')) return;
  document.querySelectorAll('.batch-cb[data-context="active"]:checked:not(.select-all)').forEach(cb => {
    cb.closest('tr').style.display = 'none';
  });
  clearSelection('active');
  await runBatch(names, name =>
    fetch(`/api/projects/${encodeURIComponent(name)}/delete`, {method:'POST', headers:{'Content-Type':'application/json'}}).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || 'Failed'); });
    })
  );
  _batchSelecting = false;
  refreshAll(); loadTrash();
}

async function batchUnarchive() {
  const names = getSelectedItems('archived');
  if (names.length === 0) return;
  document.querySelectorAll('.batch-cb[data-context="archived"]:checked:not(.select-all)').forEach(cb => {
    cb.closest('tr').style.display = 'none';
  });
  clearSelection('archived');
  await runBatch(names, name =>
    fetch(`/api/projects/${encodeURIComponent(name)}/unarchive`, {method:'POST'}).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || 'Failed'); });
    })
  );
  _batchSelecting = false;
  refreshAll();
}

async function batchRestoreProjects() {
  const names = getSelectedItems('trash-proj');
  if (names.length === 0) return;
  document.querySelectorAll('.batch-cb[data-context="trash-proj"]:checked').forEach(cb => {
    cb.closest('div[style]')?.remove();
  });
  await runBatch(names, name =>
    fetch(`/api/projects/${encodeURIComponent(name)}/restore`, {method:'POST'}).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || 'Failed'); });
    })
  );
  _batchSelecting = false;
  loadTrash(); refreshAll();
}

async function batchRestoreMemories() {
  const items = getSelectedItems('trash-mem');
  if (items.length === 0) return;
  document.querySelectorAll('.batch-cb[data-context="trash-mem"]:checked').forEach(cb => {
    cb.closest('tr')?.remove();
  });
  await runBatch(items, item =>
    fetch('/api/trash/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project:item.project, memory_id:item.id})}).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || 'Failed'); });
    })
  );
  _batchSelecting = false;
  loadTrash(); refreshAll();
}

async function batchPurgeMemories() {
  const items = getSelectedItems('trash-mem');
  if (items.length === 0) return;
  if (!confirm('Permanently delete ' + items.length + ' memory(s)? This cannot be undone.')) return;
  document.querySelectorAll('.batch-cb[data-context="trash-mem"]:checked').forEach(cb => {
    cb.closest('tr')?.remove();
  });
  await runBatch(items, item =>
    fetch('/api/trash/purge', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project:item.project, memory_id:item.id})}).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || 'Failed'); });
    })
  );
  _batchSelecting = false;
  loadTrash();
}

// ── Section collapse/expand ───────────────────────
function toggleSection(bodyId) {
  const body = document.getElementById(bodyId);
  if (!body) return;
  const toggleId = bodyId.replace('-body', '-toggle');
  const toggle = document.getElementById(toggleId);
  if (body.style.display === 'none') {
    body.style.display = '';
    if (toggle) toggle.textContent = '▼';
  } else {
    body.style.display = 'none';
    if (toggle) toggle.textContent = '▶';
  }
}

refreshAll();
loadMetrics();
loadTrash();
setInterval(refreshAll, 30000);
setInterval(loadMetrics, 30000);
</script>
</body>
</html>
"""


def _render_dashboard_html(lang: str = "en") -> str:
    """Render DASHBOARD_HTML with translated strings for the given language.

    Uses string replacement (not str.format) to avoid conflicts with CSS/JS
    curly braces inside the template.
    """
    import json as _json

    # JS I18N object injected into the page for client-side dynamic strings
    i18n_data = {
        "healthy": t("dash_healthy", lang),
        "error": t("dash_error", lang),
        "all_projects": t("dash_all_projects", lang),
        "no_projects": t("dash_no_projects", lang),
        "no_results": t("dash_no_results", lang),
        "searching": t("dash_searching", lang),
        "loading": t("dash_loading", lang),
        "rename": t("dash_rename", lang),
        "delete_btn": t("dash_delete", lang),
        "last_updated": t("dash_last_updated", lang),
        "search_failed": t("dash_search_failed", lang),
        "load_failed": t("dash_load_failed", lang),
    }
    i18n_js_block = "const I18N = " + _json.dumps(i18n_data, ensure_ascii=False) + ";"

    replacements = {
        "{i18n_title}": t("dash_title", lang),
        "{i18n_connecting}": t("dash_connecting", lang),
        "{i18n_loading}": t("dash_loading", lang),
        "{i18n_search_placeholder}": t("dash_search_placeholder", lang),
        "{i18n_all_projects}": t("dash_all_projects", lang),
        "{i18n_search_btn}": t("dash_search_btn", lang),
        "{i18n_auto_refresh}": t("dash_auto_refresh", lang),
        "{i18n_js_block}": i18n_js_block,
    }
    html = DASHBOARD_HTML
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    return html


def _js_str(s: str) -> str:
    """Escape a Python string for safe embedding as a JS string literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


# ── Login/Signup/Account Page HTML removed (single-user mode) ────
# These functions were removed because this is a selfhost single-user build.

def _render_login_html(lang: str = "en") -> str:
    """Login page (single-user mode — not used)."""
    return "<html><body><h1>Single-user mode</h1><p>No login required. <a href='/dashboard'>Go to dashboard</a></p></body></html>"


def _render_signup_html(lang: str = "en") -> str:
    """Signup page (single-user mode — not used)."""
    return "<html><body><h1>Single-user mode</h1><p>No signup required. <a href='/dashboard'>Go to dashboard</a></p></body></html>"


def _render_account_html(lang: str = "en") -> str:
    """Account page (single-user mode — not used)."""
    return "<html><body><h1>Single-user mode</h1><p>No account management. <a href='/dashboard'>Go to dashboard</a></p></body></html>"


# ── Legal Document HTML Constants ────────────────────────────────

_LEGAL_PAGE_STYLE = """\
:root {
  --bg: #0f1117; --surface: #1a1d29; --surface2: #242736;
  --text: #e2e8f0; --text2: #94a3b8; --accent: #6366f1;
  --border: #2d3148;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; min-height: 100vh; display: flex; justify-content: center; padding: 40px 20px; }
.container { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 48px; width: 100%; max-width: 720px; }
h1 { font-size: 1.6rem; margin-bottom: 8px; background: linear-gradient(135deg, var(--accent), #22d3ee); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.subtitle { color: var(--text2); margin-bottom: 32px; font-size: 0.85rem; }
h2 { font-size: 1.1rem; margin: 28px 0 12px; color: var(--text); }
h3 { font-size: 1rem; margin: 20px 0 8px; color: var(--text); }
p, li { font-size: 0.9rem; line-height: 1.7; color: var(--text2); margin-bottom: 8px; }
ul, ol { padding-left: 20px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 0.85rem; }
th, td { padding: 10px 12px; border: 1px solid var(--border); text-align: left; color: var(--text2); }
th { background: var(--surface2); color: var(--text); font-weight: 600; }
.back-link { display: inline-block; margin-top: 32px; color: var(--accent); text-decoration: none; font-size: 0.85rem; }
.back-link:hover { text-decoration: underline; }
"""

PRIVACY_POLICY_HTML = f"""\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>개인정보처리방침 — Kandela</title>
<style>{_LEGAL_PAGE_STYLE}</style>
</head>
<body>
<div class="container">
  <h1>개인정보처리방침</h1>
  <p class="subtitle">시행일: 2026년 3월 15일 | 버전 1.0</p>

  <p>딥온 주식회사(이하 "회사")는 개인정보보호법 제30조에 따라 정보주체의 개인정보를 보호하고 이와 관련한 고충을 신속하고 원활하게 처리할 수 있도록 하기 위하여 다음과 같이 개인정보 처리방침을 수립하여 공개합니다.</p>

  <h2>제1조 (개인정보의 수집 항목 및 수집 방법)</h2>
  <h3>1. 수집하는 개인정보 항목</h3>
  <p>회사는 서비스 제공을 위해 다음과 같은 개인정보를 수집합니다.</p>
  <table>
    <tr><th>구분</th><th>수집 항목</th><th>필수/선택</th></tr>
    <tr><td>회원가입</td><td>이메일, 비밀번호(해시 처리하여 저장), 표시 이름(display name)</td><td>필수</td></tr>
    <tr><td>서비스 이용</td><td>API 키(해시 처리하여 저장), 메모리 콘텐츠(사용자가 저장한 텍스트), 프로젝트명</td><td>필수</td></tr>
    <tr><td>자동 수집</td><td>접속 IP 주소, 접속 일시(타임스탬프), 브라우저 정보(User-Agent), 일일 요청 횟수</td><td>자동</td></tr>
    <tr><td>텔레그램 연동</td><td>텔레그램 사용자 ID</td><td>선택</td></tr>
    <tr><td>대기자 등록</td><td>이메일, 이름, 가입 사유</td><td>필수</td></tr>
  </table>
  <h3>2. 수집 방법</h3>
  <ul>
    <li>회원가입 시 이용자가 직접 입력</li>
    <li>서비스 이용 과정에서 자동으로 수집 (접속 로그, 이용 기록 등)</li>
    <li>MCP 클라이언트(Claude Code, Cursor 등)를 통한 메모리 저장 요청</li>
    <li>텔레그램 봇을 통한 메모리 저장 요청</li>
  </ul>

  <h2>제2조 (개인정보의 수집 및 이용 목적)</h2>
  <p>회사는 수집한 개인정보를 다음의 목적을 위해 이용합니다.</p>
  <table>
    <tr><th>이용 목적</th><th>해당 항목</th></tr>
    <tr><td><strong>회원 관리</strong></td><td>회원 가입, 본인 확인, 계정 관리, 서비스 부정 이용 방지</td></tr>
    <tr><td><strong>서비스 제공</strong></td><td>메모리 저장/검색/관리 서비스 제공, 프로젝트별 데이터 분리</td></tr>
    <tr><td><strong>서비스 개선</strong></td><td>서비스 이용 통계 분석, 기능 개선</td></tr>
    <tr><td><strong>보안 및 안정성</strong></td><td>부정 접근 탐지, 시스템 오류 진단, 서비스 안정성 확보</td></tr>
    <tr><td><strong>고객 지원</strong></td><td>이용자 문의 대응, 공지사항 전달</td></tr>
  </table>
  <p>회사는 수집한 개인정보를 광고, AI 모델 학습, 제3자 마케팅 등의 목적으로 이용하지 않습니다.</p>

  <h2>제3조 (개인정보의 보유 및 이용 기간)</h2>
  <p>회사는 개인정보 수집 및 이용 목적이 달성된 후에는 해당 정보를 지체 없이 파기합니다. 구체적인 보유 기간은 다음과 같습니다.</p>
  <table>
    <tr><th>구분</th><th>보유 기간</th></tr>
    <tr><td>회원 정보 (이메일, 표시 이름 등)</td><td>회원 탈퇴 시까지</td></tr>
    <tr><td>메모리 콘텐츠</td><td>회원 탈퇴 시 또는 이용자가 삭제 요청 시까지</td></tr>
    <tr><td>접속 로그 (IP, 타임스탬프)</td><td>수집일로부터 90일</td></tr>
    <tr><td>일일 이용 통계</td><td>수집일로부터 90일</td></tr>
    <tr><td>회원 탈퇴 후 백업 데이터</td><td>탈퇴일로부터 30일 후 완전 파기</td></tr>
    <tr><td>동의 기록</td><td>관계 법령에 따라 5년</td></tr>
  </table>
  <p>다만, 관계 법령의 규정에 의하여 보존할 필요가 있는 경우 해당 법령에서 정한 기간 동안 보존합니다.</p>
  <table>
    <tr><th>관계 법령</th><th>보존 항목</th><th>보존 기간</th></tr>
    <tr><td>전자상거래법</td><td>계약 또는 청약철회에 관한 기록</td><td>5년</td></tr>
    <tr><td>통신비밀보호법</td><td>접속 로그 기록</td><td>3개월</td></tr>
  </table>

  <h2>제4조 (개인정보의 제3자 제공)</h2>
  <p>회사는 이용자의 개인정보를 제2조에서 고지한 범위 내에서 이용하며, 이용자의 사전 동의 없이 동 범위를 초과하여 이용하거나 원칙적으로 제3자에게 제공하지 않습니다.</p>
  <p>다만, 다음의 경우에는 예외로 합니다.</p>
  <ol>
    <li>이용자가 사전에 동의한 경우</li>
    <li>법령의 규정에 의거하거나, 수사 목적으로 법령에 정해진 절차와 방법에 따라 수사기관의 요구가 있는 경우</li>
    <li>통계 작성, 학술 연구 등의 목적을 위하여 필요한 경우로서 특정 개인을 알아볼 수 없는 형태로 제공하는 경우</li>
  </ol>

  <h2>제5조 (개인정보 처리의 위탁)</h2>
  <p>회사는 서비스 제공을 위해 다음과 같이 개인정보 처리 업무를 위탁하고 있습니다.</p>
  <table>
    <tr><th>수탁업체</th><th>위탁 업무</th><th>보유 및 이용 기간</th></tr>
    <tr><td>Oracle Corporation (Oracle Cloud Infrastructure)</td><td>클라우드 서버 호스팅, 데이터 저장 및 처리</td><td>위탁 계약 종료 시까지</td></tr>
  </table>
  <p>회사는 위탁 계약 시 개인정보보호법 제26조에 따라 위탁 업무 수행 목적 외 개인정보 처리 금지, 기술적/관리적 보호 조치, 재위탁 제한, 수탁자에 대한 관리/감독, 손해배상 등 책임에 관한 사항을 계약서 등 문서에 명시하고, 수탁자가 개인정보를 안전하게 처리하는지 감독하고 있습니다.</p>
  <p>위탁 업무의 내용이나 수탁자가 변경될 경우에는 본 개인정보처리방침을 통해 공개하겠습니다.</p>

  <h2>제6조 (정보주체의 권리 및 행사 방법)</h2>
  <p>이용자(정보주체)는 개인정보보호법 제35조부터 제37조에 따라 다음과 같은 권리를 행사할 수 있습니다.</p>
  <h3>1. 권리 내용</h3>
  <table>
    <tr><th>권리</th><th>설명</th></tr>
    <tr><td><strong>열람 요구</strong></td><td>회사가 보유하고 있는 본인의 개인정보에 대해 열람을 요구할 수 있습니다.</td></tr>
    <tr><td><strong>정정/삭제 요구</strong></td><td>개인정보에 오류가 있는 경우 정정 또는 삭제를 요구할 수 있습니다.</td></tr>
    <tr><td><strong>처리 정지 요구</strong></td><td>개인정보의 처리 정지를 요구할 수 있습니다.</td></tr>
    <tr><td><strong>데이터 이동</strong></td><td>본인의 개인정보를 기계 판독 가능한 형태(JSON)로 내보내기를 요구할 수 있습니다.</td></tr>
  </table>
  <h3>2. 행사 방법</h3>
  <ul>
    <li><strong>웹 대시보드:</strong> 서비스 대시보드의 Account 페이지에서 데이터 내보내기(Export), 계정 삭제(Delete Account) 기능을 통해 직접 행사할 수 있습니다.</li>
    <li><strong>MCP 도구:</strong> memory_delete, memory_update 도구를 통해 개별 메모리의 삭제 및 수정이 가능합니다.</li>
    <li><strong>이메일 요청:</strong> 아래 개인정보 보호책임자에게 이메일로 요청하실 수 있습니다.</li>
  </ul>
  <p>회사는 이용자의 권리 행사 요청을 접수한 날로부터 10일 이내에 조치하고 그 결과를 통지합니다.</p>
  <h3>3. 대리인을 통한 행사</h3>
  <p>이용자는 개인정보보호법 시행규칙 별지 제11호 서식에 따른 위임장을 제출하여 대리인을 통해 권리를 행사할 수 있습니다.</p>

  <h2>제7조 (개인정보의 파기 절차 및 방법)</h2>
  <h3>1. 파기 절차</h3>
  <p>회사는 개인정보의 수집/이용 목적이 달성되거나 보유 기간이 경과한 경우, 해당 개인정보를 지체 없이 파기합니다.</p>
  <ul>
    <li>회원 탈퇴 시: 계정 정보, 메모리 콘텐츠, API 키 등 모든 데이터를 즉시 삭제 처리하며, 백업 데이터는 30일 이내 완전 파기합니다.</li>
    <li>보유 기간 경과 시: 자동화된 정책에 의해 접속 로그 등을 주기적으로 삭제합니다.</li>
  </ul>
  <h3>2. 파기 방법</h3>
  <table>
    <tr><th>저장 형태</th><th>파기 방법</th></tr>
    <tr><td>전자적 파일</td><td>복구 불가능한 방법으로 영구 삭제 (데이터베이스 레코드 삭제, ChromaDB 컬렉션 삭제)</td></tr>
    <tr><td>비밀번호/API 키</td><td>해시 값만 저장되어 있으며, 원본은 보관하지 않음. 해시 데이터를 삭제</td></tr>
    <tr><td>종이 문서</td><td>해당 없음 (종이 문서로 개인정보를 보관하지 않음)</td></tr>
  </table>

  <h2>제8조 (개인정보 보호책임자)</h2>
  <p>회사는 개인정보 처리에 관한 업무를 총괄하고, 개인정보 처리와 관련한 정보주체의 불만 처리 및 피해 구제 등을 위하여 아래와 같이 개인정보 보호책임자를 지정하고 있습니다.</p>
  <table>
    <tr><th>구분</th><th>내용</th></tr>
    <tr><td>성명</td><td>김동규</td></tr>
    <tr><td>직위</td><td>대표이사</td></tr>
    <tr><td>이메일</td><td><a href="mailto:privacy@kandela.ai" style="color:var(--accent)">privacy@kandela.ai</a></td></tr>
  </table>
  <p>이용자는 서비스를 이용하면서 발생하는 모든 개인정보 보호 관련 문의, 불만 처리, 피해 구제 등에 관한 사항을 개인정보 보호책임자에게 문의하실 수 있습니다. 회사는 이용자의 문의에 대해 지체 없이 답변 및 처리해드리겠습니다.</p>
  <h3>개인정보 침해 관련 신고/상담 기관</h3>
  <table>
    <tr><th>기관</th><th>연락처</th><th>홈페이지</th></tr>
    <tr><td>개인정보 침해신고센터 (한국인터넷진흥원)</td><td>(국번없이) 118</td><td><a href="https://privacy.kisa.or.kr" style="color:var(--accent)">privacy.kisa.or.kr</a></td></tr>
    <tr><td>개인정보 분쟁조정위원회</td><td>(국번없이) 1833-6972</td><td><a href="https://www.kopico.go.kr" style="color:var(--accent)">kopico.go.kr</a></td></tr>
    <tr><td>대검찰청 사이버수사과</td><td>(국번없이) 1301</td><td><a href="https://www.spo.go.kr" style="color:var(--accent)">spo.go.kr</a></td></tr>
    <tr><td>경찰청 사이버수사국</td><td>(국번없이) 182</td><td><a href="https://ecrm.police.go.kr" style="color:var(--accent)">ecrm.police.go.kr</a></td></tr>
  </table>

  <h2>제9조 (개인정보의 안전성 확보 조치)</h2>
  <p>회사는 개인정보보호법 제29조에 따라 다음과 같은 안전성 확보 조치를 취하고 있습니다.</p>
  <h3>1. 관리적 조치</h3>
  <ul>
    <li>개인정보 보호책임자 지정 및 운영</li>
    <li>개인정보 처리 직원 최소화</li>
    <li>정기적인 자체 점검 실시</li>
  </ul>
  <h3>2. 기술적 조치</h3>
  <table>
    <tr><th>조치 항목</th><th>상세 내용</th></tr>
    <tr><td><strong>비밀번호 암호화</strong></td><td>PBKDF2 해시 함수(600,000회 반복) 적용, 원문 미보관</td></tr>
    <tr><td><strong>API 키 암호화</strong></td><td>SHA-256 해시 처리, 원문 미보관</td></tr>
    <tr><td><strong>전송 구간 암호화</strong></td><td>HTTPS(TLS) 적용 (계획)</td></tr>
    <tr><td><strong>접근 통제</strong></td><td>API 키 기반 인증, 사용자별 데이터 격리(MemoryStore 분리)</td></tr>
    <tr><td><strong>입력값 검증</strong></td><td>Pydantic v2 기반 입력 데이터 검증, SQL 파라미터화 쿼리</td></tr>
    <tr><td><strong>세션 관리</strong></td><td>세션 만료 시간 설정, 만료 세션 자동 정리</td></tr>
  </table>
  <h3>3. 물리적 조치</h3>
  <ul>
    <li>클라우드 인프라(Oracle Cloud) 제공 업체의 물리적 보안 정책에 준함</li>
    <li>데이터 저장 서버에 대한 접근 권한 최소화</li>
  </ul>

  <h2>제10조 (개인정보 자동 수집 장치의 설치/운영 및 거부에 관한 사항)</h2>
  <h3>1. 쿠키의 사용</h3>
  <p>회사는 웹 대시보드 이용 시 이용자의 로그인 세션을 유지하기 위해 쿠키(Cookie)를 사용합니다.</p>
  <table>
    <tr><th>쿠키 종류</th><th>목적</th><th>보유 기간</th></tr>
    <tr><td>세션 쿠키 (session)</td><td>대시보드 로그인 상태 유지</td><td>브라우저 종료 시 또는 세션 만료 시 삭제</td></tr>
  </table>
  <h3>2. 쿠키의 설치/운영 및 거부</h3>
  <p>이용자는 웹 브라우저의 설정을 통해 쿠키의 설치를 거부할 수 있습니다.</p>
  <ul>
    <li><strong>Chrome:</strong> 설정 &gt; 개인정보 및 보안 &gt; 쿠키 및 기타 사이트 데이터</li>
    <li><strong>Firefox:</strong> 설정 &gt; 개인 정보 및 보안 &gt; 쿠키와 사이트 데이터</li>
    <li><strong>Safari:</strong> 환경설정 &gt; 개인 정보 보호</li>
  </ul>
  <p>다만, 쿠키 설치를 거부할 경우 웹 대시보드 로그인이 유지되지 않아 일부 서비스 이용에 제한이 있을 수 있습니다. MCP 클라이언트(Claude Code, Cursor 등)를 통한 서비스 이용에는 영향이 없습니다.</p>

  <h2>제11조 (개인정보 침해사고 통지)</h2>
  <p>회사는 개인정보보호법 제34조에 따라, 개인정보 유출 사고 발생 시 다음과 같이 정보주체에게 통지합니다.</p>
  <h3>1. 통지 시한</h3>
  <p>개인정보 유출을 인지한 때로부터 <strong>72시간 이내</strong>에 정보주체에게 통지합니다.</p>
  <h3>2. 통지 내용</h3>
  <ol>
    <li>유출된 개인정보 항목</li>
    <li>유출 시점 및 경위</li>
    <li>이용자의 피해 최소화를 위한 조치 방법</li>
    <li>회사의 대응 조치 및 피해 구제 절차</li>
    <li>이용자가 상담할 수 있는 담당부서 및 연락처</li>
  </ol>
  <h3>3. 통지 방법</h3>
  <ul>
    <li>이메일(회원 가입 시 제공한 이메일)</li>
    <li>웹 대시보드 공지</li>
    <li>텔레그램 봇(연동된 경우)</li>
  </ul>
  <h3>4. 관계기관 신고</h3>
  <p>1,000명 이상의 개인정보가 유출된 경우 한국인터넷진흥원(KISA) 및 개인정보보호위원회에 신고합니다.</p>
  <h3>5. 상세 절차</h3>
  <p>침해사고 대응의 상세 절차는 침해사고 대응 절차서(DATA_BREACH_PROCEDURE)에 따릅니다.</p>

  <h2>제12조 (개인정보처리방침의 변경)</h2>
  <p>이 개인정보처리방침은 시행일로부터 적용되며, 법령 및 방침에 따른 변경 내용의 추가, 삭제 및 정정이 있는 경우에는 변경 사항의 시행 7일 전부터 웹 대시보드 공지사항 및 이메일을 통하여 고지할 것입니다.</p>
  <p>중요한 변경 사항(수집 항목 추가, 이용 목적 변경, 제3자 제공 등)이 있는 경우에는 시행 30일 전에 고지하며, 필요 시 이용자의 재동의를 받겠습니다.</p>

  <h3>부칙</h3>
  <ul>
    <li><strong>공고일:</strong> 2026년 3월 15일</li>
    <li><strong>시행일:</strong> 2026년 3월 15일</li>
    <li><strong>버전:</strong> 1.0</li>
  </ul>

  <a href="/dashboard" class="back-link">&larr; 대시보드로 돌아가기</a>
</div>
</body>
</html>
"""

TERMS_HTML = f"""\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>이용약관 — Kandela</title>
<style>{_LEGAL_PAGE_STYLE}</style>
</head>
<body>
<div class="container">
  <h1>이용약관</h1>
  <p class="subtitle">시행일: 2026년 3월 15일 | 버전 1.0</p>

  <h2>제1조 (목적)</h2>
  <p>이 약관은 딥온 주식회사(이하 "회사")가 제공하는 Kandela 서비스(이하 "서비스")의 이용 조건 및 절차, 회사와 이용자 간의 권리, 의무 및 책임사항, 기타 필요한 사항을 규정함을 목적으로 합니다.</p>

  <h2>제2조 (용어의 정의)</h2>
  <p>이 약관에서 사용하는 주요 용어의 정의는 다음과 같습니다.</p>
  <table>
    <tr><th>용어</th><th>정의</th></tr>
    <tr><td><strong>서비스</strong></td><td>회사가 제공하는 Kandela, 이에 부수하는 웹 대시보드, API, 텔레그램 봇 등 일체의 서비스</td></tr>
    <tr><td><strong>이용자</strong></td><td>이 약관에 따라 서비스에 가입하여 회사가 제공하는 서비스를 이용하는 자</td></tr>
    <tr><td><strong>메모리</strong></td><td>이용자가 서비스를 통해 저장하는 텍스트 콘텐츠(결정사항, 코드 스니펫, 요약, 사실 정보 등)</td></tr>
    <tr><td><strong>프로젝트</strong></td><td>이용자가 메모리를 분류하기 위해 생성하는 논리적 단위</td></tr>
    <tr><td><strong>API 키</strong></td><td>서비스 인증을 위해 발급되는 고유 식별 문자열</td></tr>
    <tr><td><strong>MCP</strong></td><td>Model Context Protocol. AI 코딩 도구와 서비스 간의 통신 프로토콜</td></tr>
    <tr><td><strong>대시보드</strong></td><td>서비스의 웹 기반 관리 인터페이스</td></tr>
  </table>
  <p>이 약관에서 정의하지 않은 용어는 관계 법령 및 일반적인 상관례에 따릅니다.</p>

  <h2>제3조 (약관의 효력 및 변경)</h2>
  <ol>
    <li>이 약관은 서비스 화면에 게시하거나 기타의 방법으로 이용자에게 공지함으로써 효력이 발생합니다.</li>
    <li>회사는 합리적인 사유가 발생할 경우 관계 법령에 위배되지 않는 범위에서 이 약관을 변경할 수 있습니다.</li>
    <li>약관이 변경되는 경우 회사는 변경 사항을 시행일 7일 전부터 서비스 대시보드 또는 이메일을 통해 공지합니다. 다만, 이용자에게 불리하거나 중대한 변경의 경우 30일 전에 공지합니다.</li>
    <li>변경된 약관에 동의하지 않는 이용자는 서비스 이용을 중단하고 탈퇴할 수 있습니다. 약관 변경 공지 후 변경 시행일까지 거부 의사를 표시하지 않은 경우 변경된 약관에 동의한 것으로 간주합니다.</li>
  </ol>

  <h2>제4조 (서비스의 내용)</h2>
  <p>1. 회사가 제공하는 서비스는 다음과 같습니다.</p>
  <table>
    <tr><th>서비스</th><th>설명</th></tr>
    <tr><td>메모리 저장/검색/관리</td><td>MCP 프로토콜을 통한 텍스트 메모리의 저장, 시맨틱 검색, 수정, 삭제</td></tr>
    <tr><td>웹 대시보드</td><td>메모리 조회, 검색, 통계, API 키 관리, 계정 관리</td></tr>
    <tr><td>자동 기억 관리</td><td>세션 시작/종료 시 자동 회상 및 요약, 로컬 캐시 동기화</td></tr>
    <tr><td>텔레그램 봇</td><td>텔레그램을 통한 메모리 저장/검색 (제공 여부는 이용 등급에 따름)</td></tr>
  </table>
  <p>2. 서비스는 AI 코딩 도구(Claude Code, Cursor 등)의 보조 도구로서 메모리 저장 및 검색 기능을 제공하며, AI 시스템 자체가 아닙니다.</p>
  <p>3. 회사는 Anthropic, OpenAI 등 AI 서비스 제공 업체와 제휴 또는 종속 관계가 없는 독립적인 서비스입니다.</p>

  <h2>제5조 (회원가입 및 탈퇴)</h2>
  <h3>1. 회원가입</h3>
  <ol>
    <li>이용자는 회사가 정한 가입 양식에 따라 필요한 정보를 입력하고, 이 약관과 개인정보처리방침에 동의함으로써 회원가입을 신청합니다.</li>
    <li>베타 서비스 기간 중 회원가입은 초대 코드(Invite Code)를 보유한 이용자에 한하여 허용됩니다.</li>
    <li>회사는 다음 각 호에 해당하는 신청에 대하여 가입을 거절하거나 사후에 이용 계약을 해지할 수 있습니다.
      <ul>
        <li>타인의 정보를 도용한 경우</li>
        <li>가입 신청서의 내용을 허위로 기재한 경우</li>
        <li>이 약관에 따라 이전에 자격이 상실된 적이 있는 경우</li>
        <li>기타 가입을 승낙하는 것이 회사의 기술상 현저히 지장이 있는 경우</li>
      </ul>
    </li>
    <li>이용자는 만 14세 이상이어야 합니다.</li>
  </ol>
  <h3>2. 회원 탈퇴</h3>
  <ol>
    <li>이용자는 언제든지 대시보드의 계정 삭제(Delete Account) 기능 또는 이메일 요청을 통하여 탈퇴를 신청할 수 있습니다.</li>
    <li>탈퇴 시 이용자의 계정 정보, 저장된 메모리, API 키 등 모든 데이터가 삭제됩니다.</li>
    <li>탈퇴 처리 후 데이터 복구는 불가능합니다. 필요한 데이터는 탈퇴 전에 데이터 내보내기(Export) 기능을 이용하여 백업하시기 바랍니다.</li>
    <li>관계 법령에 따라 보존이 필요한 정보는 해당 법령에서 정한 기간 동안 보관됩니다.</li>
  </ol>

  <h2>제6조 (서비스 이용료)</h2>
  <ol>
    <li><strong>베타 서비스 기간:</strong> 서비스의 기본 기능은 무료로 제공됩니다.</li>
    <li><strong>유료 전환 시:</strong> 회사가 유료 서비스(Pro 등급 등)를 도입하는 경우, 도입 30일 전에 이용자에게 서비스 내용, 이용료, 결제 방법 등을 사전 고지합니다.</li>
    <li>유료 전환 시 기존 무료 이용자에게는 합리적인 전환 기간을 제공하며, 유료 전환에 동의하지 않는 이용자는 서비스를 계속 이용하거나 탈퇴할 수 있습니다.</li>
    <li>유료 서비스 도입 시 별도의 이용 약관 및 환불 정책을 수립하여 공지합니다.</li>
  </ol>

  <h2>제7조 (서비스의 변경 및 중단)</h2>
  <ol>
    <li>회사는 서비스의 내용, 운영 방식 등을 변경할 수 있으며, 변경 사항은 서비스 대시보드 또는 이메일을 통해 사전에 공지합니다.</li>
    <li>회사는 다음 각 호에 해당하는 경우 서비스의 전부 또는 일부를 제한하거나 중단할 수 있습니다.
      <ul>
        <li>서비스용 설비의 보수, 점검, 교체, 고장, 통신 두절 등의 사유가 발생한 경우</li>
        <li>천재지변, 국가비상사태, 전력 공급 중단 등 불가항력의 사유가 발생한 경우</li>
        <li>서비스 이용의 폭주 등으로 정상적인 서비스 이용에 지장이 있는 경우</li>
      </ul>
    </li>
    <li>서비스를 종료하는 경우 회사는 종료일로부터 최소 30일 전에 이용자에게 통지하며, 이용자가 데이터를 내보낼 수 있는 충분한 기간을 제공합니다.</li>
    <li>베타 서비스 기간 중에는 사전 공지 없이 서비스가 일시적으로 중단될 수 있으며, 이에 따른 손해에 대해 회사는 책임을 부담하지 않습니다.</li>
  </ol>

  <h2>제8조 (이용자의 의무 및 금지 행위)</h2>
  <p>1. 이용자는 다음 행위를 하여서는 안 됩니다.</p>
  <table>
    <tr><th>금지 행위</th><th>설명</th></tr>
    <tr><td><strong>불법 콘텐츠 저장</strong></td><td>법령에 위반되는 내용, 불법 정보, 음란물 등의 저장</td></tr>
    <tr><td><strong>타인 개인정보 저장</strong></td><td>제3자의 개인정보(주민등록번호, 카드번호, 비밀번호 등)를 이용자의 메모리에 저장하는 행위</td></tr>
    <tr><td><strong>시스템 공격</strong></td><td>서비스의 안정적 운영을 방해하는 행위 (DDoS, 해킹, 무단 접근 시도 등)</td></tr>
    <tr><td><strong>API 남용</strong></td><td>비정상적으로 대량의 요청을 발생시키는 행위, API 키의 무단 공유</td></tr>
    <tr><td><strong>타인 계정 도용</strong></td><td>타인의 계정, API 키를 무단으로 사용하는 행위</td></tr>
    <tr><td><strong>서비스 역공학</strong></td><td>서비스를 리버스 엔지니어링, 디컴파일, 분해하는 행위</td></tr>
    <tr><td><strong>재판매</strong></td><td>회사의 사전 서면 동의 없이 서비스를 재판매하거나 재배포하는 행위</td></tr>
  </table>
  <p>2. 이용자는 관계 법령, 이 약관의 규정, 이용 안내 및 서비스와 관련하여 공지한 주의사항을 준수하여야 합니다.</p>
  <p>3. 회사는 이용자가 본 조의 의무를 위반한 경우 서비스 이용을 제한하거나 계약을 해지할 수 있습니다.</p>

  <h2>제9조 (콘텐츠 소유권)</h2>
  <ol>
    <li><strong>이용자 콘텐츠의 소유권:</strong> 이용자가 서비스를 통해 저장한 메모리 콘텐츠의 소유권은 해당 이용자에게 있습니다.</li>
    <li><strong>서비스 운영을 위한 이용 허락:</strong> 이용자는 서비스 제공에 필요한 범위 내에서(저장, 인덱싱, 검색, 표시 등) 회사가 해당 콘텐츠를 이용하는 것을 허락합니다.</li>
    <li><strong>콘텐츠 활용 제한:</strong> 회사는 이용자의 메모리 콘텐츠를 다음의 목적으로 사용하지 않습니다.
      <ul>
        <li>AI 모델 학습 또는 훈련</li>
        <li>광고 또는 마케팅</li>
        <li>서비스 제공 목적 외의 이용</li>
        <li>제3자에 대한 공개 또는 판매</li>
      </ul>
    </li>
    <li><strong>데이터 이동권:</strong> 이용자는 언제든지 대시보드의 데이터 내보내기(Export) 기능을 통해 자신의 콘텐츠를 JSON 형식으로 내보낼 수 있습니다.</li>
  </ol>

  <h2>제10조 (면책조항)</h2>
  <ol>
    <li><strong>베타 서비스:</strong> 본 서비스는 베타(Beta) 서비스로 제공되며, "있는 그대로(AS-IS)" 제공됩니다. 회사는 서비스의 완전성, 정확성, 신뢰성, 이용 가능성에 대해 명시적 또는 묵시적 보증을 하지 않습니다.</li>
    <li>회사는 다음 각 호의 사유로 인해 발생한 손해에 대해 책임을 부담하지 않습니다.
      <ul>
        <li>천재지변, 전쟁, 테러, 폭동, 정부 조치, 통신 장애 등 불가항력에 의한 경우</li>
        <li>이용자의 귀책 사유로 인한 서비스 이용의 장애</li>
        <li>서비스의 변경, 중단, 종료로 인한 손해</li>
        <li>이용자가 저장한 메모리 콘텐츠의 정확성, 적법성에 관한 사항</li>
        <li>이용자 간 또는 이용자와 제3자 간의 분쟁</li>
      </ul>
    </li>
    <li>회사는 이용자가 서비스를 통해 얻은 정보를 바탕으로 한 판단이나 행위에 대해 책임을 부담하지 않습니다.</li>
    <li>베타 서비스 기간 중 서비스 장애, 데이터 손실, 서비스 변경/종료 등이 발생할 수 있으며, 이에 대해 회사는 사전 예고 의무를 제외하고 별도의 보상을 하지 않습니다.</li>
  </ol>

  <h2>제11조 (데이터 백업 책임)</h2>
  <ol>
    <li>이용자가 서비스를 통해 저장한 메모리 콘텐츠에 대한 백업은 이용자 본인의 책임입니다.</li>
    <li>회사는 서비스의 안정적 운영을 위해 합리적인 수준의 데이터 보호 조치를 취하고 있으나, 다음의 경우에 발생하는 데이터 손실에 대해서는 책임을 부담하지 않습니다.
      <ul>
        <li>시스템 장애, 하드웨어 고장 등 기술적 문제</li>
        <li>불가항력에 의한 데이터 손실</li>
        <li>이용자의 실수 또는 부주의에 의한 데이터 삭제</li>
      </ul>
    </li>
    <li>이용자는 대시보드의 데이터 내보내기(Export) 기능을 이용하여 정기적으로 데이터를 백업할 것을 권장합니다.</li>
  </ol>

  <h2>제12조 (손해배상)</h2>
  <ol>
    <li>회사의 고의 또는 중대한 과실로 인하여 이용자에게 손해가 발생한 경우, 회사는 관계 법령에 따라 손해를 배상합니다.</li>
    <li>베타 서비스 기간 중 회사의 손해배상 책임은 해당 이용자가 서비스에 대해 지불한 금액을 한도로 합니다. 무료 베타 서비스의 경우 손해배상의 범위는 관련 법령이 허용하는 범위 내에서 최소한도로 제한됩니다.</li>
    <li>이용자가 이 약관을 위반하여 회사에 손해가 발생한 경우, 이용자는 그 손해를 배상하여야 합니다.</li>
  </ol>

  <h2>제13조 (분쟁 해결)</h2>
  <ol>
    <li>이 약관과 관련하여 회사와 이용자 간에 발생한 분쟁에 대해서는 대한민국 법률을 준거법으로 합니다.</li>
    <li>서비스 이용과 관련하여 회사와 이용자 간에 분쟁이 발생한 경우, 양 당사자는 분쟁 해결을 위해 성실히 협의합니다.</li>
    <li>협의로 해결되지 않는 경우, <strong>서울중앙지방법원</strong>을 제1심 관할 법원으로 합니다.</li>
    <li>이용자는 개인정보와 관련한 분쟁의 경우 개인정보 분쟁조정위원회(<a href="https://www.kopico.go.kr" style="color:var(--accent)">kopico.go.kr</a>)에 조정을 신청할 수 있습니다.</li>
  </ol>

  <h2>제14조 (기타)</h2>
  <ol>
    <li>이 약관에서 정하지 아니한 사항과 이 약관의 해석에 관하여는 관계 법령 및 상관례에 따릅니다.</li>
    <li>이 약관의 일부 조항이 무효가 되더라도 나머지 조항의 효력에는 영향을 미치지 않습니다.</li>
  </ol>

  <h3>부칙</h3>
  <ol>
    <li>이 약관은 2026년 3월 15일부터 시행합니다.</li>
    <li>이 약관의 시행 이전에 가입한 이용자에 대해서도 이 약관이 적용됩니다.</li>
  </ol>

  <p><strong>딥온 주식회사</strong><br>버전: 1.0 | 시행일: 2026년 3월 15일</p>

  <a href="/dashboard" class="back-link">&larr; 대시보드로 돌아가기</a>
</div>
</body>
</html>
"""

OPERATOR_INFO_HTML = f"""\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>사업자 정보 — Kandela</title>
<style>{_LEGAL_PAGE_STYLE}</style>
</head>
<body>
<div class="container">
  <h1>사업자 정보</h1>
  <p class="subtitle">전자상거래 등에서의 소비자보호에 관한 법률에 의한 사업자 정보 공개</p>

  <table>
    <tr><th>항목</th><th>내용</th></tr>
    <tr><td>상호</td><td>딥온 주식회사 (DeepOn Inc.)</td></tr>
    <tr><td>대표자</td><td>김동규</td></tr>
    <tr><td>사업자등록번호</td><td>[사업장 주소 확인 후 입력]</td></tr>
    <tr><td>소재지</td><td>[사업장 주소 확인 후 입력]</td></tr>
    <tr><td>이메일</td><td><a href="mailto:support@kandela.ai" style="color:var(--accent)">support@kandela.ai</a></td></tr>
    <tr><td>개인정보 보호 책임자</td><td>김동규 (<a href="mailto:privacy@kandela.ai" style="color:var(--accent)">privacy@kandela.ai</a>)</td></tr>
  </table>

  <h2>서비스 정보</h2>
  <table>
    <tr><th>항목</th><th>내용</th></tr>
    <tr><td>서비스명</td><td>Kandela</td></tr>
    <tr><td>서비스 유형</td><td>AI 개발도구 (장기 기억 관리)</td></tr>
    <tr><td>이용 요금</td><td>베타 기간 무료</td></tr>
    <tr><td>서버 위치</td><td>대한민국 (Oracle Cloud 서울 리전)</td></tr>
  </table>

  <a href="/dashboard" class="back-link">&larr; 대시보드로 돌아가기</a>
</div>
</body>
</html>
"""
