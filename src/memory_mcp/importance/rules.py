"""Server-side importance rule engine.

Rules apply bonuses/penalties to the LLM-provided importance score
based on content patterns and tags. Applied AFTER the LLM score;
result is clamped to [1.0, 10.0].

Rules are additive: all matching rules contribute their bonus.
Easy to extend — just add a new ImportanceRule to IMPORTANCE_RULES.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from memory_mcp.constants import IMPORTANCE_MAX, IMPORTANCE_MIN

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportanceRule:
    """A single importance adjustment rule.

    At least one of pattern or tag_match must be set.

    Attributes:
        name: Human-readable rule identifier for logging.
        bonus: Importance adjustment (positive=increase, negative=decrease).
        pattern: Regex pattern matched against memory content.
        tag_match: Exact tag name to match.
    """

    name: str
    bonus: float
    pattern: re.Pattern[str] | None = None
    tag_match: str | None = None


# ── Rule definitions ─────────────────────────────────────────────
# All matching rules are applied additively.
# Rules can be continuously refined and extended.

IMPORTANCE_RULES: list[ImportanceRule] = [
    # ── Infrastructure / access patterns ──
    ImportanceRule(
        name="ssh_connection",
        bonus=2.0,
        pattern=re.compile(r"ssh\s+\S+@\S+", re.IGNORECASE),
    ),
    ImportanceRule(
        name="ip_port_pattern",
        bonus=1.5,
        pattern=re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}[:/]\d+"),
    ),
    ImportanceRule(
        name="docker_command",
        bonus=1.5,
        pattern=re.compile(r"docker\s+(compose|run|exec|build)", re.IGNORECASE),
    ),
    ImportanceRule(
        name="deploy_path",
        bonus=1.5,
        pattern=re.compile(r"(deploy|배포)\s*(경로|path|서버|server|to\s|:)", re.IGNORECASE),
    ),
    # ── Secrets / credentials ──
    ImportanceRule(
        name="api_key_or_secret",
        bonus=2.0,
        pattern=re.compile(
            r"(api[_-]?key|secret[_-]?key|token|password|credential)\s*[:=]",
            re.IGNORECASE,
        ),
    ),
    # ── Warnings / constraints ──
    ImportanceRule(
        name="gotcha_or_caveat",
        bonus=1.5,
        pattern=re.compile(
            r"(주의|gotcha|caveat|warning|주의사항|함정|트랩)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="not_supported",
        bonus=1.0,
        pattern=re.compile(
            r"(지원하지\s*않|미지원|불가능|not\s+support|unsupported)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="do_not_pattern",
        bonus=1.5,
        pattern=re.compile(
            r"(하지\s*마|금지|절대|never|must\s+not|do\s+not)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="repeated_mistake",
        bonus=2.0,
        pattern=re.compile(
            r"(반복.*실수|같은.*실수|또.*실수|again.*mistake|keep.*forgetting)",
            re.IGNORECASE,
        ),
    ),
    # ── User emphasis / semantic urgency ──
    # Detects natural-language cues like "꼭 기억해", "잊으면 안돼", "remember this"
    ImportanceRule(
        name="user_emphasis_remember",
        bonus=2.5,
        pattern=re.compile(
            r"(꼭\s*기억|반드시\s*기억|잊지\s*마|잊으면\s*안|절대\s*잊|기억해\s*둬|기억해\s*놔)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="user_emphasis_important",
        bonus=2.0,
        pattern=re.compile(
            r"(매우\s*중요|정말\s*중요|아주\s*중요|핵심|필수|critical|very\s+important|super\s+important|extremely\s+important)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="user_emphasis_remember_en",
        bonus=2.5,
        pattern=re.compile(
            r"(must\s+remember|don'?t\s+forget|never\s+forget|always\s+remember|remember\s+this)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="user_emphasis_essential",
        bonus=1.5,
        pattern=re.compile(
            r"(중요\s*:|important\s*:|note\s*:|핵심\s*:|essential|꼭\s*알아|명심)",
            re.IGNORECASE,
        ),
    ),
    # ── Command failure / fix patterns (P5) ──
    ImportanceRule(
        name="command_fix_pattern",
        bonus=1.5,
        pattern=re.compile(
            r"(올바른\s*명령|correct\s*command|교정|수정된\s*명령|대신\s*사용|instead\s+use)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="error_lesson_pattern",
        bonus=1.5,
        pattern=re.compile(
            r"(에러\s*원인|error\s*cause|실패\s*이유|failed\s*because|실패.*해결|원인\s*:|이유\s*:)",
            re.IGNORECASE,
        ),
    ),
    # ── Test / benchmark execution location ──
    # "where to run tests" is frequently forgotten → escalate to CRITICAL
    ImportanceRule(
        name="test_execution_location",
        bonus=2.5,
        pattern=re.compile(
            r"(테스트.*실행.*위치|테스트.*어디서|where.*run.*test|run.*test.*on|"
            r"pytest.*컨테이너|컨테이너.*pytest|dev.*컨테이너.*테스트|테스트.*dev.*컨테이너|"
            r"only.*on.*container|테스트.*명령|test.*command)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="benchmark_location",
        bonus=2.5,
        pattern=re.compile(
            r"(benchmark.*실행|벤치마크.*실행|run.*benchmark|benchmark.*위치|"
            r"benchmark_v4|benchmark.*only|벤치마크.*명령|benchmark.*command)",
            re.IGNORECASE,
        ),
    ),
    # ── Tag-based bonuses ──
    ImportanceRule(
        name="gotcha_tag_bonus",
        bonus=1.0,
        tag_match="gotcha",
    ),
    ImportanceRule(
        name="unfinished_task_bonus",
        bonus=1.5,
        tag_match="unfinished",
    ),
    ImportanceRule(
        name="project_infra_tag_bonus",
        bonus=2.0,
        tag_match="project-infra",
    ),
    # ── File/document location patterns ──
    ImportanceRule(
        name="file_location_pattern",
        bonus=1.5,
        pattern=re.compile(
            r"(위치|경로|폴더|디렉토리|located|path|directory).{0,30}(\.md|\.yaml|\.json|\.toml|\.py)",
            re.IGNORECASE,
        ),
    ),
    # ── Docker --no-deps pattern (log analyzer: most frequent violation) ──
    ImportanceRule(
        name="docker_no_deps_pattern",
        bonus=2.0,
        pattern=re.compile(
            r"(--no-deps|no.deps.*필수|without.*no.deps|no.deps.*없으면)",
            re.IGNORECASE,
        ),
    ),
    # ── Non-codifiable knowledge bonus (MA-bench lesson) ──
    # Infrastructure knowledge that CANNOT be embedded in code:
    # SSH ports, manual procedures, external API constraints, server paths.
    # These are the highest-value memories for Kandela.
    ImportanceRule(
        name="non_codifiable_knowledge",
        bonus=1.5,
        pattern=re.compile(
            r"(SSH.*포트|ssh.*port\s*\d|서버.*접속|server.*access"
            r"|수동.*호출|수동.*실행|manual.*step|manual.*call"
            r"|after.*migration.*must|마이그레이션.*후.*반드시"
            r"|rate.?limit|API.*제한|외부.*서버|external.*server"
            r"|방화벽|firewall|VPN.*필요|vpn.*required"
            r"|flush.*금지|절대.*flush|never.*flush"
            r"|자동화.*안|not.*automated|백로그)",
            re.IGNORECASE,
        ),
    ),
    # ── Auto-saved penalty ──
    ImportanceRule(
        name="auto_saved_penalty",
        bonus=-3.0,
        tag_match="auto-saved",
    ),
    # ── Code-readable content penalty (MF-3) ──
    # Content describing code structure, imports, file layout — penalize
    # because this info can be read directly from the codebase.
    ImportanceRule(
        name="code_structure_description",
        bonus=-2.0,
        pattern=re.compile(
            r"(프로젝트\s*구조|project\s*structure|directory\s*structure|폴더\s*구조|파일\s*구조)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="code_import_description",
        bonus=-1.5,
        pattern=re.compile(
            r"(import\s+\w+\s+from|from\s+\w+\s+import|requires?\s+\w+|의존성\s*:|dependencies\s*:)",
            re.IGNORECASE,
        ),
    ),
    ImportanceRule(
        name="code_function_description",
        bonus=-1.5,
        pattern=re.compile(
            r"(함수\s*(시그니처|signature)|class\s+\w+\s+(has|contains|includes)|메서드\s*(목록|list))",
            re.IGNORECASE,
        ),
    ),
]


# ── Code-readable content detection (MF-3) ───────────────────────

# Patterns that indicate content is describing code-readable information.
# Used for soft warnings at store time, NOT for blocking.
_CODE_READABLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"(프로젝트\s*구조|project\s*structure|directory\s*structure|폴더\s*구조|파일\s*구조)",
        re.IGNORECASE,
    ), "project structure"),
    (re.compile(
        r"(import\s+\w+\s+from|from\s+\w+\s+import|requires?\s+\w+|의존성\s*:|dependencies\s*:)",
        re.IGNORECASE,
    ), "code imports/dependencies"),
    (re.compile(
        r"(함수\s*(시그니처|signature)|class\s+\w+\s+(has|contains|includes)|메서드\s*(목록|list))",
        re.IGNORECASE,
    ), "function/class description"),
    (re.compile(
        r"(config\s*파일|설정\s*파일|\.json\s*파일에|\.yaml\s*파일에|\.toml\s*파일에).{0,30}(내용|contains|값|value)",
        re.IGNORECASE,
    ), "config file content"),
    (re.compile(
        r"(코드\s*패턴|code\s*pattern|구현\s*방식|implementation\s*approach).{0,20}(사용|uses?|적용|applied)",
        re.IGNORECASE,
    ), "code pattern"),
]

# Patterns that EXEMPT content from code-readable warnings
# (these describe WHY, not WHAT)
_CODE_INVISIBLE_EXEMPTIONS: list[re.Pattern[str]] = [
    re.compile(r"(이유|reason|because|왜냐|므로|때문에|why)", re.IGNORECASE),
    re.compile(r"(gotcha|주의|caveat|warning|함정|트랩)", re.IGNORECASE),
    re.compile(r"(삭제|removed|deprecated|제거).{0,30}(이유|because|reason)", re.IGNORECASE),
    re.compile(r"(대신|instead|alternative|vs\s)", re.IGNORECASE),
]


def detect_code_readable(content: str, tags: list[str]) -> str | None:
    """Check if content describes code-readable information.

    Returns a hint string if code-readable patterns are detected
    (and no exemption patterns match), or None if content is fine.

    Args:
        content: Memory content text.
        tags: Memory tags.

    Returns:
        Hint string for the user, or None.
    """
    # Skip if tagged as gotcha/infra — these are always code-invisible
    exempt_tags = {"gotcha", "deploy", "infra", "ssh", "docker", "server", "preference"}
    if set(tags) & exempt_tags:
        return None

    # Check for code-readable patterns
    matched_categories: list[str] = []
    for pattern, category in _CODE_READABLE_PATTERNS:
        if pattern.search(content):
            matched_categories.append(category)

    if not matched_categories:
        return None

    # Check for exemptions (WHY/reason content overrides)
    for exempt_pattern in _CODE_INVISIBLE_EXEMPTIONS:
        if exempt_pattern.search(content):
            return None

    categories_str = ", ".join(matched_categories)
    return (
        f"HINT: This memory describes {categories_str} — "
        f"information that can be read directly from code. "
        f"Consider storing WHY (decision reasons) or gotchas instead. "
        f"Memory is most valuable for code-invisible knowledge: "
        f"Why decisions were made, failure lessons, infra/deploy info, preferences."
    )


def apply_rule_bonus(
    content: str,
    tags: list[str],
    base_importance: float,
) -> float:
    """Apply all matching rules to the base importance score.

    Args:
        content: Memory content text.
        tags: Memory tags.
        base_importance: LLM-provided importance (1.0-10.0).

    Returns:
        Adjusted importance, clamped to [IMPORTANCE_MIN, IMPORTANCE_MAX].
    """
    total_bonus = 0.0
    matched_rules: list[str] = []

    for rule in IMPORTANCE_RULES:
        matched = False
        if rule.pattern is not None and rule.pattern.search(content):
            matched = True
        if rule.tag_match is not None and rule.tag_match in tags:
            matched = True
        if matched:
            total_bonus += rule.bonus
            matched_rules.append(rule.name)

    if matched_rules:
        logger.debug(
            "Importance rules matched: %s (bonus=%.1f)",
            ", ".join(matched_rules),
            total_bonus,
        )

    adjusted = base_importance + total_bonus
    return max(IMPORTANCE_MIN, min(IMPORTANCE_MAX, adjusted))


# ── Auto-tagging for Session Continuity ──────────────────────────

# Infrastructure keyword patterns → tags to add
_INFRA_TAG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"docker\s+(compose|run|exec|build|container)", re.IGNORECASE), "docker"),
    (re.compile(r"ssh\s+(-p\s+\d+\s+)?\S+@\S+", re.IGNORECASE), "ssh"),
    (re.compile(r"(컨테이너|container)\s+\S+", re.IGNORECASE), "docker"),
    (re.compile(r"(배포|deploy)\s*(경로|path|서버|server|완료|to\s|:)", re.IGNORECASE), "deployment"),
    (re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}[:/]\d+"), "infrastructure"),
    (re.compile(r"(dev.container|개발.컨테이너|dev-container)", re.IGNORECASE), "dev-container"),
    (re.compile(r"(서버|server)\s*(주소|address|IP|포트|port|URL)", re.IGNORECASE), "infrastructure"),
    (re.compile(r"(pytest|테스트\s*실행|test\s*run)\s+", re.IGNORECASE), "testing"),
    (re.compile(r"(명령.*실패|command.*fail|에러.*해결|error.*fix|올바른\s*명령)", re.IGNORECASE), "command-fix"),
]


def infer_infrastructure_tags(content: str, existing_tags: list[str]) -> list[str]:
    """Auto-detect infrastructure-related content and add tags.

    Scans memory content for infrastructure keywords (docker, ssh,
    deploy, IP addresses, etc.) and adds corresponding tags if not
    already present.  Used by MemoryStore.store() for Session
    Continuity forced recall.

    Args:
        content: Memory content text.
        existing_tags: Tags already assigned to the memory.

    Returns:
        Updated tag list (may be the same object if no tags added).
    """
    new_tags: set[str] = set()

    for pattern, tag in _INFRA_TAG_PATTERNS:
        if tag not in existing_tags and pattern.search(content):
            new_tags.add(tag)

    # If any infra-specific tag matched, also ensure "infrastructure" is present
    if new_tags and "infrastructure" not in existing_tags:
        new_tags.add("infrastructure")

    if not new_tags:
        return existing_tags

    result = list(existing_tags) + sorted(new_tags)
    logger.debug("Auto-tagged infrastructure: +%s", sorted(new_tags))
    return result
