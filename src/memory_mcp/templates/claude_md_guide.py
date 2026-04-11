"""Canonical CLAUDE.md guide template for Kandela projects.

This module contains the versioned guide template that gets inserted into
each project's CLAUDE.md file. When the Kandela server gains new features,
update GUIDE_VERSION and GUIDE_TEMPLATE here. All projects can then update
their CLAUDE.md via the /kd-update slash command.

v4 changes:
- CLAUDE.md guide section is now compact (~450 tokens, down from ~2,156).
- Detailed reference information is in a separate `.kandela-guide.md` file.
- REFERENCE_TEMPLATE contains the full detailed reference.

v6 changes:
- Lazy Retrieval: auto_recall defaults to mode='brief' (~100-200 tokens).
- New tool: context_search — compact on-demand search for mid-conversation use.
- Updated usage rules to favor on-demand search over eager loading.

v7 changes:
- Renamed slash commands: pm-* → dm* (no dash, easier typing).
- New tool: inbox — unreviewed memo management (telegram/auto-save source).
- Inbox workflow: telegram memos tagged with 'telegram' + 'unreviewed', review via /kd-inbox.
- Brief mode now shows unreviewed memo count notification.

v8 changes:
- New slash command: /kd-status — show current project + status brief.
- Telegram bot: /kd-status command added (same as /status but unified naming).

v9 changes:
- New slash command: /kd-task — view and process pending tasks from Telegram.
- Task queue: Telegram /kd-task registers tasks → Claude Code detects at session start → processes → Telegram notifies completion.
- Brief mode now shows pending task count notification.
- Security: task content length limit (500 chars), pending task cap (20 per project), duplicate detection.

v10 changes:
- Removed `.kandela-guide.md` auto-reference from CLAUDE.md guide section.
  Previously the line "상세 레퍼런스: `.kandela-guide.md`" caused Claude to auto-load
  the 127-line reference file (~1,300 tokens) at every session start, negating the
  lazy retrieval savings. Now the reference file is only loaded on-demand via /kd-help.
- Net saving: ~1,300 tokens per session start.

v13 changes:
- Rule 7 strengthened: "명령 실패 시 다음 작업 전에 즉시 gotcha 저장" (기존: 저장 권장)
- New Rule 8: "명령 실패 시 재시도 전에 context_search로 기존 gotcha 확인"
- Rule numbering: 8→9 (긴 세션 체크포인트)
- PreCompact hook: 5개 지시항목 → 2개로 간소화 (summarize + gotcha 필수)

v14 changes:
- Rule 7/8 scope expanded: "셸 명령 실패" → "도구/API/명령 실패" (Notion API, HTTP 에러 등 포함)
- New Rule 9: "해매기 방지" — 같은 도구 3회 실패 시 반드시 접근 변경 + gotcha 검색
- Rule numbering: 9→10 (긴 세션 체크포인트)
- PreCompact hook: gotcha 범위 확장 (도구/API 실패 포함)

v16 changes (Memory Focus):
- 저장 원칙 핵심 변경: "코드에 없는 것만 저장" 원칙 도입
  - 코드/파일에서 읽을 수 있는 정보 (구조, 패턴, import 등) 저장 금지
  - Why (결정 이유), Gotcha, 삭제된 히스토리, 인프라/배포, 선호/컨벤션만 저장
- Importance 예시를 code-invisible 중심으로 업데이트
- brief recall이 코드에 없는 정보를 우선 반환하도록 설명 변경
- Rule 1 설명 업데이트: "gotcha + 결정 이유 + 인프라" 우선 로드

v19 changes:
- GUIDE_TEMPLATE 대폭 슬림화 (~450 tok → ~50 tok): 규칙 1~13, Importance 테이블, 저장 원칙 제거
- 전체 규칙은 command_prompts.py COMMAND_PROMPTS["guide"]로 이동 (서버 사이드, IP 보호)
- 클라이언트 CLAUDE.md에는 project ID + auto_recall 호출 + 가이드 참조 링크만 노출
- auto_recall brief footer에 핵심 규칙 compact 1줄 추가 (매 세션 자동 전달)

v18 changes:
- New Rule 12: "리소스 확인" — API 키/설정이 없다고 판단하기 전에 context_search + .env/config 확인 필수
- Brief recall에 Env/Resources 섹션 추가 — env/api-key/credentials/infrastructure/config 태그 메모리 자동 로드
- 세션 시작 시 "이 프로젝트에서 사용 가능한 리소스" 자동 표시

v17 changes:
- PreCompact hook: 2개 지시항목 → 3개 (환경 경로 + 반복 실패 패턴 저장 추가)
  - [3A] 빌드/런타임 환경 경로 (JAVA_HOME, SDK paths 등) 자동 저장
  - [3B] 반복 실패 패턴 (같은 에러 3회+) gotcha 저장
- PostToolUse context monitor hook 신규: 적응형 체크 간격으로 컨텍스트 사용량 모니터링
  - 0~50%→120초, 50~70%→60초, 70~85%→30초, 85%+→10초+경고
  - 85% 초과 시 pre-compact 프롬프트 출력 (PreCompact hook이 auto-compaction에서 미발동 대응)
- Hook 스크립트 bash 버그 수정: grep -c . || echo 0 → 0\n0 문제 해결
- INSTALL_VERSION 11 (PostToolUse hook 추가, 클라이언트 재설치 필요)

v20 changes:
- 듀얼 저장 규칙 추가: 중요 사항 저장 시 로컬 메모리 + Kandela store 동시 저장 필수

Uses string.Template ($placeholder) instead of str.format() because the
guide text contains many {} characters in code blocks (bash, JSON examples).
"""

from __future__ import annotations

import string
from typing import Any

GUIDE_VERSION = 20

# NOTE: Uses $placeholder syntax (string.Template).
# To include a literal $ in the template, use $$.

# ── Compact guide for CLAUDE.md (~50 tokens) ──────────────────────
GUIDE_TEMPLATE = string.Template("""\
<!-- KANDELA-GUIDE-START v$version -->
## Kandela
memory project ID: $project_id

세션 시작 시 `auto_recall(project='$project_id', mode='brief')` 호출.
대화 중: `context_search(query='주제')` on-demand 검색.
서브에이전트 생성 시: 프롬프트에 `context_search(query='작업주제', project='$project_id')` 호출 지시 포함.
전체 가이드: `get_command_prompt(command='guide')`.
중요 사항 저장 시 로컬 메모리 + Kandela store 동시 저장 필수.
<!-- KANDELA-GUIDE-END -->""")

# ── Detailed reference file (.kandela-guide.md) ─────────────────
REFERENCE_TEMPLATE = string.Template("""\
<!-- KANDELA-REFERENCE v$version -->
# Kandela 상세 레퍼런스

> 핵심 규칙은 CLAUDE.md 참조. 여기는 필요 시 조회용.

## Auto-Recall
세션 시작 시 `auto_recall`이 자동 호출된다.

### Brief 모드 (기본값, ~100-300 토큰)
- 코드에 없는 정보만 우선 반환: Gotcha → 결정 이유 → 인프라/배포 → 보유 리소스(API키/환경변수) → 기타 critical
- critical memories (importance >= 9.0) 중 code-invisible 항목 우선
- env/api-key/credentials/infrastructure/config 태그 메모리 자동 포함
- 대화 중 필요한 기억은 `context_search`로 on-demand 검색

### Full 모드 (compaction 복구 시)
1. **importance >= 9.0 (CRITICAL)** — 항상 전부 로드
2. **최근 세션 요약** (summary 타입, 최신 3개)
3. **관련 메모리** (importance >= 3.0, 시맨틱 검색 + MMR + 시간 가중치)
4. **최근 메모리** (low importance 제외, 시간순)
5. **LOW (importance < 3.0)** — 위 결과가 부족할 때만 보충
- `mode='full'` 또는 `recall_source='compact'`로 명시 호출
- 검색/회상된 메모리의 사용 빈도가 자동 추적됨

## context_search (대화 중 On-demand 검색)
대화 중 빠른 기억 검색용. 압축 포맷으로 ~50 토큰/건.

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `query` | 필수 | 시맨틱 검색 쿼리 |
| `project` | 필수 | 프로젝트 ID |
| `n_results` | 3 | 최대 결과 수 (1~10) |
| `include_content` | true | false면 메타만 (초경량) |

출력 포맷: `[type] 내용_80자... (imp:X.X, YYYY-MM-DD)`

## search 고급 검색 옵션

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `use_hybrid` | false | BM25 + 시맨틱 RRF 결합. 키워드/식별자 검색 시 |
| `use_mmr` | false | 결과 다양성 (MMR, lambda=0.7) |
| `time_weighted` | false | 최신 우선 (importance별 감쇄 차등) |
| `tags` | null | 태그 OR 필터 |
| `importance_min` | null | 최소 importance 필터 |
| `importance_max` | null | 최대 importance 필터 |
| `date_after` | null | ISO date 이후만 |
| `date_before` | null | ISO date 이전만 |

**팁**: 의미 검색=기본 / 키워드=hybrid / 다양성=mmr / 최신=time_weighted / 조합 가능

## 서버 Importance 보정 규칙
저장 시 서버가 content/tags 패턴 분석하여 importance 보정:

| 패턴 | 보정 |
|------|------|
| SSH 접속 명령 | +2.0 |
| Docker 명령 | +1.5 |
| API 키/시크릿 | +2.0 |
| 배포 경로 키워드 | +1.5 |
| 주의/gotcha | +1.5 |
| 반복 실수 | +2.0 |
| 명령 실패/교정 패턴 | +1.5 |
| unfinished 태그 | +1.5 |
| gotcha 태그 | +1.0 |
| 파일 위치 정보 | +1.5 |
| auto-saved 태그 | -3.0 |

사용 빈도 반영: 자주 검색/회상 → 자동 중요도 상승

## MCP 도구 (23개)

| 도구 | 용도 |
|------|------|
| `store` | 기억 저장 (type, importance 1.0~10.0) |
| `search` | 시맨틱/하이브리드 검색 (상세 메타데이터) |
| `context_search` | 💡 대화 중 압축 검색 (50토큰/건, 기본 3건) |
| `report_failure` | 🛑 실패 보고 + Circuit Breaker (gotcha 반환, 3회 시 STOP) |
| `inbox` | 📬 미확인 메모 조회/확인 처리 (텔레그램/자동저장) |
| `delete` | 개별 삭제 |
| `update` | 기억 수정 (내용/타입/중요도/태그) |
| `summarize_session` | 세션 요약 |
| `list_projects` | 프로젝트 목록 |
| `stats` | 통계 |
| `auto_recall` | 자동 회상 (brief/full 모드, gotcha 사전 주입) |
| `project_rename` | 이름 변경 |
| `project_delete` | 삭제 (confirm 필수) |
| `get_guide` | 가이드 템플릿 |
| `get_command_prompt` | 슬래시 명령 프롬프트 (서버사이드) |
| `confirm_change` | 기존 결정 변경 시 2-way 확인 |
| `infra_update` | 프로젝트 인프라/테스트 문서 생성·갱신 |
| `infra_get` | 인프라 문서 조회 |
| `progress_update` | 프로젝트 진행 상황 문서 생성·갱신 |
| `progress_get` | 진행 상황 문서 조회 |
| `checklist_add` | 이름 있는 체크리스트에 항목 추가 |
| `checklist_get` | 체크리스트 조회 (done/total) |
| `checklist_done` | 체크리스트 항목 완료/해제 |

## 슬래시 명령

| 명령 | 용도 |
|------|------|
| `/kd-init <id>` | CLAUDE.md 가이드 설정 + 레퍼런스 파일 생성 |
| `/kd-link <id>` | 기존 프로젝트를 현재 디렉토리에 연결 |
| `/kd-update` | 가이드 업데이트 + 기억 최신화 |
| `/kd-list` | 프로젝트 목록 |
| `/kd-load <이름>` | 다른 프로젝트 기억 조회 |
| `/kd-rename <현재> <새이름>` | 이름 변경 |
| `/kd-delete <이름>` | 삭제 |
| `/kd-inbox` | 미확인 메모 조회/확인 처리 |
| `/kd-status` | 현재 프로젝트 확인 + 상태 브리프 |
| `/kd-task` | 대기 작업 확인/처리 (텔레그램 연동) |
| `/kd-worker` | 자동 작업 워커 관리 (enable/disable/status) |
| `/kd-workspace` | 워크스페이스 경로 조회/변경 |
| `/kd-activation` | Memory Activation 활용률 통계 |
| `/kd-help` | 도움말 |

## 클라이언트 MCP 연결 방법
~/.claude/settings.json에 mcpServers를 넣으면 안 됨!

```bash
# CLI 가능한 환경 (MacBook 등):
claude mcp add --transport http --scope user memory https://api.kandela.ai/mcp

# CLI 없는 원격 환경 (WSL, 컨테이너):
# ~/.claude.json의 최상위에 mcpServers 추가:
# {"mcpServers": {"memory": {"type": "http", "url": "https://api.kandela.ai/mcp"}}}
```

## 크로스 프로젝트 기능
- **글로벌 기억**: `store(is_global=True, ...)` → `_global` 프로젝트에 저장. 모든 auto_recall 시 자동 로드.
- **다른 프로젝트 검색**: `search(source_project="다른프로젝트", ...)` → 지정 프로젝트에서 검색.
- **기억 링크**: `store(linked_projects=["프로젝트B"], ...)` → 프로젝트B의 auto_recall에 포함됨.
- **자동 추천**: auto_recall 시 다른 프로젝트의 관련 기억을 시맨틱 검색으로 자동 발견.

## 멀티에이전트 사용 시 주의
서브에이전트(Agent 도구)를 생성할 때 **반드시** 프롬프트에 다음을 포함:
1. `context_search(query='작업 주제', project='$project_id')` 호출 지시 — 서브에이전트는 auto_recall을 받지 못하므로 관련 기억을 직접 조회해야 함
2. 프로젝트 ID 전달: `project='$project_id'`
3. gotcha가 있을 수 있으므로 작업 전 기억 검색 필수

예시:
```
Agent 프롬프트에 추가:
"작업 시작 전 context_search(query='이 작업 관련 주의사항', project='my_project')를 호출하여 관련 gotcha를 확인하세요."
```

## 레거시 호환
- 기존 `priority` 파라미터 동작: critical->9.0, normal->5.0, low->2.0 변환
""")


# Pro-only tool lines in the MCP tool table — replaced with summary for FREE tier
_PRO_TOOL_LINES = [
    '| `report_failure` | 🛑 실패 보고 + Circuit Breaker (gotcha 반환, 3회 시 STOP) |',
    '| `confirm_change` | 기존 결정 변경 시 2-way 확인 |',
]

_PRO_SUMMARY = (
    "\n## Pro 기능 (현재 비활성)\n"
    "- Circuit Breaker: 반복 실패 자동 방지 (`report_failure`)\n"
    "- Prompt Guard: 기존 결정 충돌 감지 (`confirm_change`)\n"
    "→ 업그레이드: 대시보드 Account 페이지 / 셀프호스팅하면 모든 기능 무료\n"
)


def get_guide(project_id: str, tier: str | None = None, current_version: int | None = None) -> dict[str, Any]:
    """Return the guide template with project_id substituted.

    Args:
        project_id: Project identifier to embed in the guide.
        tier: User tier ('free', 'pro', 'admin', or None).
              None (single-user mode) shows all features.
        current_version: Client's current guide version (from CLAUDE.md marker).
              If provided, server compares and sets needs_update flag.

    Returns:
        Dict with 'version' (int), 'needs_update' (bool), 'content' (str),
        and 'reference_content' (str for .kandela-guide.md) keys.
    """
    content = GUIDE_TEMPLATE.substitute(
        version=GUIDE_VERSION,
        project_id=project_id,
    )

    # FREE tier: replace Pro tool details with summary
    if tier == "free":
        for line in _PRO_TOOL_LINES:
            content = content.replace(line, "")
        # Replace "## MCP 도구 (23개)" count
        content = content.replace("## MCP 도구 (23개)", "## MCP 도구 (21개 + Pro 2개)")
        content += _PRO_SUMMARY

    reference_content = REFERENCE_TEMPLATE.substitute(
        version=GUIDE_VERSION,
        project_id=project_id,
    )
    needs_update = (current_version is None) or (current_version < GUIDE_VERSION)
    return {
        "version": GUIDE_VERSION,
        "needs_update": needs_update,
        "content": content,
        "reference_content": reference_content,
    }
