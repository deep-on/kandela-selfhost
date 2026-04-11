"""Output formatting for MCP tool responses."""

from __future__ import annotations

import json
from typing import Any


def _importance_badge(meta: dict[str, Any]) -> str:
    """Generate importance/priority badge from metadata."""
    importance = meta.get("importance")
    if isinstance(importance, (int, float)):
        imp = float(importance)
        if imp >= 9.0:
            return " \u26a0\ufe0f CRITICAL"
        elif imp < 3.0:
            return " (low)"
        else:
            return f" [imp:{imp:.1f}]"
    # Fallback for unmigrated data
    priority = meta.get("priority", "normal")
    if priority == "critical":
        return " \u26a0\ufe0f CRITICAL"
    elif priority == "low":
        return " (low)"
    return ""


def format_memory_result(result: dict[str, Any]) -> str:
    """Format a single memory search result as readable text."""
    meta = result.get("metadata", {})
    badge = _importance_badge(meta)

    # Score display: prefer retrieval_score, then distance
    retrieval_score = result.get("retrieval_score")
    distance = result.get("distance", 0)
    if retrieval_score is not None:
        score_str = f"score: {retrieval_score:.3f}"
    elif distance >= 0:
        score_str = f"distance: {distance:.3f}"
    else:
        score_str = ""

    lines = [
        f"**[{meta.get('type', '?')}]**{badge} ({score_str})",
        f"  project: {meta.get('project', '?')}",
        f"  created: {meta.get('created_at', '?')}",
    ]
    tags = meta.get("tags", "[]")
    if isinstance(tags, str):
        tags = json.loads(tags)
    if tags:
        lines.append(f"  tags: {', '.join(tags)}")
    lines.append(f"  id: {result.get('id', '?')}")
    lines.append(f"\n  {result.get('content', '')}")
    return "\n".join(lines)


def format_search_results(results: list[dict[str, Any]]) -> str:
    """Format multiple search results."""
    if not results:
        return "No memories found."
    parts = [format_memory_result(r) for r in results]
    header = f"Found {len(results)} memory(ies):\n"
    return header + "\n\n---\n\n".join(parts)


def format_compact_result(
    result: dict[str, Any],
    max_content_len: int = 80,
    include_content: bool = True,
) -> str:
    """Format a single memory as a compact one-liner (~50 tokens).

    Format: [type] content_snippet... (imp:X.X, YYYY-MM-DD)
    """
    meta = result.get("metadata", {})
    mtype = meta.get("type", "?")

    # Importance display
    imp = meta.get("importance")
    if isinstance(imp, (int, float)):
        imp_str = f"{float(imp):.1f}"
    else:
        # Fallback for unmigrated data
        priority = meta.get("priority", "normal")
        imp_str = {"critical": "9.0", "low": "2.0"}.get(priority, "5.0")

    # Date (YYYY-MM-DD only)
    date_str = str(meta.get("created_at", "?"))[:10]

    if include_content:
        content = result.get("content", "")
        # Flatten to single line, truncate
        snippet = content.replace("\n", " ").strip()
        if len(snippet) > max_content_len:
            snippet = snippet[:max_content_len] + "..."
        return f"[{mtype}] {snippet} ({imp_str}, {date_str})"
    else:
        return f"[{mtype}] ({imp_str}, {date_str})"


_HINT_PREFIX = "\u2026 \u2192 context_search('"
_HINT_SUFFIX = "')"
_HINT_OVERHEAD = len(_HINT_PREFIX) + len(_HINT_SUFFIX)


def _extract_hint_keyword(content: str, max_len: int = 20) -> str:
    """Extract a context_search hint keyword from memory content."""
    first_line = ""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if not first_line:
        return content[:max_len].strip()

    for prefix in ("GOTCHA:", "CRITICAL:", "결정:", "주의:", "##"):
        if first_line.startswith(prefix):
            after = first_line[len(prefix):].strip()
            if after:
                return after[:max_len].strip().rstrip(".,;:\u2026")

    if first_line.startswith(("http://", "https://")):
        for line in content.split("\n")[1:]:
            stripped = line.strip()
            if stripped and not stripped.startswith(("http://", "https://")):
                return stripped[:max_len].strip()

    return first_line[:max_len].strip().rstrip(".,;:\u2026")


def format_brief_recall_item(
    result: dict[str, Any],
    max_content_len: int = 80,
) -> str:
    """Brief Recall only — long memories get summary + search hint."""
    meta = result.get("metadata", {})
    mtype = meta.get("type", "?")

    imp = meta.get("importance")
    if isinstance(imp, (int, float)):
        imp_str = f"{float(imp):.1f}"
    else:
        priority = meta.get("priority", "normal")
        imp_str = {"critical": "9.0", "low": "2.0"}.get(priority, "5.0")

    date_str = str(meta.get("created_at", "?"))[:10]

    content = result.get("content", "")
    snippet = content.replace("\n", " ").strip()

    if len(snippet) <= max_content_len:
        return f"[{mtype}] {snippet} ({imp_str}, {date_str})"

    keyword = _extract_hint_keyword(content)
    hint = f"{_HINT_PREFIX}{keyword}{_HINT_SUFFIX}"
    summary_len = max(max_content_len - len(hint), 20)
    summary = snippet[:summary_len]

    return f"[{mtype}] {summary}{hint} ({imp_str}, {date_str})"


def format_compact_results(results: list[dict[str, Any]], **kwargs: Any) -> str:
    """Format multiple results in compact numbered list (~50 tokens/item)."""
    if not results:
        return "No memories found."
    lines = [f"Found {len(results)} memory(ies):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {format_compact_result(r, **kwargs)}")
    return "\n".join(lines)


def format_project_brief(
    brief: dict[str, Any],
    project: str,
    critical_memories: list[dict[str, Any]] | None = None,
) -> str:
    """Format a project brief for lazy recall output (~100-200 tokens).

    Args:
        brief: Dict from store.get_project_brief()
        project: Project identifier
        critical_memories: Optional list of critical memory results
    """
    mem_count = brief.get("memory_count", 0)
    crit_count = brief.get("critical_count", 0)
    type_counts = brief.get("type_counts", {})
    topics = brief.get("topic_keywords", [])
    last_date = brief.get("last_session_date")
    last_snippet = brief.get("last_summary_snippet")

    # Header line
    lines = [f"# Memory: {project} ({mem_count} memories, {crit_count} critical)"]

    # Type breakdown as inline
    if type_counts:
        parts = [f"{count} {mtype}" for mtype, count in sorted(type_counts.items())]
        lines.append(f"Types: {', '.join(parts)}")

    # Last session
    if last_date and last_snippet:
        lines.append(f"Last: {last_date} — \"{last_snippet}\"")
    elif last_date:
        lines.append(f"Last session: {last_date}")

    # Topics
    if topics:
        lines.append(f"Topics: {', '.join(topics)}")

    # Critical memories section
    if critical_memories:
        lines.append(f"\n## Critical ({len(critical_memories)})")
        for i, r in enumerate(critical_memories, 1):
            lines.append(f"{i}. {format_compact_result(r)}")

    return "\n".join(lines)


def format_stats(stats: dict[str, Any]) -> str:
    """Format stats dict as readable text."""
    if "projects" in stats:
        # Global stats
        lines = [
            f"Total projects: {stats['total_projects']}",
            f"Total memories: {stats['total_memories']}",
            "",
        ]
        for name, pstat in stats["projects"].items():
            lines.append(f"  [{name}] {pstat['total_memories']} memories")
            for mtype, count in pstat.get("by_type", {}).items():
                lines.append(f"    - {mtype}: {count}")
        return "\n".join(lines)
    else:
        # Single project stats
        lines = [
            f"Project: {stats['project']}",
            f"Total memories: {stats['total_memories']}",
        ]
        for mtype, count in stats.get("by_type", {}).items():
            lines.append(f"  - {mtype}: {count}")
        # Token economy
        te = stats.get("token_economy")
        if te and te.get("memory_count", 0) > 0:
            lines.append("")
            lines.append("Token Economy:")
            net = te.get("net_saving_usd", 0)
            lines.append(f"  Net saving: ${net:.3f}")
            lines.append(f"  Overhead: {te.get('overhead_tokens', 0):,} tok")
            lines.append(f"  Benefit: {te.get('estimated_benefit_tokens', 0):,} tok")
            lines.append(f"  Recalls: {te.get('total_recalls', 0)}")
            lines.append(f"  Searches: {te.get('total_searches', 0)}")
        return "\n".join(lines)
