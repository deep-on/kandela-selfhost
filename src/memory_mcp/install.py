"""One-liner install script generator and HTTP endpoint.

Serves ``curl -s http://SERVER:8321/install | bash`` — a single command
that installs all client-side files (slash commands, hooks, settings.json)
and configures ``~/.claude.json`` with the MCP server connection.

All 19 client files are embedded as Python string constants so there are
no external file dependencies.
"""

from __future__ import annotations

import hashlib
import os
import re
import textwrap
from typing import Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from memory_mcp.i18n import TRANSLATIONS, SUPPORTED_LANGS, detect_lang, shell_i18n_block, t

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Client Files — embedded as Python strings
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _hook_i18n_block(keys: list[str]) -> str:
    """Generate an inlined bash locale-detection + _t() block for the given keys.

    Used to embed i18n support directly in hook scripts (which are standalone
    bash files with no access to the Python i18n module).
    """
    lines = [
        "# ── Locale detection ──",
        "_KANDELA_LANG=\"${KANDELA_LANG:-}\"",
        "if [ -z \"$_KANDELA_LANG\" ]; then",
        "  _RAW_LANG=\"${LANG:-${LC_ALL:-en_US}}\"",
        "  _KANDELA_LANG=\"${_RAW_LANG:0:2}\"",
        "fi",
        "case \"$_KANDELA_LANG\" in",
        "  ko|ja|de|fr|es|pt|it) ;;",
        "  *) _KANDELA_LANG=\"en\" ;;",
        "esac",
        "",
        "# _t KEY — returns translated string for current locale",
        "_t() {",
        "  local _KEY=\"$1\"",
        "  case \"${_KANDELA_LANG}:${_KEY}\" in",
    ]
    for key in keys:
        table = TRANSLATIONS.get(key, {})
        for lang in ("ko", "ja", "de", "fr", "es", "pt", "it"):
            val = table.get(lang, "")
            if val:
                val_escaped = val.replace("'", "'\\''")
                lines.append(f"    {lang}:{key}) echo '{val_escaped}' ;;")
        en_val = table.get("en", key)
        en_escaped = en_val.replace("'", "'\\''")
        lines.append(f"    *:{key}) echo '{en_escaped}' ;;")
    lines += [
        "  esac",
        "}",
        "",
    ]
    return "\n".join(lines)


def _hook_python_detect_block() -> str:
    """Generate a bash block that detects python3/python and sets $PYTHON.

    Inserted at the top of every hook script so they work on Windows
    (Git Bash) where only 'python' exists, not 'python3'.
    """
    return textwrap.dedent("""\
        # ── Python detection ──
        PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
        if [ -z "$PYTHON" ]; then
          echo "Kandela: python3/python not found, hook skipped" >&2
          exit 0
        fi
        export PYTHONUTF8=1
    """)


# Bump when SKILL commands or hook scripts change (clients need reinstall)
INSTALL_VERSION = 32

# ── Feature version requirements ────────────────────────────────────
# Features that require a specific Claude Code minimum version.
# Used at install time to conditionally install, and at runtime for
# double-defense checks inside hook scripts.
FEATURE_MIN_VERSION: dict[str, str] = {
    "StopFailure": "2.1.78",  # StopFailure hook event introduced in v2.1.78
}

# ── Slash Commands (14) ────────────────────────────────────────────

CMD_DM_INIT = textwrap.dedent("""\
---
name: kd-init
description: "새 프로젝트에 memory 시스템 가이드를 CLAUDE.md에 설정한다."
argument-hint: "<project_id>"
---

`get_command_prompt(command='kd-init', arguments='$ARGUMENTS')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_LINK = textwrap.dedent("""\
---
name: kd-link
description: "기존 메모리 프로젝트를 현재 디렉토리에 연결한다. CLAUDE.md 가이드를 설정하고 기존 기억을 즉시 로드한다."
argument-hint: "<project_id>"
---

`get_command_prompt(command='kd-link', arguments='$ARGUMENTS')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_LIST = textwrap.dedent("""\
---
name: kd-list
description: "Kandela 프로젝트 목록 조회. 각 프로젝트의 메모리 개수를 함께 보여준다."
---

list_projects 도구를 호출하여 모든 프로젝트 목록과 각 프로젝트의 메모리 개수를 보여주세요.
""")

CMD_DM_LOAD = textwrap.dedent("""\
---
name: kd-load
description: "특정 프로젝트의 기억을 로드한다. 프로젝트 이름을 인자로 전달."
argument-hint: "<프로젝트이름>"
---

auto_recall 도구를 호출하여 지정된 프로젝트의 기억을 불러오세요.

프로젝트 이름: $ARGUMENTS

프로젝트 이름이 비어있으면 CLAUDE.md에서 `memory project ID:`를 찾아서 사용하세요.
불러온 기억을 바탕으로 프로젝트 현재 상태를 요약해주세요.
""")

CMD_DM_DELETE = textwrap.dedent("""\
---
name: kd-delete
description: "프로젝트를 삭제한다. 프로젝트 이름을 인자로 전달."
argument-hint: "<프로젝트이름>"
---

project_delete 도구를 호출하여 프로젝트를 삭제하세요.

프로젝트 이름: $ARGUMENTS

중요: 삭제는 되돌릴 수 없습니다.
1. 먼저 confirm=false로 호출하여 메모리 개수를 확인합니다.
2. 사용자에게 정말 삭제할 것인지 확인을 요청합니다.
3. 사용자가 확인하면 confirm=true로 다시 호출하여 삭제합니다.
""")

CMD_DM_RENAME = textwrap.dedent("""\
---
name: kd-rename
description: "프로젝트 이름을 변경한다. 현재이름과 새이름을 인자로 전달."
argument-hint: "<현재이름> <새이름>"
---

project_rename 도구를 호출하여 프로젝트 이름을 변경하세요.

인자: $ARGUMENTS
형식: <현재이름> <새이름> (공백으로 구분)

예시: /kd-rename old_project new_project

인자가 2개가 아니면 사용법을 안내해주세요.
""")

CMD_DM_UPDATE = textwrap.dedent("""\
---
name: kd-update
description: "가이드 버전 업데이트 + 기억 최신화 (공백 메우기)"
---

`get_command_prompt(command='kd-update')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_JOURNAL = textwrap.dedent("""\
---
name: kd-journal
description: "프로젝트별 일일 일지 조회/일괄 생성"
---

`get_command_prompt(command='kd-journal', arguments='$ARGUMENTS')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_VISIBILITY = textwrap.dedent("""\
---
name: kd-visibility
description: "프로젝트 크로스 검색 가시성 제어 (on/off/list)"
---

`get_command_prompt(command='kd-visibility', arguments='$ARGUMENTS')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_HELP = textwrap.dedent("""\
---
name: kd-help
description: "Kandela 프로젝트 관리 명령어 도움말을 보여준다."
---

아래 프로젝트 관리 슬래시 명령 도움말을 사용자에게 보여주세요:

## Kandela 명령어

### 일상
| 명령 | 설명 | 사용법 |
|------|------|--------|
| `/kd-status` | 현재 프로젝트 확인 + 상태 브리프 | `/kd-status` |
| `/kd-inbox` | 미확인 메모 조회/확인 처리 | `/kd-inbox` |
| `/kd-task` | 대기 작업 확인/처리 | `/kd-task` |
| `/kd-journal` | 일일 일지 조회/일괄 생성 | `/kd-journal`, `/kd-journal sync`, `/kd-journal 03-29` |
| `/kd-daily` | 일일 회의록 조회 | `/kd-daily`, `/kd-daily 어제` |
| `/kd-progress` | 프로젝트 종합 진행상황 보고서 | `/kd-progress` |

### 프로젝트 관리
| 명령 | 설명 | 사용법 |
|------|------|--------|
| `/kd-init` | CLAUDE.md에 memory 가이드 설정 (새 프로젝트) | `/kd-init <project_id>` |
| `/kd-link` | 기존 메모리 프로젝트를 현재 디렉토리에 연결 | `/kd-link <project_id>` |
| `/kd-list` | 프로젝트 목록 + 메모리 개수 조회 | `/kd-list` |
| `/kd-load` | 다른 프로젝트 기억 조회 | `/kd-load <프로젝트이름>` |
| `/kd-rename` | 프로젝트 이름 변경 | `/kd-rename <현재이름> <새이름>` |
| `/kd-delete` | 프로젝트 삭제 (확인 필요) | `/kd-delete <프로젝트이름>` |
| `/kd-workspace` | 워크스페이스 경로 조회/변경 | `/kd-workspace [새경로]` |
| `/kd-visibility` | 크로스 프로젝트 검색 가시성 제어 | `/kd-visibility on/off/all-on/all-off/restore/list` |

### 유지보수
| 명령 | 설명 | 사용법 |
|------|------|--------|
| `/kd-update` | 가이드 업데이트 + 기억 최신화 | `/kd-update` |
| `/kd-sync` | 로컬 캐시를 서버에 동기화 | `/kd-sync` |
| `/kd-activation` | Memory Activation 활용률 통계 | `/kd-activation` |
| `/kd-monitor` | log_analyzer 실행 (gotcha 위반 감지) | `/kd-monitor` |
| `/kd-guard` | Prompt Guard 설정 | `/kd-guard strong/medium/weak/explore/pause/stats` |
| `/kd-uninstall` | Kandela 완전 제거 | `/kd-uninstall` |
| `/kd-help` | 이 도움말 표시 | `/kd-help` |

### 자동 동작 (Hook — 사용자 개입 불필요)
| 시점 | 동작 | 방식 |
|------|------|------|
| 세션 시작 | 이전 기억 자동 회상 + 캐시 동기화 | SessionStart Hook |
| 프롬프트 입력 | Prompt Guard 충돌 감지 + gotcha 사전 주입 | UserPromptSubmit Hook |
| 도구 사용 전 | 위험 명령 gotcha 주입 | PreToolUse Hook |
| 도구 사용 후 | 컨텍스트 모니터 + 일지 자동 생성 | PostToolUse Hook |
| 매 응답 후 | 응답 내용 자동 저장 (중요 응답 즉시 서버 동기화) | Stop Hook |
| 컨텍스트 압축 전 | 세션 요약 + gotcha + 환경경로 저장 | PreCompact Hook |

### MCP 도구 (23개)
`store`, `search`, `context_search`, `inbox`,
`delete`, `update`, `summarize_session`, `list_projects`,
`stats`, `auto_recall`, `project_rename`, `project_delete`,
`get_guide`, `get_command_prompt`, `confirm_change`,
`report_failure`, `infra_update`, `infra_get`,
`progress_update`, `progress_get`,
`checklist_add`, `checklist_get`, `checklist_done`

> 💡 대화 중 빠른 검색: `context_search(query='주제')` — 압축 포맷, 3건 기본
> 📬 미확인 메모 확인: `/kd-inbox`
> 📊 일일 일지: `/kd-journal sync` — 전체 프로젝트 일지 일괄 생성 + 통합 보고
""")

CMD_DM_STATUS = textwrap.dedent("""\
---
name: kd-status
description: "현재 프로젝트 확인 및 상태 브리프 (메모리 수, 최근 세션, 미확인 메모)"
---

`get_command_prompt(command='kd-status')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_TASK = textwrap.dedent("""\
---
name: kd-task
description: "대기 작업 확인 및 처리"
---

`get_command_prompt(command='kd-task')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_INBOX = textwrap.dedent("""\
---
name: kd-inbox
description: "미확인 메모 조회 및 확인 처리"
---

`get_command_prompt(command='kd-inbox')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")


# ── Hooks (4) ─────────────────────────────────────────────────────

def _build_hook_session_start() -> str:
    _i18n = _hook_i18n_block([
        "hook_server_down",
        "hook_sync_stored",
        "hook_sync_pending",
        "hook_fallback_recall",
    ])
    return textwrap.dedent("""\
#!/bin/bash
# Kandela: SessionStart hook — thin wrapper
# 서버에서 워크스페이스 매칭 + 프롬프트 생성, 클라이언트는 auto-sync만

""") + _i18n + textwrap.dedent("""\
MCP_HEALTH_URL="__MCP_HEALTH_URL_PLACEHOLDER__"
[ -z "$MCP_HEALTH_URL" ] || [ "$MCP_HEALTH_URL" = "__MCP_HEALTH_URL""_PLACEHOLDER__" ] && exit 0

# Retry up to 2 times (3s apart) to handle server restart window (~10s)
HEALTH_JSON=""
for _retry in 1 2; do
  HEALTH_JSON=$(curl -s --max-time 3 "$MCP_HEALTH_URL" 2>/dev/null)
  echo "$HEALTH_JSON" | grep -q '"healthy"' && break
  [ $_retry -lt 2 ] && sleep 3
done
MCP_BASE="${MCP_HEALTH_URL%/api/health}"
if ! echo "$HEALTH_JSON" | grep -q '"healthy"'; then
  echo "$(_t hook_server_down)"
  exit 0
fi

CURR_CWD=$(pwd)
CURR_HOSTNAME=$(hostname)

# CLAUDE.md에서 로컬 가이드 버전 추출
LOCAL_GUIDE_V=""
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done
if [ -n "$CLAUDE_MD" ]; then
  LOCAL_GUIDE_V=$(grep -m1 'KANDELA-GUIDE-START' "$CLAUDE_MD" | sed 's/.*GUIDE-START v\\([0-9]*\\).*/\\1/')
fi
LOCAL_INSTALL_V=""
INSTALL_V_FILE="$HOME/.claude/hooks/.kandela-install-version"
[ -f "$INSTALL_V_FILE" ] && LOCAL_INSTALL_V=$(cat "$INSTALL_V_FILE")

# API 키 로드 (multi-user 인증 — session-start에도 필요)
_SS_API_KEY=""
[ -f "$HOME/.claude/hooks/.kandela-api-key" ] && _SS_API_KEY=$(cat "$HOME/.claude/hooks/.kandela-api-key" | tr -d '\\n')

# 서버에 평가 요청
export _SS_CWD="$CURR_CWD" _SS_HOST="$CURR_HOSTNAME" _SS_BASE="$MCP_BASE"
export _SS_LGV="$LOCAL_GUIDE_V" _SS_LIV="$LOCAL_INSTALL_V" _SS_API_KEY
RESP=$($PYTHON << 'PYEOF'
import json, os, sys, urllib.request

cwd = os.environ.get("_SS_CWD", "")
hostname = os.environ.get("_SS_HOST", "")
base = os.environ.get("_SS_BASE", "")
lgv = os.environ.get("_SS_LGV", "")
liv = os.environ.get("_SS_LIV", "")
api_key = os.environ.get("_SS_API_KEY", "").strip()

if not cwd or not base:
    sys.exit(0)

d = {"cwd": cwd, "hostname": hostname}
if lgv:
    try: d["local_guide_version"] = int(lgv)
    except Exception: pass
if liv:
    try: d["local_install_version"] = int(liv)
    except Exception: pass

payload = json.dumps(d).encode()
headers = {"Content-Type": "application/json"}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"

req = urllib.request.Request(
    f"{base}/api/hook-eval/session-start",
    data=payload,
    headers=headers,
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=5)
    print(resp.read().decode())
except Exception:
    sys.exit(0)
PYEOF
)

[ -z "$RESP" ] && exit 0

# 프롬프트 출력
PROMPT=$(echo "$RESP" | $PYTHON -c "
import json, sys
try:
    r = json.load(sys.stdin)
    p = r.get('prompt', '')
    if p: print(p)
except Exception: pass
" 2>/dev/null)

# 프로젝트 ID 추출 (auto-sync용)
PROJECT_ID=$(echo "$RESP" | $PYTHON -c "
import json, sys
try:
    r = json.load(sys.stdin)
    print(r.get('project_id', ''))
except Exception: print('')
" 2>/dev/null)

# 서버 매칭 실패 시 폴백: CLAUDE.md에서 추출
if [ -z "$PROJECT_ID" ] && [ -n "$CLAUDE_MD" ]; then
  PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
  if [ -n "$PROJECT_ID" ]; then
    PROMPT="[Memory] Project: $PROJECT_ID
$(_t hook_fallback_recall | sed "s/{project}/$PROJECT_ID/g;s|{cwd}|$CURR_CWD|g;s/{hostname}/$CURR_HOSTNAME/g")"
  fi
fi

# ── 미동기화 로컬 캐시 자동 동기화 ──
SYNC_HINT=""
if [ -n "$PROJECT_ID" ]; then
  CACHE_DIR="$HOME/.claude/kandela-cache/$PROJECT_ID"
  if [ -d "$CACHE_DIR" ]; then
    UNSYNCED=$(find "$CACHE_DIR" -name "*.meta.json" -exec grep -l '"synced": false' {} \\; 2>/dev/null | grep -c . 2>/dev/null)
    UNSYNCED=${UNSYNCED:-0}
    if [ "$UNSYNCED" -gt 0 ]; then
      API_KEY_FILE="$HOME/.claude/hooks/.kandela-api-key"
      SYNC_API_KEY=""
      [ -f "$API_KEY_FILE" ] && SYNC_API_KEY=$(cat "$API_KEY_FILE")
      SYNC_RESULT=$(CACHE_DIR="$CACHE_DIR" PROJECT_ID="$PROJECT_ID" MCP_BASE_URL="$MCP_BASE" SYNC_API_KEY="$SYNC_API_KEY" $PYTHON << 'PYEOF'
import os, json, glob, sys, urllib.request
cache_dir = os.environ.get("CACHE_DIR", "")
project = os.environ.get("PROJECT_ID", "")
base_url = os.environ.get("MCP_BASE_URL", "")
api_key = os.environ.get("SYNC_API_KEY", "")
if not all([cache_dir, project, base_url]):
    print("0 0"); sys.exit(0)
meta_files = []
for mf in glob.glob(os.path.join(cache_dir, "*.meta.json")):
    try:
        with open(mf) as f: meta = json.load(f)
        if not meta.get("synced", False): meta_files.append(mf)
    except Exception: pass
if not meta_files:
    print("0 0"); sys.exit(0)
entries = []
for mf in meta_files:
    jf = mf.replace(".meta.json", ".jsonl")
    if not os.path.exists(jf): continue
    try:
        with open(jf) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    c = ev.get("content", ""); ts = ev.get("ts", "")
                    if len(c) >= 100: entries.append({"content": c[:3000], "ts": ts})
                except Exception: pass
    except Exception: pass
stored = 0; ok = True
for i in range(0, len(entries), 20):
    batch = entries[i:i+20]
    p = json.dumps({"project": project, "entries": batch}).encode()
    h = {"Content-Type": "application/json"}
    if api_key: h["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(f"{base_url}/api/cache-ingest", data=p, headers=h, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        stored += json.loads(resp.read()).get("stored", 0)
    except Exception: ok = False; break
if ok:
    for mf in meta_files:
        try:
            with open(mf) as f: m = json.load(f)
            m["synced"] = True
            with open(mf, "w") as f: json.dump(m, f, ensure_ascii=False, indent=2)
        except Exception: pass
print(f"{stored} {len(meta_files)}")
PYEOF
      )
      SYNC_STORED=$(echo "$SYNC_RESULT" | awk '{print $1}')
      SYNC_SESSIONS=$(echo "$SYNC_RESULT" | awk '{print $2}')
      if [ -n "$SYNC_STORED" ] && [ "$SYNC_STORED" -gt 0 ] 2>/dev/null; then
        SYNC_HINT="\\n$(_t hook_sync_stored | sed "s/{n}/$SYNC_STORED/g;s/{sessions}/$SYNC_SESSIONS/g")"
      elif [ "$UNSYNCED" -gt 0 ]; then
        SYNC_HINT="\\n$(_t hook_sync_pending | sed "s/{n}/$UNSYNCED/g")"
      fi
    fi
  fi
fi

# ── Docs map: 프로젝트 문서 스캔 → 서버 전송 (Brief Recall에 포함) ──
_DOC_LIST="/tmp/.memory-docs-$$"
rm -f "$_DOC_LIST"
_DOC_EXTS="md html pdf pptx"
shopt -s nullglob 2>/dev/null
for _ext in $_DOC_EXTS; do
  for _f in *."$_ext"; do
    [ "$_f" = "CLAUDE.md" ] && continue
    [ -f "$_f" ] && printf '%s\n' "$_f" >> "$_DOC_LIST"
  done
done
for _dir in docs doc documents notes reports papers; do
  [ -d "$_dir" ] || continue
  for _ext in $_DOC_EXTS; do
    for _f in "$_dir"/*."$_ext"; do
      [ -f "$_f" ] && printf '%s\n' "$_f" >> "$_DOC_LIST"
    done
  done
done
shopt -u nullglob 2>/dev/null
if [ -f "$_DOC_LIST" ] && [ -n "$MCP_BASE" ] && [ -n "$PROJECT_ID" ]; then
  _DOCS_JSON=$(sort -u "$_DOC_LIST" | head -50 | $PYTHON -c "
import sys, json
files = [l.strip() for l in sys.stdin if l.strip()]
print(json.dumps({'project': '$PROJECT_ID', 'files': files}))
" 2>/dev/null)
  rm -f "$_DOC_LIST"
  if [ -n "$_DOCS_JSON" ]; then
    _AUTH_HDR=""
    [ -f ~/.claude/hooks/.kandela-api-key ] && _AUTH_HDR="Authorization: Bearer $(cat ~/.claude/hooks/.kandela-api-key)"
    curl -sf --max-time 2 -X POST "$MCP_BASE/api/docs-ingest" \
      -H "Content-Type: application/json" \
      ${_AUTH_HDR:+-H "$_AUTH_HDR"} \
      -d "$_DOCS_JSON" >/dev/null 2>&1 &
  fi
else
  rm -f "$_DOC_LIST"
fi

if [ -n "$PROMPT" ]; then
  echo "$PROMPT"
  [ -n "$SYNC_HINT" ] && echo -e "$SYNC_HINT"
fi
exit 0
""")


HOOK_SESSION_START = _build_hook_session_start()

CMD_DM_WORKSPACE = textwrap.dedent("""\
---
name: kd-workspace
description: "프로젝트 워크스페이스 경로 조회/변경. CLAUDE.md가 있는 디렉토리 경로를 관리한다."
argument-hint: "[new_path]"
---

`get_command_prompt(command='kd-workspace', arguments='$ARGUMENTS')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_SYNC = textwrap.dedent("""\
---
name: kd-sync
description: "로컬 캐시된 기억을 서버에 정제/동기화한다."
---

`get_command_prompt(command='kd-sync')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_DAILY = textwrap.dedent("""\
---
name: kd-daily
description: "일일 회의록 조회. 인자 없음=오늘, 어제=어제, MM-DD 또는 YYYY-MM-DD=특정날짜"
argument-hint: "[어제|MM-DD|YYYY-MM-DD]"
---

`get_command_prompt(command='kd-daily', arguments='$ARGUMENTS')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_GUARD = textwrap.dedent("""\
---
name: kd-guard
description: "Prompt Guard 강도/톤/일시정지 조회·설정. 인자: strong/medium/weak/explore, tone friendly/brief/formal, pause [기간], resume, stats"
---

Prompt Guard 설정 명령입니다.

인자($ARGUMENTS)를 확인하세요:

---

## 인자가 없는 경우 — 현재 상태 조회

다음 bash를 실행하세요:
```bash
echo "level=$(cat ~/.claude/hooks/.kandela-guard-level 2>/dev/null || echo 'medium')"
echo "tone=$(cat ~/.claude/hooks/.kandela-guard-tone 2>/dev/null || echo 'friendly')"
PAUSE_FILE="$HOME/.claude/hooks/.kandela-guard-paused-until"
if [ -f "$PAUSE_FILE" ]; then
    PAUSE_UNTIL=$(cat "$PAUSE_FILE")
    NOW=$($PYTHON -c "import time; print(int(time.time()))")
    REMAINING=$($PYTHON -c "r=int('$PAUSE_UNTIL')-int('$NOW'); print(f'{r//3600}h {(r%3600)//60}m' if r>0 else 'expired')")
    echo "pause=active until_ts=$PAUSE_UNTIL remaining=$REMAINING"
else
    echo "pause=off"
fi
```

결과를 아래 표들과 함께 보여주세요.

---

## 강도 변경

`strong`, `강`, `강하게` → `strong` 설정
`medium`, `중`, `중간` → `medium` 설정
`weak`, `약`, `약하게` → `weak` 설정
`explore`, `탐색`, `탐색모드` → `explore` 설정

```bash
echo "LEVEL" > ~/.claude/hooks/.kandela-guard-level
```

---

## 톤 변경 (인자가 `tone`으로 시작)

`tone friendly` 또는 `tone 친근` → `friendly`
`tone brief` 또는 `tone 간결` → `brief`
`tone formal` 또는 `tone 공식` → `formal`

```bash
echo "TONE" > ~/.claude/hooks/.kandela-guard-tone
```

---

## 일시 정지 (인자가 `pause`로 시작)

기간 형식: `30m`, `1h` (기본), `2h`, `4h`, `1d`

다음 bash를 실행하세요 (기간을 파싱해서 timestamp 계산):
```bash
DURATION="${ARGUMENTS#pause}"
DURATION=$(echo "$DURATION" | tr -d ' ')
[ -z "$DURATION" ] && DURATION="1h"
SECONDS_ADD=$($PYTHON -c "
import re, sys
m = re.match(r'(\\d+)([mhd])', '$DURATION')
if m:
    n, u = int(m.group(1)), m.group(2)
    print(n * {'m':60,'h':3600,'d':86400}[u])
else:
    print(3600)
")
UNTIL=$($PYTHON -c "import time; print(int(time.time()) + $SECONDS_ADD)")
echo "$UNTIL" > ~/.claude/hooks/.kandela-guard-paused-until
UNTIL_STR=$($PYTHON -c "import datetime; print(datetime.datetime.fromtimestamp($UNTIL).strftime('%H:%M'))")
echo "Guard paused until $UNTIL_STR ($DURATION)"
```

---

## 즉시 재개 (인자가 `resume`)

```bash
rm -f ~/.claude/hooks/.kandela-guard-paused-until
echo "Guard resumed"
```

---

## 통계 (인자가 `stats`)

```bash
$PYTHON -c "
import json, time, collections
from pathlib import Path

stats_file = Path.home() / '.claude/hooks/.kandela-guard-stats.jsonl'
if not stats_file.exists():
    print('통계 없음 (아직 Guard가 발동된 적 없음)')
    exit()

now = time.time()
week_ago = now - 7 * 86400
entries = []
for line in stats_file.read_text().strip().splitlines():
    try:
        entries.append(json.loads(line))
    except:
        pass

recent = [e for e in entries if e.get('ts', 0) >= week_ago]
blocks = [e for e in recent if e.get('action') == 'block']
warns  = [e for e in recent if e.get('action') == 'warn']
total  = len(entries)

print(f'이번 주: {len(blocks)}건 차단, {len(warns)}건 알림')
print(f'전체 누적: {total}건')
if blocks:
    by_proj = collections.Counter(e.get('project','?') for e in blocks)
    print('프로젝트별 차단:')
    for proj, cnt in by_proj.most_common(5):
        print(f'  {proj}: {cnt}건')
"
```

---

설정 후 아래 표로 현재 상태를 보여주세요:

**강도 설정:**
| 레벨 | 보호 범위 | 현재 |
|------|-----------|------|
| **강 (strong)** | 거의 모든 결정 | |
| **중 (medium)** | 핵심 결정만 (기본값) | |
| **약 (weak)** | 치명적 결정만 | |
| **탐색 (explore)** | 알림만 (PoC/초기 개발) | |

**메시지 톤:**
| 톤 | 느낌 | 현재 |
|----|------|------|
| **친근 (friendly)** | 💡 혹시 놓치셨을까 봐... | |
| **간결 (brief)** | ⚠️ 이전 결정: ... | |
| **공식 (formal)** | ⚠️ 이전 결정과 충돌이 감지되었습니다. | |

현재 설정 행에 ✅ 표시를 추가하세요.
""")

CMD_DM_ACTIVATION = textwrap.dedent("""\
---
name: kd-activation
description: "Memory Activation 활용률 통계 조회. 기억이 실제로 얼마나 활용되는지 측정."
---

`get_command_prompt(command='kd-activation')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
""")

CMD_DM_PROGRESS = textwrap.dedent("""\
---
name: kd-progress
description: "프로젝트 종합 진행상황 보고서 (완료/미완료/결정사항/주의점). 'all' 인자 시 전체 프로젝트 통합 보고."
argument-hint: "[all]"
---

인자를 확인하세요: $ARGUMENTS

---

## 모드 A: `all` 인자가 있는 경우 — 전체 프로젝트 통합 보고

1. **프로젝트 목록 조회**: `list_projects()`를 호출하여 전체 프로젝트 목록을 가져옵니다.
   - `_global` 프로젝트는 제외하세요.
   - 메모리 0건인 프로젝트도 제외하세요.

2. **프로젝트별 진행상황 수집**: 각 프로젝트마다 아래 2개 검색을 **병렬로** 수행하세요:
   - `context_search(query='recent progress completed deployed decision', project='{project_id}', n_results=5)`
   - `context_search(query='pending todo unfinished next step problem', project='{project_id}', n_results=3)`

3. **통합 보고서 작성**: 아래 형식으로 보여주세요:

```
📊 전체 프로젝트 진행상황 통합 보고
━━━━━━━━━━━━━━━━━━━━
📅 {오늘 날짜}

### {project_1} (N건)
**최근**: (최근 완료/진행 중인 것 1~3줄)
**다음**: (다음 작업/미완료 1~2줄)

...
━━━━━━━━━━━━━━━━━━━━
📦 총 프로젝트: N개 | 총 기억: N건
```

4. **간결하게**: 각 프로젝트는 최대 5줄.

---

## 모드 B: `all` 인자가 없는 경우 — 단일 프로젝트 보고

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 찾아 project_id를 추출하세요.

2. **기억 수집** (4가지 검색을 병렬로):
   - `search(query='decision architecture design choice', project='{project_id}', memory_type='decision', n_results=10, time_weighted=true)`
   - `search(query='session summary progress completed', project='{project_id}', memory_type='summary', n_results=10, time_weighted=true)`
   - `search(query='completed done implemented deployed', project='{project_id}', memory_type='fact', n_results=5, time_weighted=true)`
   - `search(query='pending todo incomplete unfinished next step', project='{project_id}', n_results=5, time_weighted=true)`

3. **보고서 작성**:

```
📊 프로젝트 진행상황: {project_id}
━━━━━━━━━━━━━━━━━━━━
## 최근 완료 사항
## 미완료/다음 작업
## 주요 결정사항
## 특이사항/주의점
━━━━━━━━━━━━━━━━━━━━
📦 기억 통계: 총 N건 | fact N | decision N | ...
```

4. **없는 섹션은 생략하세요.**
""")

CMD_DM_LOG_REVIEW = textwrap.dedent("""\
---
name: kd-log-review
description: "log_analyzer 실행 및 결과 리뷰"
---

`get_command_prompt(command='kd-log-review')` 도구를 호출하고, 반환된 지시사항을 그대로 따르세요.
프로젝트: 현재 CLAUDE.md의 memory project ID를 사용하세요.
""")

CMD_DM_UNINSTALL = textwrap.dedent("""\
---
name: kd-uninstall
description: "Kandela를 완전히 제거한다. 설치 전 상태로 100% 복구."
---

Kandela 언인스톨을 안내합니다.

다음 bash 명령을 실행하여 언인스톨 스크립트를 다운로드하고 실행하세요:

```bash
curl -sL https://kandela.ai/uninstall | bash
```

또는 서버 URL이 다른 경우:
```bash
curl -sL $SERVER_URL/uninstall | bash
```

**제거되는 항목:**
- MCP 서버 연결 (`~/.claude.json`에서 `kandela` 항목 제거)
- 훅 스크립트 (`~/.claude/hooks/kandela-*.sh` 전체)
- API 키 파일 (`~/.claude/hooks/.kandela-api-key`)
- 슬래시 명령 (`~/.claude/skills/kd-*/` 전체)
- settings.json의 Kandela 관련 hooks 항목

**보존되는 것:** 기존 MCP 서버, 다른 훅, 나머지 설정 — 설치 전 상태 그대로.

사용자가 확인하면 위 curl 명령을 실행하세요.
""")

def _build_hook_pre_compact() -> str:
    _i18n = _hook_i18n_block([
        "hook_precompact_header",
        "hook_precompact_server_down",
    ])
    return textwrap.dedent("""\
#!/bin/bash
# Kandela: PreCompact hook — 서버에서 프롬프트를 동적으로 가져옴
# 서버 불가 시 내장 fallback 사용. CLAUDE.md에 memory project ID 필요.

""") + _i18n + textwrap.dedent("""\
# CLAUDE.md 찾기: CWD → 상위 5단계
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done

if [ -n "$CLAUDE_MD" ]; then
  PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
  if [ -n "$PROJECT_ID" ]; then
    # 서버에서 동적 프롬프트 가져오기 (실패 시 fallback)
    MCP_HEALTH_URL="__MCP_HEALTH_URL_PLACEHOLDER__"
    PROMPT_TEXT=""
    if [ -n "$MCP_HEALTH_URL" ] && [ "$MCP_HEALTH_URL" != "__MCP_HEALTH_URL""_PLACEHOLDER__" ]; then
      MCP_BASE="${MCP_HEALTH_URL%/api/health}"
      _PC_SESSION="${CLAUDE_SESSION_ID:-}"
      PROMPT_TEXT=$(curl -s --max-time 3 "$MCP_BASE/api/hook-prompt/pre-compact?project=$PROJECT_ID&session_id=$_PC_SESSION" 2>/dev/null)
      # 에러 응답이면 비움
      if echo "$PROMPT_TEXT" | grep -q "^Unknown hook\\|^project param" 2>/dev/null; then
        PROMPT_TEXT=""
      fi
    fi
    if [ -n "$PROMPT_TEXT" ]; then
      echo "$PROMPT_TEXT"
    else
      echo "$(_t hook_precompact_header | sed "s/{project}/$PROJECT_ID/g")"
      echo "$(_t hook_precompact_server_down | sed "s/{project}/$PROJECT_ID/g")"
    fi
  fi
fi
exit 0
""")


HOOK_PRE_COMPACT = _build_hook_pre_compact()

HOOK_CONTEXT_MONITOR = textwrap.dedent("""\
#!/bin/bash
# Kandela: PostToolUse hook — thin wrapper
# 서버에 원시 데이터를 보내고, 서버가 판단 + 프롬프트를 반환

STDIN=$(cat 2>/dev/null)
TOOL_NAME=$(echo "$STDIN" | grep -o '"tool_name":"[^"]*"' | head -1 | sed 's/"tool_name":"\\(.*\\)"/\\1/')

# CLAUDE.md 찾기: CWD → 상위 5단계
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done
[ -z "$CLAUDE_MD" ] && exit 0
PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
[ -z "$PROJECT_ID" ] && exit 0

MCP_HEALTH_URL="__MCP_HEALTH_URL_PLACEHOLDER__"
[ -z "$MCP_HEALTH_URL" ] || [ "$MCP_HEALTH_URL" = "__MCP_HEALTH_URL""_PLACEHOLDER__" ] && exit 0
MCP_BASE="${MCP_HEALTH_URL%/api/health}"

# 상태 파일: 마지막 체크 시각 + 간격 + warned + tool_call_count + session_bloat_warned + milestones_hit
STATE="/tmp/.memory-ctx-monitor-${PROJECT_ID}"
NOW=$(date +%s)
LAST_CHECK=0; INTERVAL=120; WARNED=0; TOOL_CALL_COUNT=0; SESSION_BLOAT_WARNED=0; MILESTONES_HIT=0
if [ -f "$STATE" ]; then
  read -r LAST_CHECK INTERVAL WARNED TOOL_CALL_COUNT SESSION_BLOAT_WARNED MILESTONES_HIT < "$STATE" 2>/dev/null || { LAST_CHECK=0; INTERVAL=120; WARNED=0; TOOL_CALL_COUNT=0; SESSION_BLOAT_WARNED=0; MILESTONES_HIT=0; }
fi
TOOL_CALL_COUNT=$((TOOL_CALL_COUNT + 1))

# Bash 정보 추출
CMD=""; EXIT_CODE=""
if [ "$TOOL_NAME" = "Bash" ]; then
  CMD=$(echo "$STDIN" | grep -o '"command":"[^"]*"' | head -1 | sed 's/"command":"\\(.*\\)"/\\1/')
  EXIT_CODE=$(echo "$STDIN" | grep -o '"exit_code":[0-9]*' | tail -1 | grep -o '[0-9]*')
fi

# file_path 추출 (Edit/Write 도구용 — $PYTHON 파싱, grep은 중첩 JSON에서 오탐 가능)
FILE_PATH=""
if [ "$TOOL_NAME" = "Edit" ] || [ "$TOOL_NAME" = "Write" ]; then
  FILE_PATH=$(echo "$STDIN" | $PYTHON -c "
import sys, json
try:
    d = json.load(sys.stdin)
    inp = d.get('tool_input', d.get('input', {}))
    print(inp.get('file_path', '') if isinstance(inp, dict) else '')
except: print('')
" 2>/dev/null)
fi

# session_id 추출
SESSION_ID="${CLAUDE_SESSION_ID:-}"

# input_tokens 추출 (세션 JSONL)
INPUT_TOKENS=""
PROJECT_ROOT=$(dirname "$CLAUDE_MD")
PROJECT_HASH=$(echo "$PROJECT_ROOT" | sed 's|^/||; s|/|-|g')
JSONL=$(ls -t "$HOME/.claude/projects/-${PROJECT_HASH}/"*.jsonl 2>/dev/null | head -1)
if [ -n "$JSONL" ]; then
  INPUT_TOKENS=$(tail -50 "$JSONL" | grep -o '"input_tokens":[0-9]*' | tail -1 | grep -o '[0-9]*')
fi

# 서버에 평가 요청 — 환경변수로 안전하게 전달, $PYTHON가 JSON 생성 + HTTP + 응답 처리
export _MCP_PROJECT="$PROJECT_ID" _MCP_TOOL="$TOOL_NAME" _MCP_CMD="$CMD"
export _MCP_EXIT="$EXIT_CODE" _MCP_TOKENS="$INPUT_TOKENS"
export _MCP_FILE_PATH="$FILE_PATH" _MCP_SESSION_ID="$SESSION_ID"
export _MCP_LAST_CHECK="$LAST_CHECK" _MCP_INTERVAL="${INTERVAL:-120}" _MCP_WARNED="${WARNED:-0}"
export _MCP_BASE="$MCP_BASE" _MCP_STATE="$STATE"
export _MCP_TOOL_COUNT="${TOOL_CALL_COUNT:-0}" _MCP_BLOAT_WARNED="${SESSION_BLOAT_WARNED:-0}" _MCP_MILESTONES="${MILESTONES_HIT:-0}"
$PYTHON << 'PYEOF'
import json, sys, os, urllib.request

project = os.environ.get("_MCP_PROJECT", "")
tool = os.environ.get("_MCP_TOOL", "")
cmd = os.environ.get("_MCP_CMD", "")
ec = os.environ.get("_MCP_EXIT", "")
it = os.environ.get("_MCP_TOKENS", "")
lc = os.environ.get("_MCP_LAST_CHECK", "0")
iv = os.environ.get("_MCP_INTERVAL", "120")
wd = os.environ.get("_MCP_WARNED", "0")
base = os.environ.get("_MCP_BASE", "")
state = os.environ.get("_MCP_STATE", "")
tc = os.environ.get("_MCP_TOOL_COUNT", "0")
bw = os.environ.get("_MCP_BLOAT_WARNED", "0")
mh = os.environ.get("_MCP_MILESTONES", "0")

if not project or not base:
    sys.exit(0)

d = {"project": project, "tool_name": tool}
if cmd:
    d["command"] = cmd[:500]
if ec:
    try: d["exit_code"] = int(ec)
    except Exception: pass
if it:
    try: d["input_tokens"] = int(it)
    except Exception: pass
try: d["last_check_ts"] = float(lc)
except Exception: d["last_check_ts"] = 0
try: d["interval"] = int(iv)
except Exception: d["interval"] = 120
d["warned"] = wd == "1"
try: d["tool_call_count"] = int(tc)
except Exception: d["tool_call_count"] = 0
d["session_bloat_warned"] = bw == "1"
try: d["milestones_hit"] = int(mh)
except Exception: d["milestones_hit"] = 0
fp = os.environ.get("_MCP_FILE_PATH", "")
if fp:
    d["file_path"] = fp
sid = os.environ.get("_MCP_SESSION_ID", "")
if sid:
    d["session_id"] = sid

payload = json.dumps(d).encode()
req = urllib.request.Request(
    f"{base}/api/hook-eval/context-monitor",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=3)
    r = json.loads(resp.read())
except Exception:
    sys.exit(0)

# 출력
ow = r.get("ops_warn_output", "")
if ow: print(ow)
out = r.get("output", "")
if out: print(out)
cw = r.get("context_warn_output", "")
if cw: print(cw)

# 상태 파일 업데이트
ni = r.get("next_interval", 120)
w = 1 if r.get("warned", False) else 0
sbw = 1 if r.get("session_bloat_warned", False) else 0
mhv = r.get("milestones_hit", int(mh) if mh else 0)
now = int(r.get("now", 0))
tc_val = int(tc) if tc else 0
if now and state:
    with open(state, "w") as f:
        f.write(f"{now} {ni} {w} {tc_val} {sbw} {mhv}")
PYEOF
exit 0
""")

HOOK_PRE_TOOL = textwrap.dedent("""\
#!/bin/bash
# Kandela: PreToolUse hook — 위험 명령 전에 gotcha 주입 (exit 2 차단)
#
# 동작:
#   1. 위험 명령 + 관련 gotcha 있음 → exit 2 (차단, 경고 출력)
#   2. 같은 명령 재시도 → exit 0 (허용, 무한루프 방지)
#   3. 위험하지 않거나 gotcha 없음 → exit 0 (통과)

STDIN=$(cat 2>/dev/null)

# Bash 도구만 처리
TOOL_NAME=$(echo "$STDIN" | $PYTHON -c "
import sys, json
try: print(json.load(sys.stdin).get('tool_name',''))
except: print('')
" 2>/dev/null)
[ "$TOOL_NAME" != "Bash" ] && exit 0

# 명령어 추출
CMD=$(echo "$STDIN" | $PYTHON -c "
import sys, json
try:
    d = json.load(sys.stdin)
    inp = d.get('tool_input', d.get('input', {}))
    print(inp.get('command', '') if isinstance(inp, dict) else '')
except: print('')
" 2>/dev/null)
[ -z "$CMD" ] && exit 0

# CLAUDE.md 찾기: CWD → 상위 5단계
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done
[ -z "$CLAUDE_MD" ] && exit 0

PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
[ -z "$PROJECT_ID" ] && exit 0

MCP_HEALTH_URL="__MCP_HEALTH_URL_PLACEHOLDER__"
[ -z "$MCP_HEALTH_URL" ] || [ "$MCP_HEALTH_URL" = "__MCP_HEALTH_URL""_PLACEHOLDER__" ] && exit 0
MCP_BASE="${MCP_HEALTH_URL%/api/health}"

# ── 재시도 감지 (무한루프 방지) ──────────────────────────────────
SIG=$(echo "${PROJECT_ID}:${CMD:0:120}" | $PYTHON -c "import sys,hashlib; print(hashlib.md5(sys.stdin.read().encode()).hexdigest()[:16])" 2>/dev/null)
FLAG="/tmp/.memory-pretool-${SIG}"
if [ -f "$FLAG" ]; then
  rm -f "$FLAG"
  exit 0
fi

# ── 서버에 평가 요청 ─────────────────────────────────────────────
export _MCP_PROJECT="$PROJECT_ID" _MCP_BASE="$MCP_BASE" _MCP_CMD="$CMD" _MCP_FLAG="$FLAG"
$PYTHON << 'PYEOF'
import json, sys, os, urllib.request

project = os.environ.get("_MCP_PROJECT", "")
base    = os.environ.get("_MCP_BASE", "")
cmd     = os.environ.get("_MCP_CMD", "")
flag    = os.environ.get("_MCP_FLAG", "")

if not project or not base or not cmd:
    sys.exit(0)

payload = json.dumps({"project": project, "command": cmd[:500]}).encode()
req = urllib.request.Request(
    f"{base}/api/hook-eval/pre-tool",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=3)
    r = json.loads(resp.read())
except Exception:
    sys.exit(0)

output = r.get("output", "")
block  = r.get("block", False)

if not output:
    sys.exit(0)

if block and flag:
    open(flag, "w").close()
    print(output, file=sys.stderr)
    sys.exit(2)

print(output)
sys.exit(0)
PYEOF
exit 0
""")

def _build_hook_post_compact() -> str:
    _i18n = _hook_i18n_block([
        "hook_postcompact_header",
        "hook_postcompact_server_down",
    ])
    return textwrap.dedent("""\
#!/bin/bash
# Kandela: Post-Compact hook — 서버에서 프롬프트를 동적으로 가져옴
# 서버 불가 시 내장 fallback 사용.

""") + _i18n + textwrap.dedent("""\
# CLAUDE.md 찾기: CWD → 상위 5단계
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done

if [ -n "$CLAUDE_MD" ]; then
  PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
  if [ -n "$PROJECT_ID" ]; then
    # 서버에서 동적 프롬프트 가져오기 (실패 시 fallback)
    MCP_HEALTH_URL="__MCP_HEALTH_URL_PLACEHOLDER__"
    PROMPT_TEXT=""
    if [ -n "$MCP_HEALTH_URL" ] && [ "$MCP_HEALTH_URL" != "__MCP_HEALTH_URL""_PLACEHOLDER__" ]; then
      MCP_BASE="${MCP_HEALTH_URL%/api/health}"
      PROMPT_TEXT=$(curl -s --max-time 3 "$MCP_BASE/api/hook-prompt/post-compact?project=$PROJECT_ID" 2>/dev/null)
      if echo "$PROMPT_TEXT" | grep -q "^Unknown hook\\|^project param" 2>/dev/null; then
        PROMPT_TEXT=""
      fi
    fi
    if [ -n "$PROMPT_TEXT" ]; then
      echo "$PROMPT_TEXT"
    else
      echo "$(_t hook_postcompact_header | sed "s/{project}/$PROJECT_ID/g")"
      echo "$(_t hook_postcompact_server_down | sed "s/{project}/$PROJECT_ID/g")"
    fi
  fi
fi
exit 0
""")


HOOK_POST_COMPACT = _build_hook_post_compact()

HOOK_AUTO_SAVE = textwrap.dedent("""\
#!/bin/bash
# Kandela: Stop hook — Claude 응답을 로컬 JSONL 캐시에 저장
# 중요 응답(결정/gotcha/인프라) 감지 시 즉시 서버 동기화 (importance 7.0+)
# 일반 응답은 로컬 캐시 → 다음 세션 /kd-sync로 동기화 (importance 2.0)
# 의존성: $PYTHON, grep, sed, date, curl (즉시 동기화용)

set -euo pipefail

# stdin에서 hook 입력 JSON 읽기
INPUT=$(cat)

# $PYTHON로 JSON 파싱
PARSED=$(echo "$INPUT" | $PYTHON -c "
import sys, json
try:
    d = json.load(sys.stdin)
    stop = str(d.get('stop_hook_active', False)).lower()
    msg = d.get('last_assistant_message', '')
    print(stop)
    print(msg)
except Exception:
    print('false')
    print('')
" 2>/dev/null)

# 첫 줄: stop_hook_active, 나머지: last_assistant_message
STOP_ACTIVE=$(echo "$PARSED" | head -1)
LAST_MSG=$(echo "$PARSED" | tail -n +2)

# 무한루프 방지: stop_hook_active=true이면 즉시 종료
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

MSG_LEN=${#LAST_MSG}

# 너무 짧은 응답은 스킵 (인사, "네", "확인" 등)
if [ "$MSG_LEN" -lt 30 ]; then
  exit 0
fi

# CLAUDE.md 찾기: CWD → 상위 5단계
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done
if [ -z "$CLAUDE_MD" ]; then
  exit 0
fi
PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
if [ -z "$PROJECT_ID" ]; then
  exit 0
fi

# ── 중요도 판별 (키워드 패턴 매칭) ──
# 중요 응답: 결정/gotcha/인프라 정보 → importance 7.0+, rate limit 10초
# 일반 응답: importance 2.0, rate limit 30초
IMP_HINT="2.0"
RATE_LIMIT=30

# 중요도 판별 ($PYTHON — 한/영 키워드)
IMP_HINT=$(echo "$LAST_MSG" | $PYTHON -c "
import sys, re
text = sys.stdin.read().lower()
score = 2.0

# 결정 키워드 (decision)
decision_kr = ['결정', '확정', '선택했', '변경했', '전환했', '채택', '합의']
decision_en = ['decided', 'chosen', 'switched to', 'agreed on', 'finalized']
if any(k in text for k in decision_kr + decision_en):
    score = max(score, 7.0)

# Gotcha 키워드 (warnings, must-not)
gotcha_kr = ['주의:', '금지', '절대 하지', '반드시', '필수:', 'gotcha', '삽질']
gotcha_en = ['warning:', 'never ', 'must not', 'do not ', 'critical:', 'gotcha']
if any(k in text for k in gotcha_kr + gotcha_en):
    score = max(score, 8.0)

# 인프라/환경 키워드
infra = ['api key', 'api_key', 'password', 'token', '.env', '환경변수',
         '포트', 'port ', 'ssh ', 'docker ', '서버 주소', 'url:',
         '계정', 'account', 'credential']
if any(k in text for k in infra):
    score = max(score, 7.0)

# 발견/해결 키워드 (root cause)
discovery_kr = ['원인은', '발견:', '해결:', '알고보니', '근본 원인']
discovery_en = ['root cause', 'found that', 'fixed by', 'turns out', 'resolved:']
if any(k in text for k in discovery_kr + discovery_en):
    score = max(score, 7.5)

# 외부 정보 (전화번호, 이메일, URL 등)
if re.search(r'\\d{2,4}-\\d{3,4}-\\d{4}', text):  # 전화번호
    score = max(score, 7.0)
if re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}', text):  # 이메일
    score = max(score, 7.0)

# 법적/규제 키워드
legal = ['신고', '등록 필요', '허가', '인증서', '개인정보', 'gdpr', '약관']
if any(k in text for k in legal):
    score = max(score, 7.5)

print(f'{score:.1f}')
" 2>/dev/null || echo "2.0")

# 중요 응답이면 rate limit 단축
if $PYTHON -c "exit(0 if float('$IMP_HINT') >= 7.0 else 1)" 2>/dev/null; then
  RATE_LIMIT=10
fi

# Rate limit: 중요=10초, 일반=30초
MARKER="/tmp/.claude-memory-save-${PROJECT_ID}"
NOW=$(date +%s)
if [ -f "$MARKER" ]; then
  LAST=$(cat "$MARKER" 2>/dev/null || echo 0)
  if [ $((NOW - LAST)) -lt $RATE_LIMIT ]; then
    exit 0
  fi
fi
echo "$NOW" > "$MARKER"

# 응답이 너무 길면 앞부분 3000자만 사용
if [ "$MSG_LEN" -gt 3000 ]; then
  LAST_MSG="${LAST_MSG:0:3000}... [truncated]"
fi

# ── 로컬 JSONL 캐시에 저장 ──
CACHE_DIR="$HOME/.claude/kandela-cache/$PROJECT_ID"
mkdir -p "$CACHE_DIR"

# 세션 ID: 환경변수 또는 fallback
SESSION_ID="${CLAUDE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
  # fallback: PID 기반 세션 ID (같은 Claude 프로세스 내에서 동일)
  SESSION_ID="local_$(date +%Y%m%d)_$$"
fi

JSONL_FILE="$CACHE_DIR/${SESSION_ID}.jsonl"
META_FILE="$CACHE_DIR/${SESSION_ID}.meta.json"
TS=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S)

# JSONL에 이벤트 append (importance_hint 포함)
echo "$LAST_MSG" | $PYTHON -c "
import sys, json
content = sys.stdin.read()
event = {
    'ts': '$TS',
    'type': 'assistant_response',
    'content': content.strip(),
    'len': $MSG_LEN,
    'importance_hint': float('$IMP_HINT')
}
print(json.dumps(event, ensure_ascii=False))
" >> "$JSONL_FILE" 2>/dev/null

# meta.json 업데이트
if [ -f "$META_FILE" ]; then
  $PYTHON -c "
import json
with open('$META_FILE') as f:
    meta = json.load(f)
meta['last_event_at'] = '$TS'
meta['event_count'] = meta.get('event_count', 0) + 1
with open('$META_FILE', 'w') as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
" 2>/dev/null
else
  $PYTHON -c "
import json
meta = {
    'session_id': '$SESSION_ID',
    'project_id': '$PROJECT_ID',
    'hostname': '$(hostname)',
    'started_at': '$TS',
    'last_event_at': '$TS',
    'event_count': 1,
    'synced': False
}
with open('$META_FILE', 'w') as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
" 2>/dev/null
fi

# ── 중요 응답은 즉시 서버 동기화 (best-effort, non-blocking) ──
if $PYTHON -c "exit(0 if float('$IMP_HINT') >= 7.0 else 1)" 2>/dev/null; then
  MCP_HEALTH_URL=$(grep -m1 'health.*http' "$CLAUDE_MD" 2>/dev/null | grep -oE 'https?://[^ )"]+' | head -1 || echo '')
  [ -z "$MCP_HEALTH_URL" ] && MCP_HEALTH_URL=$($PYTHON -c "
import json, os
try:
    cj = os.path.expanduser('~/.claude.json')
    with open(cj) as f:
        c = json.load(f)
    url = c.get('mcpServers',{}).get('kandela',{}).get('url','')
    if url: print(url.rsplit('/mcp',1)[0] if '/mcp' in url else url)
except: pass
" 2>/dev/null || echo '')
  if [ -n "$MCP_HEALTH_URL" ]; then
    MCP_BASE="${MCP_HEALTH_URL%/api/health}"
    [ "$MCP_BASE" = "$MCP_HEALTH_URL" ] && MCP_BASE="$MCP_HEALTH_URL"
    API_KEY_FILE="$HOME/.claude/hooks/.kandela-api-key"
    AUTH_HDR=""
    [ -f "$API_KEY_FILE" ] && AUTH_HDR="Authorization: Bearer $(cat "$API_KEY_FILE")"
    # Non-blocking: 백그라운드로 전송, 실패해도 로컬 캐시에 이미 저장됨
    (curl -sf --max-time 3 -X POST "$MCP_BASE/api/cache-ingest" \
      -H "Content-Type: application/json" \
      ${AUTH_HDR:+-H "$AUTH_HDR"} \
      -d "$($PYTHON -c "
import json
print(json.dumps({
    'project': '$PROJECT_ID',
    'entries': [{'content': '''$(echo "$LAST_MSG" | head -c 2000 | sed "s/'/\\\\'/g")''', 'ts': '$TS', 'importance_hint': float('$IMP_HINT')}]
}))" 2>/dev/null)" > /dev/null 2>&1 &) || true
  fi
fi

exit 0
""")

HOOK_STOP_FAILURE = textwrap.dedent("""\
#!/bin/bash
# Kandela: StopFailure hook — API 에러로 턴 종료 시 에러 정보를 로컬 캐시에 기록
# 요구 버전: Claude Code >= 2.1.78 (StopFailure 이벤트 지원)
# 의존성: $PYTHON, grep, sed, date

set -euo pipefail

# ── 런타임 버전 체크 (이중 방어) ──
_CLAUDE_VER=$(claude --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+' | head -1 || echo "0.0.0")
_SUPPORTED=$($PYTHON -c "
v = '$_CLAUDE_VER'.split('.')
req = [2, 1, 78]
try:
    parts = [int(x) for x in v]
    while len(parts) < 3:
        parts.append(0)
    print('yes' if parts >= req else 'no')
except:
    print('no')
" 2>/dev/null || echo "no")
if [ "$_SUPPORTED" != "yes" ]; then
    exit 0
fi

# stdin에서 hook 입력 JSON 읽기
INPUT=$(cat)

# JSON 파싱 (stop_hook_active, reason, error)
PARSED=$(echo "$INPUT" | $PYTHON -c "
import sys, json
try:
    d = json.load(sys.stdin)
    stop = str(d.get('stop_hook_active', False)).lower()
    reason = d.get('reason', '')
    error = d.get('error', '')
    print(stop)
    print(reason)
    print(error)
except Exception:
    print('false')
    print('')
    print('')
" 2>/dev/null)

STOP_ACTIVE=$(echo "$PARSED" | sed -n '1p')
FAILURE_REASON=$(echo "$PARSED" | sed -n '2p')
FAILURE_ERROR=$(echo "$PARSED" | sed -n '3p')

# 무한루프 방지
if [ "$STOP_ACTIVE" = "true" ]; then
    exit 0
fi

# CLAUDE.md 찾기: CWD → 상위 5단계
CLAUDE_MD=""
_D="$PWD"
for _i in 1 2 3 4 5; do
    if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
    _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done
if [ -z "$CLAUDE_MD" ]; then
    exit 0
fi

PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
if [ -z "$PROJECT_ID" ]; then
    exit 0
fi

# Rate limit: 같은 프로젝트에서 30초에 1회만 저장
MARKER="/tmp/.claude-stop-failure-${PROJECT_ID}"
NOW=$(date +%s)
if [ -f "$MARKER" ]; then
    LAST=$(cat "$MARKER" 2>/dev/null || echo 0)
    if [ $((NOW - LAST)) -lt 30 ]; then
        exit 0
    fi
fi
echo "$NOW" > "$MARKER"

# ── 로컬 JSONL 캐시에 저장 ──
CACHE_DIR="$HOME/.claude/kandela-cache/$PROJECT_ID"
mkdir -p "$CACHE_DIR"

SESSION_ID="${CLAUDE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
    SESSION_ID="local_$(date +%Y%m%d)_$$"
fi

JSONL_FILE="$CACHE_DIR/${SESSION_ID}.jsonl"
META_FILE="$CACHE_DIR/${SESSION_ID}.meta.json"
TS=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S)

# JSONL에 stop_failure 이벤트 append
$PYTHON -c "
import json
event = {
    'ts': '$TS',
    'type': 'stop_failure',
    'reason': '''$FAILURE_REASON''',
    'error': '''$FAILURE_ERROR''',
}
print(json.dumps(event, ensure_ascii=False))
" >> "$JSONL_FILE" 2>/dev/null

# meta.json 업데이트
if [ -f "$META_FILE" ]; then
    $PYTHON -c "
import json
with open('$META_FILE') as f:
    meta = json.load(f)
meta['last_event_at'] = '$TS'
meta['event_count'] = meta.get('event_count', 0) + 1
with open('$META_FILE', 'w') as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
" 2>/dev/null
else
    $PYTHON -c "
import json
meta = {
    'session_id': '$SESSION_ID',
    'project_id': '$PROJECT_ID',
    'hostname': '$(hostname)',
    'started_at': '$TS',
    'last_event_at': '$TS',
    'event_count': 1,
    'synced': False
}
with open('$META_FILE', 'w') as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
" 2>/dev/null
fi

exit 0
""")

HOOK_PROMPT_GUARD = textwrap.dedent("""\
#!/bin/bash
# Kandela: UserPromptSubmit hook — 변경 의도 감지 시 관련 결정 자동 주입
#
# 동작:
#   1. 사용자 프롬프트에서 변경 키워드 감지 (switch to, replace, 변경, 교체 등)
#   2. 서버에 프롬프트 전송 → 관련 기억(결정/gotcha) 검색
#   3. 관련 기억 발견 시 시스템 컨텍스트로 주입
#   4. 관련 기억 없으면 무시 (exit 0)

STDIN=$(cat 2>/dev/null)

# 프롬프트 텍스트 + cwd 추출
eval $(echo "$STDIN" | $PYTHON -c "
import sys, json, shlex
try:
    d = json.load(sys.stdin)
    print(f'PROMPT={shlex.quote(d.get(\"prompt\", \"\"))}')
    print(f'HOOK_CWD={shlex.quote(d.get(\"cwd\", \"\"))}')
    print(f'SESSION_ID={shlex.quote(d.get(\"session_id\", \"\"))}')
except:
    print('PROMPT=\"\"'); print('HOOK_CWD=\"\"'); print('SESSION_ID=\"\"')
" 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

# 빠른 키워드 사전 검사 (광범위 — 서버가 정밀 분석)
if ! echo "$PROMPT" | grep -qiE "switch|change|replace|migrat|lower|reduce|increase|remove|delete|drop|disable|deprecat|upgrade|downgrade|revert|rollback|swap|rid of|stop using|instead|simpler|too complex|overcomplicat|overkill|excessive|wasteful|rather than|prefer|better to|not need|not necessary|do we need|why not|just use|should we|let.s use|refactor|rework|rewrite|add redis|add cache|add cach|alongside|normalize|extend|shorten|바꾸|변경|교체|전환|줄이|늘리|삭제|제거|비활성|폐기|업그레이드|다운그레이드|롤백|되돌|대신|그만|scp |docker exec|docker cp|docker compose|docker run|docker build|ssh |deploy|배포|컨테이너|마운트|mount|symlink|심볼릭"; then
    exit 0
fi

# Guard pause check
PAUSE_FILE="$HOME/.claude/hooks/.kandela-guard-paused-until"
if [ -f "$PAUSE_FILE" ]; then
    PAUSE_UNTIL=$(cat "$PAUSE_FILE" 2>/dev/null)
    NOW=$($PYTHON -c "import time; print(int(time.time()))")
    if [ -n "$PAUSE_UNTIL" ] && $PYTHON -c "import sys; sys.exit(0 if int('$NOW') < int('$PAUSE_UNTIL') else 1)" 2>/dev/null; then
        exit 0  # Guard is paused
    else
        rm -f "$PAUSE_FILE"  # Expired, clean up
    fi
fi

# CLAUDE.md 찾기: hook input cwd → 상위 5단계 (fallback: $PWD)
CLAUDE_MD=""
_D="${HOOK_CWD:-$PWD}"
for _i in 1 2 3 4 5; do
  if [ -f "$_D/CLAUDE.md" ]; then CLAUDE_MD="$_D/CLAUDE.md"; break; fi
  _P=$(dirname "$_D"); [ "$_P" = "$_D" ] && break; _D="$_P"
done
[ -z "$CLAUDE_MD" ] && exit 0

PROJECT_ID=$(grep -m1 'memory project ID:' "$CLAUDE_MD" 2>/dev/null | sed 's/.*memory project ID: *\\([a-zA-Z0-9_-]*\\).*/\\1/' || echo '')
[ -z "$PROJECT_ID" ] && exit 0

MCP_HEALTH_URL="__MCP_HEALTH_URL_PLACEHOLDER__"
[ -z "$MCP_HEALTH_URL" ] || [ "$MCP_HEALTH_URL" = "__MCP_HEALTH_URL""_PLACEHOLDER__" ] && exit 0
MCP_BASE="${MCP_HEALTH_URL%/api/health}"

# API 키 로드 (Bearer 인증)
API_KEY=""
API_KEY_FILE="$HOME/.claude/hooks/.kandela-api-key"
[ -f "$API_KEY_FILE" ] && API_KEY=$(cat "$API_KEY_FILE")

# Guard level: strong (강) / medium (중, 기본) / weak (약) / explore (탐색 — 차단 없이 알림만)
GUARD_LEVEL="medium"
[ -f "$HOME/.claude/hooks/.kandela-guard-level" ] && GUARD_LEVEL=$(cat "$HOME/.claude/hooks/.kandela-guard-level")

# Guard tone: friendly (친근, 기본) / brief (간결) / formal (공식)
GUARD_TONE="friendly"
[ -f "$HOME/.claude/hooks/.kandela-guard-tone" ] && GUARD_TONE=$(cat "$HOME/.claude/hooks/.kandela-guard-tone")

# 서버에 평가 요청
export _PG_PROJECT="$PROJECT_ID" _PG_BASE="$MCP_BASE" _PG_PROMPT="$PROMPT" _PG_API_KEY="$API_KEY" _PG_GUARD_LEVEL="$GUARD_LEVEL" _PG_GUARD_TONE="$GUARD_TONE" _PG_SESSION="$SESSION_ID"
$PYTHON << 'PYEOF'
import json, sys, os, urllib.request

project     = os.environ.get("_PG_PROJECT", "")
base        = os.environ.get("_PG_BASE", "")
prompt      = os.environ.get("_PG_PROMPT", "")
api_key     = os.environ.get("_PG_API_KEY", "")
guard_level = os.environ.get("_PG_GUARD_LEVEL", "medium")
guard_tone  = os.environ.get("_PG_GUARD_TONE", "friendly")
session_id  = os.environ.get("_PG_SESSION", "")

if not project or not base or not prompt:
    sys.exit(0)

payload = json.dumps({
    "project": project,
    "prompt": prompt[:2000],
    "guard_level": guard_level,
    "session_id": session_id,
}).encode()
headers = {
    "Content-Type": "application/json",
    "x-guard-level": guard_level,
    "x-guard-tone": guard_tone,
}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"

req = urllib.request.Request(
    f"{base}/api/hook-eval/prompt-guard-hook",
    data=payload,
    headers=headers,
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=3)
    output = resp.read().decode("utf-8", errors="replace").strip()
except Exception:
    sys.exit(0)

# Stats tracking
import time as _time
stats_file = os.path.expanduser("~/.claude/hooks/.kandela-guard-stats.jsonl")
try:
    action = "block" if output and not output.startswith("<user-prompt-submit-hook>") else "warn"
    entry = json.dumps({"ts": int(_time.time()), "project": project, "action": action})
    with open(stats_file, "a") as _sf:
        _sf.write(entry + "\\n")
except Exception:
    pass

# stdout injection — PreToolUse gate가 blocking 담당 (v2.1.77+)
if output:
    print(output)
sys.exit(0)
PYEOF
exit 0
""")

# ── Settings JSON ──────────────────────────────────────────────────

SETTINGS_JSON = """\
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-session-start.sh",
            "timeout": 15
          }
        ]
      },
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-post-compact.sh",
            "timeout": 15
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-pre-compact.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-pre-tool.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-context-monitor.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-auto-save-check.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/kandela-prompt-guard.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
"""

# ── File manifest ──────────────────────────────────────────────────

COMMANDS: dict[str, str] = {
    "kd-init": CMD_DM_INIT,
    "kd-link": CMD_DM_LINK,
    "kd-list": CMD_DM_LIST,
    "kd-load": CMD_DM_LOAD,
    "kd-delete": CMD_DM_DELETE,
    "kd-rename": CMD_DM_RENAME,
    "kd-update": CMD_DM_UPDATE,
    "kd-help": CMD_DM_HELP,
    "kd-journal": CMD_DM_JOURNAL,
    "kd-visibility": CMD_DM_VISIBILITY,
    "kd-inbox": CMD_DM_INBOX,
    "kd-status": CMD_DM_STATUS,
    "kd-task": CMD_DM_TASK,
    "kd-workspace": CMD_DM_WORKSPACE,
    "kd-sync": CMD_DM_SYNC,
    "kd-guard": CMD_DM_GUARD,
    "kd-daily": CMD_DM_DAILY,
    "kd-activation": CMD_DM_ACTIVATION,
    "kd-progress": CMD_DM_PROGRESS,
    "kd-log-review": CMD_DM_LOG_REVIEW,
    "kd-uninstall": CMD_DM_UNINSTALL,
}

# Inject python detect block after shebang in all hooks
_PY_DETECT = _hook_python_detect_block()

def _inject_python_detect(hook_code: str) -> str:
    """Insert Python detection block after #!/bin/bash line."""
    lines = hook_code.split("\n", 1)
    if lines[0].startswith("#!/bin/bash"):
        return lines[0] + "\n" + _PY_DETECT + (lines[1] if len(lines) > 1 else "")
    return _PY_DETECT + hook_code

HOOKS: dict[str, str] = {
    "kandela-session-start.sh": _inject_python_detect(HOOK_SESSION_START),
    "kandela-pre-compact.sh": _inject_python_detect(HOOK_PRE_COMPACT),
    "kandela-post-compact.sh": _inject_python_detect(HOOK_POST_COMPACT),
    "kandela-auto-save-check.sh": _inject_python_detect(HOOK_AUTO_SAVE),
    "kandela-stop-failure.sh": _inject_python_detect(HOOK_STOP_FAILURE),
    "kandela-context-monitor.sh": _inject_python_detect(HOOK_CONTEXT_MONITOR),
    "kandela-pre-tool.sh": _inject_python_detect(HOOK_PRE_TOOL),
    "kandela-prompt-guard.sh": _inject_python_detect(HOOK_PROMPT_GUARD),

}

# ── Version-gated hooks (installed conditionally) ───────────────────
# Map of hook filename → feature name (key into FEATURE_MIN_VERSION)
VERSION_GATED_HOOKS: dict[str, str] = {
    "kandela-stop-failure.sh": "StopFailure",
}

# Server URL — used as default when generating install scripts.
# Override with MEMORY_SERVER_URL env var or ?server= query param.
DEFAULT_SERVER_URL = os.environ.get(
    "MEMORY_SERVER_URL", "https://api.kandela.ai"
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Install script generator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _escape_for_heredoc(content: str) -> str:
    """Escape content to be safely placed inside a bash heredoc.

    We use quoted heredoc (<<'ENDOFFILE') so no escaping is needed
    for most characters except the heredoc delimiter itself.
    """
    # Just make sure the content doesn't contain the delimiter
    return content


def generate_install_script(server_url: str = DEFAULT_SERVER_URL, lang: str = "en") -> str:
    """Generate a complete bash install script with all client files inlined.

    Args:
        server_url: The Kandela server URL.
        lang: 2-char language code for i18n (e.g. 'en', 'ko', 'ja').
    """
    mcp_url = f"{server_url}/mcp"

    parts: list[str] = []

    # ── Header ──
    i18n_block = shell_i18n_block()
    parts.append(textwrap.dedent(f"""\
        #!/bin/bash
        # Kandela Memory — Client Installer
        # Usage: curl -sL {server_url}/install | bash
        #   or:  curl -sL {server_url}/install | bash -s -- --api-key YOUR_KEY
        #   or:  curl -sL {server_url}/install | bash -s -- --dry-run
        #   or:  curl -sL {server_url}/install | bash -s -- --yes
        set -euo pipefail

        SERVER_URL="{server_url}"
        MCP_URL="{mcp_url}"
        DRY_RUN=false
        AUTO_YES=false
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)

        # ── Color helpers ──
        RED='\\033[0;31m'
        GREEN='\\033[0;32m'
        YELLOW='\\033[1;33m'
        CYAN='\\033[0;36m'
        NC='\\033[0m' # No Color

        info()    {{ echo -e "${{GREEN}}✓${{NC}} $1"; }}
        warn()    {{ echo -e "${{YELLOW}}⚠${{NC}} $1"; }}
        error()   {{ echo -e "${{RED}}✗${{NC}} $1" >&2; }}
        dryrun()  {{ echo -e "${{YELLOW}}[DRY RUN]${{NC}} $1"; }}
    """))

    # ── i18n block ──
    parts.append(i18n_block)
    parts.append("\n")

    # ── Environment Detection + Python Detection ──
    parts.append(textwrap.dedent("""\
        # ── Environment Detection ──
        detect_env() {
          OS_TYPE="unknown"
          IS_WSL=false
          WIN_USER=""

          case "$(uname -s)" in
            Darwin)
              OS_TYPE="macos"
              ;;
            Linux)
              OS_TYPE="linux"
              if grep -qi "microsoft\\|wsl" /proc/version 2>/dev/null; then
                IS_WSL=true
                OS_TYPE="wsl"
                WIN_USER=$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\\r\\n' || true)
                if [ -z "$WIN_USER" ]; then
                  for u in /mnt/c/Users/*/; do
                    uname=$(basename "$u")
                    case "$uname" in Public|Default|"All Users"|"Default User") continue ;; esac
                    if [ -f "$u.claude.json" ] || [ -d "$u.claude" ]; then
                      WIN_USER="$uname"
                      break
                    fi
                  done
                  if [ -z "$WIN_USER" ]; then
                    CANDIDATES=$(ls /mnt/c/Users/ 2>/dev/null | grep -vE '^(Public|Default|All Users|Default User)$')
                    CCOUNT=$(echo "$CANDIDATES" | wc -l | tr -d ' ')
                    if [ "$CCOUNT" -eq 1 ]; then
                      WIN_USER=$(echo "$CANDIDATES" | head -1)
                    elif [ "$CCOUNT" -gt 1 ]; then
                      echo "$CANDIDATES" | nl
                      read -rp "Enter Windows username: " WIN_USER </dev/tty
                    fi
                  fi
                fi
              fi
              ;;
            MINGW*|MSYS*|CYGWIN*)
              OS_TYPE="windows_git_bash"
              # Git Bash: $HOME is already Windows home (e.g., /c/Users/kdk)
              # No path conversion needed — ~/.claude/ points to correct location
              ;;
          esac

          export OS_TYPE IS_WSL WIN_USER
        }

        detect_env

        # ── Python Detection ──
        PYTHON=""
        if command -v python3 &>/dev/null; then
          PYTHON="python3"
        elif command -v python &>/dev/null; then
          PY_VER=$(python -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "0")
          if [ "$PY_VER" = "3" ]; then
            PYTHON="python"
          fi
        fi

        if [ -z "$PYTHON" ]; then
          echo "Python 3 is required. Install python3 or python." >&2
          exit 1
        fi

        # Windows: Python defaults to cp949/cp1252, force UTF-8 for all file I/O
        export PYTHONUTF8=1

        # Git Bash: Python needs Windows paths, not MSYS paths
        # Set USERPROFILE so Python os.path.expanduser returns Windows path
        if [ "$OS_TYPE" = "windows_git_bash" ]; then
          export USERPROFILE=$(cygpath -w "$HOME" 2>/dev/null || echo "$HOME")
        fi

    """))

    parts.append(textwrap.dedent(f"""\
        # ── Parse arguments ──
        API_KEY=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --api-key)
              API_KEY="$2"
              shift 2
              ;;
            --api-key=*)
              API_KEY="${{1#*=}}"
              shift
              ;;
            --dry-run)
              DRY_RUN=true
              shift
              ;;
            --yes|-y)
              AUTO_YES=true
              shift
              ;;
            *)
              shift
              ;;
          esac
        done

        echo ""
        echo -e "${{CYAN}}╔══════════════════════════════════════════╗${{NC}}"
        echo -e "${{CYAN}}║     $(_t install_banner)   ║${{NC}}"
        [ "$DRY_RUN" = "true" ] && echo -e "${{YELLOW}}║           $(_t install_dry_run_banner)           ║${{NC}}"
        echo -e "${{CYAN}}╚══════════════════════════════════════════╝${{NC}}"
        echo ""
        echo "$(_t install_adding)"
        echo "  • MCP 서버 연결 (기억 도구 14개)"
        echo "  • ~/.claude/hooks/ 훅 스크립트 $(echo "{len(HOOKS)}")개"
        echo "  • ~/.claude/skills/kd-* 슬래시 명령 $(echo "{len(COMMANDS)}")개"
        echo "  • ~/.claude/settings.json (MCP + 훅 등록)"
        echo ""
        echo "$(_t install_not_touching)"
        echo "  • 기존 MCP 서버, Hook, 권한 설정 (삭제·수정 없음)"
        echo "  • 프로젝트 파일 (CLAUDE.md 등 일체)"
        echo ""
        echo "$(_t install_uninstall_hint)"
        echo "  • curl -sL {server_url}/uninstall | bash"
        echo "  • 또는 설치 후: /kd-uninstall"
        echo ""
        if [ "$DRY_RUN" = "true" ]; then
          echo -e "${{YELLOW}}$(_t install_dry_run_intro)${{NC}}"
          echo ""
          echo "설치 시 생성될 파일:"
          echo "  ~/.claude/skills/kd-*/ (슬래시 명령 {len(COMMANDS)}개)"
          echo "  ~/.claude/hooks/kandela-*.sh (훅 {len(HOOKS)}개)"
          echo "  ~/.claude/hooks/.kandela-api-key (API 키, chmod 600)"
          echo "  ~/.claude/hooks/.kandela-install-version"
          echo "  ~/.claude/settings.json (수정 — hooks 항목 추가)"
          echo "  ~/.claude.json (수정 — mcpServers.kandela 추가)"
          echo ""
          echo "백업 생성 위치:"
          echo "  ~/.claude/settings.json.kandela-backup-YYYYMMDD_HHMMSS"
          echo "  ~/.claude.json.kandela-backup-YYYYMMDD_HHMMSS"
          echo ""
          echo "$(_t install_dry_run_to_install)"
          echo "  curl -sL {server_url}/install | bash"
          exit 0
        else
          if [ "$AUTO_YES" = "true" ]; then
            echo ""
          elif [ -t 0 ]; then
            read -r -p "$(_t install_confirm)" REPLY </dev/tty || REPLY=""
            if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
              echo "$(_t install_cancelled)"
              exit 0
            fi
          fi
          echo ""
        fi

        # ── Claude Code 버전 감지 ──
        CLAUDE_VERSION=$(claude --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+' | head -1 || echo "0.0.0")
        _check_min_version() {{
            local feature="$1" min="$2" cur="$3"
            $PYTHON -c "
v = '$cur'.split('.')
req = [int(x) for x in '$min'.split('.')]
try:
    parts = [int(x) for x in v]
    while len(parts) < 3:
        parts.append(0)
    print('yes' if parts >= req else 'no')
except:
    print('no')
" 2>/dev/null || echo "no"
        }}

        # ── Check prerequisites ──
        if ! command -v claude &>/dev/null; then
          error "$(_t install_claude_not_found) https://docs.anthropic.com/en/docs/claude-code"
          exit 1
        fi

        # ── Check Claude Code version (HTTP hooks require >= 2.1.77) ──
        CLAUDE_VERSION=$(claude --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+' | head -1 || true)
        MIN_VERSION="2.1.77"
        version_lt() {{
          # Returns 0 (true) if $1 < $2
          $PYTHON -c "import sys; a=list(map(int,'$1'.split('.'))); b=list(map(int,'$2'.split('.'))); sys.exit(0 if a < b else 1)"
        }}
        if [ -n "$CLAUDE_VERSION" ] && version_lt "$CLAUDE_VERSION" "$MIN_VERSION"; then
          warn "$(_t install_version_warning | sed "s/{{version}}/$CLAUDE_VERSION/g;s/{{min}}/$MIN_VERSION/g")"
          warn "PreToolUse HTTP hook (Prompt Guard gate) requires Claude Code >= $MIN_VERSION"
          warn "To upgrade: npm update -g @anthropic-ai/claude-code"
          echo ""
          # curl | bash 방식이면 stdin이 스크립트라 read 불가 → 자동 계속
          if [ -t 0 ]; then
            read -r -p "$(_t install_continue_anyway)" REPLY </dev/tty
            if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
              echo "$(_t install_cancelled)"
              exit 1
            fi
          else
            warn "$(_t install_noninteractive_continue)"
          fi
        else
          info "Claude Code $CLAUDE_VERSION"
        fi

        # ── Server connectivity check ──
        echo -n "$(_t install_checking_server) "
        if curl -s --max-time 5 "$SERVER_URL/api/health" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
          echo -e "${{GREEN}}OK${{NC}}"
        else
          error "$(_t install_server_unreachable | sed "s|{{url}}|$SERVER_URL|g")"
          exit 1
        fi

        # ── Create directories (environment-aware) ──
        if [ "$IS_WSL" = true ] && [ -n "$WIN_USER" ]; then
          if [ ! -d "/mnt/c/Users/$WIN_USER" ]; then
            error "/mnt/c/Users/$WIN_USER not found. Check Windows username."
            exit 1
          fi
          CLAUDE_DIR="/mnt/c/Users/$WIN_USER/.claude"
          CLAUDE_JSON="/mnt/c/Users/$WIN_USER/.claude.json"
          info "WSL detected — installing to $CLAUDE_DIR"
        elif [ "$OS_TYPE" = "windows_git_bash" ]; then
          # Git Bash: bash uses MSYS paths, Python needs Windows paths
          CLAUDE_DIR="$HOME/.claude"
          CLAUDE_JSON="$HOME/.claude.json"
          info "Git Bash detected — installing to $CLAUDE_DIR"
        else
          CLAUDE_DIR="$HOME/.claude"
          CLAUDE_JSON="$HOME/.claude.json"
        fi
        COMMANDS_DIR="$CLAUDE_DIR/skills"
        HOOKS_DIR="$CLAUDE_DIR/hooks"

        # For Python heredocs: convert MSYS paths to Windows paths (using / not \ to avoid Python unicode escapes)
        if command -v cygpath &>/dev/null; then
          CLAUDE_DIR_PY=$(cygpath -m "$CLAUDE_DIR")
          CLAUDE_JSON_PY=$(cygpath -m "$CLAUDE_JSON")
        else
          CLAUDE_DIR_PY="$CLAUDE_DIR"
          CLAUDE_JSON_PY="$CLAUDE_JSON"
        fi

        mkdir -p "$COMMANDS_DIR"
        mkdir -p "$HOOKS_DIR"

        # WSL: create symlink from WSL home to Windows path
        if [ "$IS_WSL" = true ] && [ -n "$WIN_USER" ]; then
          if [ -L "$HOME/.claude" ]; then
            : # already a symlink
          elif [ -d "$HOME/.claude" ]; then
            warn "~/.claude directory exists in WSL home (may be from a previous install)."
            warn "Windows Claude Code reads from $CLAUDE_DIR instead."
          else
            ln -s "$CLAUDE_DIR" "$HOME/.claude" 2>/dev/null || true
          fi
        fi

        info "$(_t install_dirs_ready)"
    """))

    # ── Migration from memory / memory-mcp to kandela ──
    parts.append(textwrap.dedent("""\

        # ═══ Kandela Migration (from memory / memory-mcp) ═══
        $PYTHON << 'ENDOFMIGRATION'
import os, json

hooks_dir = os.path.expanduser("~/.claude/hooks")
claude_json = os.path.expanduser("~/.claude.json")

# 1. .claude.json: "memory" or "memory-mcp" → "kandela"
if os.path.exists(claude_json):
    with open(claude_json) as f:
        config = json.load(f)
    servers = config.get("mcpServers", {})
    migrated = False
    for old_key in ("memory", "memory-mcp"):
        if old_key in servers and "kandela" not in servers:
            servers["kandela"] = servers.pop(old_key)
            migrated = True
        elif old_key in servers:
            del servers[old_key]
            migrated = True
    if migrated:
        with open(claude_json, "w") as f:
            json.dump(config, f, indent=2)

# 2. Hook files: rename old → new
_HOOK_RENAME = {
    "memory-session-start.sh": "kandela-session-start.sh",
    "memory-pre-compact.sh": "kandela-pre-compact.sh",
    "memory-post-compact.sh": "kandela-post-compact.sh",
    "memory-auto-save-check.sh": "kandela-auto-save-check.sh",
    "memory-stop-failure.sh": "kandela-stop-failure.sh",
    "memory-context-monitor.sh": "kandela-context-monitor.sh",
    "memory-pre-tool.sh": "kandela-pre-tool.sh",
    "memory-prompt-guard.sh": "kandela-prompt-guard.sh",
}
for old_name, new_name in _HOOK_RENAME.items():
    old_path = os.path.join(hooks_dir, old_name)
    new_path = os.path.join(hooks_dir, new_name)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        os.rename(old_path, new_path)

# 3. Hidden files
for old_name, new_name in [
    (".memory-api-key", ".kandela-api-key"),
    (".memory-install-version", ".kandela-install-version"),
    (".memory-guard-level", ".kandela-guard-level"),
    (".memory-guard-tone", ".kandela-guard-tone"),
    (".memory-guard-pause-until", ".kandela-guard-pause-until"),
    (".memory-guard-stats.json", ".kandela-guard-stats.json"),
]:
    old_path = os.path.join(hooks_dir, old_name)
    new_path = os.path.join(hooks_dir, new_name)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        os.rename(old_path, new_path)

# 4. Cache directory
cache_parent = os.path.expanduser("~/.claude")
old_cache = os.path.join(cache_parent, "memory-cache")
new_cache = os.path.join(cache_parent, "kandela-cache")
if os.path.isdir(old_cache) and not os.path.exists(new_cache):
    os.rename(old_cache, new_cache)
    try:
        os.symlink(new_cache, old_cache)
    except OSError:
        pass  # symlink fail (WSL etc) — ignore

# 5. settings.json: rename memory-*.sh → kandela-*.sh in hook commands
settings_file = os.path.join(cache_parent, "settings.json")
if os.path.exists(settings_file):
    with open(settings_file) as f:
        settings = json.load(f)
    changed = False
    if "hooks" in settings:
        for event, entries in settings["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "memory-" in cmd and ".sh" in cmd:
                        for old_h, new_h in _HOOK_RENAME.items():
                            if old_h in cmd:
                                hook["command"] = cmd.replace(old_h, new_h)
                                changed = True
                                break
    if "permissions" in settings and "allow" in settings["permissions"]:
        new_allow = []
        for p in settings["permissions"]["allow"]:
            if p.startswith("mcp__memory__"):
                new_allow.append(p.replace("mcp__memory__", "mcp__kandela__", 1))
                changed = True
            else:
                new_allow.append(p)
        settings["permissions"]["allow"] = new_allow
    if changed:
        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\\n")
ENDOFMIGRATION
        _MIG_EXIT=$?
        if [ $_MIG_EXIT -eq 0 ]; then
          info "Migration check completed"
        fi
    """))

    # ── Slash Command Migration (dm.* → kd-*) ──
    parts.append(textwrap.dedent("""\

        # ═══ Slash Command Migration (dm.* → kd-*) ═══
        $PYTHON << 'ENDOFSLASHMIG'
import os, shutil

_commands_dir = os.path.join(os.path.expanduser("~/.claude"), "commands")
_OLD_SLASH_COMMANDS = [
    "dm.activation", "dm.daily", "dm.delete", "dm.guard", "dm.help",
    "dm.inbox", "dm.init", "dm.journal", "dm.link", "dm.list",
    "dm.load", "dm.log-review", "dm.progress", "dm.rename",
    "dm.status", "dm.sync", "dm.task", "dm.uninstall", "dm.update",
    "dm.visibility", "dm.worker", "dm.workspace",
]
_removed = 0
for _old_cmd in _OLD_SLASH_COMMANDS:
    _old_path = os.path.join(_commands_dir, _old_cmd)
    if os.path.isdir(_old_path):
        shutil.rmtree(_old_path)
        _removed += 1
# Also remove old .md files (non-directory commands)
for _old_md in ["dm.journal.md", "dm.monitor.md", "dm.visibility.md"]:
    _old_path = os.path.join(_commands_dir, _old_md)
    if os.path.isfile(_old_path):
        os.remove(_old_path)
        _removed += 1
if _removed > 0:
    print(f"Removed {_removed} old dm.* slash commands")

# Also migrate kd-* from commands/ to skills/ (v30 path change)
_skills_dir = os.path.join(os.path.expanduser("~/.claude"), "skills")
for _entry in os.listdir(_commands_dir) if os.path.isdir(_commands_dir) else []:
    if _entry.startswith("kd-") and os.path.isdir(os.path.join(_commands_dir, _entry)):
        _old = os.path.join(_commands_dir, _entry)
        shutil.rmtree(_old)
        _removed += 1
if _removed > 0:
    print(f"Cleaned up commands/ directory")
ENDOFSLASHMIG
        _SLASH_MIG_EXIT=$?
        if [ $_SLASH_MIG_EXIT -eq 0 ]; then
          info "Slash command migration check completed (dm.* → kd-*)"
        fi
    """))

    # ── Write commands (Phase 3: translate descriptions based on lang) ──
    parts.append(f"\n# ═══ Slash Commands ({len(COMMANDS)}) ═══\n")
    for cmd_name, cmd_content in COMMANDS.items():
        # Translate the description field in SKILL.md for the target language.
        # cmd_name is e.g. "kd-init" → key suffix is "init"
        cmd_suffix = cmd_name.replace("kd-", "", 1).replace("-", "_")
        i18n_key = f"cmd_{cmd_suffix}_desc"
        translated_desc = t(i18n_key, lang)
        if translated_desc and translated_desc != i18n_key:
            translated_desc_escaped = translated_desc.replace('"', '\\"')
            cmd_content = re.sub(
                r'^description:.*$',
                f'description: "{translated_desc_escaped}"',
                cmd_content,
                flags=re.MULTILINE,
                count=1,
            )
        safe_content = _escape_for_heredoc(cmd_content)
        parts.append(f'mkdir -p "$COMMANDS_DIR/{cmd_name}"\n')
        parts.append(f"cat > \"$COMMANDS_DIR/{cmd_name}/SKILL.md\" <<'ENDOFCMD'\n")
        parts.append(safe_content)
        if not safe_content.endswith("\n"):
            parts.append("\n")
        parts.append("ENDOFCMD\n\n")
    cmd_count = len(COMMANDS)
    parts.append(f'info "$(_t install_commands_done | sed "s/{{n}}/{cmd_count}/g")"\n')

    # ── Legacy cleanup (pm-* and old dm* without dot) ──
    parts.append(textwrap.dedent("""\

        # ═══ Legacy Command Cleanup ═══
        LEGACY_CMDS="pm-init pm-update pm-list pm-load pm-delete pm-rename pm-help dminit dmlink dmlist dmload dmdelete dmrename dmupdate dmhelp dminbox dmstatus dmtask dmworker"
        LEGACY_COUNT=0
        for cmd in $LEGACY_CMDS; do
          if [ -d "$COMMANDS_DIR/$cmd" ]; then
            rm -rf "$COMMANDS_DIR/$cmd"
            LEGACY_COUNT=$((LEGACY_COUNT + 1))
          fi
        done
        if [ "$LEGACY_COUNT" -gt 0 ]; then
          info "Cleaned up $LEGACY_COUNT legacy commands (pm-* / dm* → kd-*)"
        fi
    """))

    # ── Write hooks (all scripts, version-gated ones have runtime checks) ──
    hook_names = list(HOOKS.keys())
    base_hook_names = [h for h in hook_names if h not in VERSION_GATED_HOOKS]
    gated_hook_names = [h for h in hook_names if h in VERSION_GATED_HOOKS]

    parts.append(f"\n# ═══ Hooks ({len(hook_names)}) ═══\n")
    for hook_name, hook_content in HOOKS.items():
        safe_content = _escape_for_heredoc(hook_content)
        parts.append(f"cat > \"$HOOKS_DIR/{hook_name}\" <<'ENDOFHOOK'\n")
        parts.append(safe_content)
        if not safe_content.endswith("\n"):
            parts.append("\n")
        parts.append("ENDOFHOOK\n")
        parts.append(f'chmod +x "$HOOKS_DIR/{hook_name}"\n\n')

    base_hook_count = len(base_hook_names)
    parts.append(f'info "$(_t install_hooks_done | sed "s/{{n}}/{base_hook_count}/g")"\n')

    # ── Write install version marker ──
    parts.append(f"""
# ═══ Install Version Marker ═══
echo "{INSTALL_VERSION}" > "$HOOKS_DIR/.kandela-install-version"
info "Install version marker written (v{INSTALL_VERSION})"
""")


    # ── Write settings.json (merge with existing) ──
    # Write new settings content to a temp file, then merge via $PYTHON
    parts.append("\n# ═══ Settings JSON ═══\n")
    parts.append("SETTINGS_FILE=\"$CLAUDE_DIR/settings.json\"\n")
    parts.append("SETTINGS_TMP=$(mktemp)\n")
    parts.append("cat > \"$SETTINGS_TMP\" <<'ENDOFSETTINGS'\n")
    parts.append(SETTINGS_JSON)
    parts.append("ENDOFSETTINGS\n\n")
    parts.append("""\
if [ -f "$SETTINGS_FILE" ]; then
  cp "$SETTINGS_FILE" "$SETTINGS_FILE.kandela-backup-$TIMESTAMP"
  SETTINGS_FILE_ABS="$SETTINGS_FILE"
  SETTINGS_TMP_ABS="$SETTINGS_TMP"
  $PYTHON - "$SETTINGS_FILE_ABS" "$SETTINGS_TMP_ABS" <<'ENDOFPY'
import json, sys
sf = sys.argv[1]
tf = sys.argv[2]
with open(sf) as f:
    existing = json.load(f)
with open(tf) as f:
    new_hooks = json.load(f)
if "hooks" not in existing:
    existing["hooks"] = {}
changed = False
for key, val in new_hooks["hooks"].items():
    if key not in existing["hooks"]:
        existing["hooks"][key] = val
        changed = True
    else:
        # Track existing commands AND urls to avoid duplicates
        existing_cmds = {h.get("hooks", [{}])[0].get("command", "") for h in existing["hooks"][key] if h.get("hooks")}
        existing_urls = {h.get("hooks", [{}])[0].get("url", "") for h in existing["hooks"][key] if h.get("hooks")}
        for entry in val:
            hook0 = entry.get("hooks", [{}])[0]
            cmd = hook0.get("command", "")
            url = hook0.get("url", "")
            if cmd and cmd not in existing_cmds:
                existing["hooks"][key].append(entry)
                changed = True
            elif cmd and cmd in existing_cmds:
                # Command exists — update timeout if changed
                new_timeout = hook0.get("timeout")
                if new_timeout is not None:
                    for ex_entry in existing["hooks"][key]:
                        for ex_hook in ex_entry.get("hooks", []):
                            if ex_hook.get("command", "") == cmd:
                                if ex_hook.get("timeout") != new_timeout:
                                    ex_hook["timeout"] = new_timeout
                                    changed = True
            elif url and url not in existing_urls:
                existing["hooks"][key].append(entry)
                changed = True
if changed:
    with open(sf, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\\n")
    print("UPDATED")
else:
    print("UNCHANGED")
ENDOFPY
  if [ $? -eq 0 ]; then
    info "$(_t install_settings_updated)"
  else
    warn "Could not merge settings.json — writing fresh copy"
    cp "$SETTINGS_TMP" "$SETTINGS_FILE"
    info "settings.json written (fresh)"
  fi
else
  cp "$SETTINGS_TMP" "$SETTINGS_FILE"
  info "settings.json created"
fi
rm -f "$SETTINGS_TMP"
""")

    # ── Version-gated settings entries ──
    # Build version requirements string for the bash script
    gated_entries = []
    for hook_name, feature in VERSION_GATED_HOOKS.items():
        min_ver = FEATURE_MIN_VERSION.get(feature, "0.0.0")
        # StopFailure settings entry to inject
        if feature == "StopFailure":
            stop_failure_entry = (
                '{"hooks": [{"type": "command", "command": "~/.claude/hooks/kandela-stop-failure.sh", "timeout": 5}]}'
            )
            gated_entries.append((feature, min_ver, "StopFailure", stop_failure_entry))

    if gated_entries:
        parts.append("\n# ═══ Version-Gated Features ═══\n")
        parts.append(f'SETTINGS_FILE="$CLAUDE_DIR/settings.json"\n')
        parts.append("MISSING_FEATURES=\"\"\n")
        for feature, min_ver, event_key, json_entry in gated_entries:
            parts.append(textwrap.dedent(f"""\
# ── {feature} (requires Claude >= {min_ver}) ──
_SF_SUPPORTED=$(_check_min_version "{feature}" "{min_ver}" "$CLAUDE_VERSION")
if [ "$_SF_SUPPORTED" = "yes" ]; then
    $PYTHON - "$SETTINGS_FILE" <<'ENDOFSFPY'
import json, sys
sf = sys.argv[1]
try:
    with open(sf) as f:
        s = json.load(f)
    if "hooks" not in s:
        s["hooks"] = {{}}
    if "{event_key}" not in s["hooks"]:
        s["hooks"]["{event_key}"] = []
    existing_cmds = {{h.get("hooks", [{{}}])[0].get("command", "") for h in s["hooks"]["{event_key}"] if h.get("hooks")}}
    entry = {json_entry}
    cmd = entry["hooks"][0].get("command", "")
    if cmd and cmd not in existing_cmds:
        s["hooks"]["{event_key}"].append(entry)
        with open(sf, "w") as f:
            json.dump(s, f, indent=2)
            f.write("\\n")
except Exception as e:
    pass
ENDOFSFPY
    info "{feature} hook registered in settings.json (Claude $CLAUDE_VERSION >= {min_ver})"
else
    MISSING_FEATURES="$MISSING_FEATURES {feature}(>={min_ver})"
fi
"""))

        # Show upgrade recommendation if any features are missing
        parts.append(textwrap.dedent("""\
if [ -n "$MISSING_FEATURES" ]; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}⬆  $(_t install_upgrade_recommended)${NC}"
    echo -e "   현재 버전: ${CYAN}$CLAUDE_VERSION${NC}"
    echo -e "   다음 기능이 비활성화되어 있습니다:${YELLOW}$MISSING_FEATURES${NC}"
    echo -e "   ${CYAN}claude update${NC} 로 업데이트하면 자동으로 활성화됩니다."
    echo -e "   (재설치 불필요 — 훅 스크립트는 이미 설치되어 있습니다)"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
fi
"""))

    # ── Configure .claude.json (MCP server) ──
    parts.append(textwrap.dedent(f"""\

        # ═══ MCP Server Configuration ═══
        # CLAUDE_JSON already set by environment detection above

        if [ -f "$CLAUDE_JSON" ] && grep -q "kandela" "$CLAUDE_JSON" 2>/dev/null; then
          info ".claude.json already has kandela configuration (skipped)"
        else
          # Ask for API key if not provided
          if [ -z "$API_KEY" ]; then
            echo ""
            echo -e "${{YELLOW}}$(_t install_api_key_prompt)${{NC}}"
            echo -e "$(_t install_api_key_get | sed "s|{{url}}|{server_url}|g")"
            echo ""
            read -rp "$(_t install_api_key_enter)" API_KEY </dev/tty || API_KEY=""
            echo ""
          fi

          if [ -z "$API_KEY" ]; then
            warn "$(_t install_api_key_skip | sed "s|{{url}}|{server_url}|g")"
          else
            if [[ ! "$API_KEY" =~ ^mcp_ ]]; then
              warn "API key doesn't start with 'mcp_'. Are you sure it's correct?"
            fi

            MCP_SERVER_URL="{mcp_url}"

            # Try claude mcp add first (preferred, handles auth properly)
            if command -v claude &>/dev/null; then
              claude mcp remove kandela 2>/dev/null || true
              if claude mcp add -t http -H "Authorization: Bearer $API_KEY" -s user kandela "$MCP_SERVER_URL" 2>/dev/null; then
                info "MCP server registered via claude mcp add"
              else
                warn "claude mcp add failed — falling back to .claude.json"
                _MCP_CLI_FAIL=true
              fi
            else
              _MCP_CLI_FAIL=true
            fi

            # Fallback: write .claude.json directly
            if [ "${{_MCP_CLI_FAIL:-}}" = "true" ]; then
              if [ -f "$CLAUDE_JSON" ]; then
                cp "$CLAUDE_JSON" "$CLAUDE_JSON.kandela-backup-$TIMESTAMP"
              fi

              $PYTHON << ENDOFPY
        import json, os
        cj = "$CLAUDE_JSON_PY"
        config = {{}}
        if os.path.exists(cj):
            with open(cj) as f:
                config = json.load(f)
        if "mcpServers" not in config:
            config["mcpServers"] = {{}}
        config["mcpServers"]["kandela"] = {{
            "type": "http",
            "url": "$MCP_SERVER_URL",
            "headers": {{
                "Authorization": "Bearer $API_KEY"
            }}
        }}
        with open(cj, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\\n")
        ENDOFPY

              if [ $? -eq 0 ]; then
                info ".claude.json configured (fallback)"
              else
                error "Failed to configure .claude.json"
              fi
            fi

            # ── Save API key to file (chmod 600) ──
            touch "$HOOKS_DIR/.kandela-api-key"
            chmod 600 "$HOOKS_DIR/.kandela-api-key"
            echo "$API_KEY" > "$HOOKS_DIR/.kandela-api-key"
            info "$(_t install_api_key_saved)"
          fi
        fi
    """))

    # ── Fix Stop hook MCP URL to use API key ──
    parts.append(textwrap.dedent(f"""
        # ═══ Patch Stop hook with server URL and auth ═══
        STOP_HOOK="$HOOKS_DIR/kandela-auto-save-check.sh"
        STOP_HOOK_PY=$(cygpath -m "$STOP_HOOK" 2>/dev/null || echo "$STOP_HOOK")
        if [ -f "$STOP_HOOK" ]; then
          AUTH_VALUE=""
          if [ -n "$API_KEY" ]; then
            AUTH_VALUE="Bearer $API_KEY"
          fi
          if ! $PYTHON -c "
        import sys
        with open('$STOP_HOOK_PY') as f:
            c = f.read()
        c = c.replace('__MCP_URL_PLACEHOLDER__', '{mcp_url}')
        c = c.replace('__AUTH_HEADER_PLACEHOLDER__', '${{AUTH_VALUE}}')
        with open('$STOP_HOOK_PY', 'w') as f:
            f.write(c)
        " 2>&1; then
            warn "Failed to patch Stop hook — using sed fallback"
            sed -i.bak "s|__MCP_URL_PLACEHOLDER__|{mcp_url}|g" "$STOP_HOOK"
            sed -i.bak "s|__AUTH_HEADER_PLACEHOLDER__|${{AUTH_VALUE}}|g" "$STOP_HOOK"
            rm -f "$STOP_HOOK.bak"
          fi
          # Verify no placeholders remain
          if grep -q -e '__MCP_URL_PLACEHOLDER__' -e '__AUTH_HEADER_PLACEHOLDER__' "$STOP_HOOK" 2>/dev/null; then
            warn "Stop hook still contains unresolved placeholders!"
          else
            info "Stop hook configured with server URL"
          fi
        fi
    """))

    # ── Fix Session start hook with server health URL ──
    parts.append(textwrap.dedent(f"""
        # ═══ Patch Session start hook with health check URL ═══
        START_HOOK="$HOOKS_DIR/kandela-session-start.sh"
        START_HOOK_PY=$(cygpath -m "$START_HOOK" 2>/dev/null || echo "$START_HOOK")
        if [ -f "$START_HOOK" ]; then
          if ! $PYTHON -c "
        import sys
        with open('$START_HOOK_PY') as f:
            c = f.read()
        c = c.replace('__MCP_HEALTH_URL_PLACEHOLDER__', '{server_url}/api/health')
        with open('$START_HOOK_PY', 'w') as f:
            f.write(c)
        " 2>&1; then
            warn "Failed to patch Session start hook — using sed fallback"
            sed -i.bak "s|__MCP_HEALTH_URL_PLACEHOLDER__|{server_url}/api/health|g" "$START_HOOK"
            rm -f "$START_HOOK.bak"
          fi
          # Verify no placeholders remain
          if grep -q '__MCP_HEALTH_URL_PLACEHOLDER__' "$START_HOOK" 2>/dev/null; then
            warn "Session start hook still contains unresolved placeholders!"
          else
            info "Session start hook configured with health check URL"
          fi
        fi
    """))

    # ── Patch PreCompact and PostCompact hooks with health URL ──
    parts.append(textwrap.dedent(f"""
        # ═══ Patch PreCompact/PostCompact hooks with server URL ═══
        for HOOK_FILE in "$HOOKS_DIR/kandela-pre-compact.sh" "$HOOKS_DIR/kandela-post-compact.sh" "$HOOKS_DIR/kandela-context-monitor.sh" "$HOOKS_DIR/kandela-pre-tool.sh" "$HOOKS_DIR/kandela-prompt-guard.sh"; do
          if [ -f "$HOOK_FILE" ] && grep -q '__MCP_HEALTH_URL_PLACEHOLDER__' "$HOOK_FILE" 2>/dev/null; then
            sed -i.bak "s|__MCP_HEALTH_URL_PLACEHOLDER__|{server_url}/api/health|g" "$HOOK_FILE"
            rm -f "$HOOK_FILE.bak"
          fi
        done
    """))


    # ── Verification ──
    parts.append(textwrap.dedent(f"""
        # ═══ Verification ═══
        echo ""
        echo -e "${{CYAN}}── Installation Summary ──${{NC}}"
        echo ""

        # Count installed files
        CMD_COUNT=$(ls -d "$COMMANDS_DIR"/kd-*/SKILL.md 2>/dev/null | wc -l | tr -d ' ')
        HOOK_COUNT=$(ls "$HOOKS_DIR"/kandela-*.sh 2>/dev/null | wc -l | tr -d ' ')

        echo "  Slash commands:  $CMD_COUNT/{len(COMMANDS)}"
        echo "  Hooks:           $HOOK_COUNT/{len(HOOKS)}"
        [ -f "$CLAUDE_DIR/settings.json" ] && echo "  Settings:        ✓" || echo "  Settings:        ✗"
        [ -f "$HOME/.claude.json" ] && grep -q "kandela" "$HOME/.claude.json" 2>/dev/null && echo "  MCP config:      ✓" || echo "  MCP config:      ✗"

        # Check for unresolved placeholders in hooks
        # Exclude guard clauses (lines with !=) which intentionally reference placeholders
        PLACEHOLDER_COUNT=0
        _PH_FILES=$(grep -rl '__[A-Z_]*_PLACEHOLDER__' "$HOOKS_DIR"/kandela-*.sh 2>/dev/null || true)
        if [ -n "$_PH_FILES" ]; then
          _PC=$(echo "$_PH_FILES" | xargs grep -l '="__[A-Z_]*_PLACEHOLDER__"' 2>/dev/null | wc -l | tr -d ' ') || true
          PLACEHOLDER_COUNT="${{_PC:-0}}"
        fi
        if [ "$PLACEHOLDER_COUNT" -gt 0 ]; then
          echo ""
          echo -e "${{RED}}  ⚠ WARNING: $PLACEHOLDER_COUNT hook(s) have unresolved placeholders!${{NC}}"
          grep -n '="__[A-Z_]*_PLACEHOLDER__"' "$HOOKS_DIR"/kandela-*.sh 2>/dev/null | while read line; do
            echo -e "    ${{YELLOW}}$line${{NC}}"
          done
        else
          echo "  Hook patching:   ✓"
        fi

        echo ""
        echo -e "${{GREEN}}$(_t install_complete)${{NC}}"
        echo ""
        echo "$(_t install_next_steps)"
        echo "  1. Run /kd-init <project_id> to initialize a project"
        echo "  2. Run /kd-update in existing projects to apply the latest guide"
        echo "  3. Use /kd-inbox to review unread memos"
        echo "  4. Memory auto-recall will work automatically on each session start"
        echo ""
        echo -e "${{YELLOW}}슬래시 명령이 /dm.* → /kd-*로 변경되었습니다.${{NC}}"
        echo "기존 프로젝트에서 \`/kd-update\`를 실행하여 가이드를 갱신하세요."
        echo ""
        echo -e "Dashboard: ${{CYAN}}{server_url}/dashboard${{NC}}"
        echo -e "Docs:      ${{CYAN}}{server_url}/account${{NC}}"
        echo ""
    """))

    return "".join(parts)


def generate_uninstall_script(server_url: str = DEFAULT_SERVER_URL, lang: str = "en") -> str:
    """Generate a complete bash uninstall script that reverses all install changes.

    Args:
        server_url: The Kandela server URL.
        lang: 2-char language code for i18n (e.g. 'en', 'ko', 'ja').
    """
    parts: list[str] = []

    i18n_block = shell_i18n_block()
    parts.append(textwrap.dedent(f"""\
        #!/bin/bash
        # Kandela — Uninstall script
        # Usage: curl -sL {server_url}/uninstall | bash
        # Removes all Kandela client files and reverts settings to pre-install state.
        set -euo pipefail

        # ── Color helpers ──
        RED='\\033[0;31m'
        GREEN='\\033[0;32m'
        YELLOW='\\033[1;33m'
        CYAN='\\033[0;36m'
        NC='\\033[0m'

        info()  {{ echo -e "${{GREEN}}✓${{NC}} $1"; }}
        warn()  {{ echo -e "${{YELLOW}}⚠${{NC}} $1"; }}
        error() {{ echo -e "${{RED}}✗${{NC}} $1" >&2; }}
    """))

    # ── i18n block ──
    parts.append(i18n_block)
    parts.append("\n")

    # ── Environment Detection (same as install) ──
    parts.append(textwrap.dedent("""\
        # ── Environment Detection ──
        detect_env() {
          OS_TYPE="unknown"; IS_WSL=false; WIN_USER=""
          case "$(uname -s)" in
            Darwin) OS_TYPE="macos" ;;
            Linux)
              OS_TYPE="linux"
              if grep -qi "microsoft\\|wsl" /proc/version 2>/dev/null; then
                IS_WSL=true; OS_TYPE="wsl"
                WIN_USER=$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\\r\\n' || true)
                if [ -z "$WIN_USER" ]; then
                  for u in /mnt/c/Users/*/; do
                    uname=$(basename "$u")
                    case "$uname" in Public|Default|"All Users"|"Default User") continue ;; esac
                    if [ -f "$u.claude.json" ] || [ -d "$u.claude" ]; then WIN_USER="$uname"; break; fi
                  done
                fi
              fi ;;
            MINGW*|MSYS*|CYGWIN*) OS_TYPE="windows_git_bash" ;;
          esac
          export OS_TYPE IS_WSL WIN_USER
        }
        detect_env

        PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "python3")
        export PYTHONUTF8=1

    """))

    parts.append(textwrap.dedent(f"""\
        if [ "$IS_WSL" = true ] && [ -n "$WIN_USER" ]; then
          CLAUDE_DIR="/mnt/c/Users/$WIN_USER/.claude"
          CLAUDE_JSON="/mnt/c/Users/$WIN_USER/.claude.json"
        else
          CLAUDE_DIR="$HOME/.claude"
          CLAUDE_JSON="$HOME/.claude.json"
        fi
        COMMANDS_DIR="$CLAUDE_DIR/skills"
        HOOKS_DIR="$CLAUDE_DIR/hooks"
        SETTINGS_FILE="$CLAUDE_DIR/settings.json"

        # ── Pre-flight: detect installed items ──
        HOOK_FILES=$(ls "$HOOKS_DIR"/kandela-*.sh 2>/dev/null || true)
        HOOK_COUNT=$(echo "$HOOK_FILES" | grep -c . || echo 0)
        CMD_DIRS=$(ls -d "$COMMANDS_DIR"/kd-*/ "$COMMANDS_DIR"/dm.*/ 2>/dev/null || true)
        CMD_COUNT=$(echo "$CMD_DIRS" | grep -c . || echo 0)
        HAS_API_KEY=false
        [ -f "$HOOKS_DIR/.kandela-api-key" ] && HAS_API_KEY=true
        HAS_CACHE=false
        [ -d "$CLAUDE_DIR/kandela-cache" ] && HAS_CACHE=true
        HAS_MCP=false
        if [ -f "$CLAUDE_JSON" ]; then
          $PYTHON -c "import json; d=json.load(open('$CLAUDE_JSON')); exit(0 if 'kandela' in d.get('mcpServers',{{}}) else 1)" 2>/dev/null && HAS_MCP=true || true
        fi
        HAS_SETTINGS_HOOKS=false
        if [ -f "$SETTINGS_FILE" ]; then
          grep -q 'kandela-' "$SETTINGS_FILE" 2>/dev/null && HAS_SETTINGS_HOOKS=true || true
        fi

        echo ""
        echo -e "${{CYAN}}╔══════════════════════════════════════════╗${{NC}}"
        echo -e "${{CYAN}}║     $(_t uninstall_banner)                   ║${{NC}}"
        echo -e "${{CYAN}}╚══════════════════════════════════════════╝${{NC}}"
        echo ""
        echo "$(_t uninstall_will_remove)"
        [ "$HAS_MCP" = true ]           && echo "  • MCP 서버 연결 (~/.claude.json → kandela 항목)"
        [ "$HAS_SETTINGS_HOOKS" = true ] && echo "  • settings.json의 Kandela 훅 항목"
        [ "$HOOK_COUNT" -gt 0 ]         && echo "  • 훅 스크립트 $HOOK_COUNT개 (~/.claude/hooks/kandela-*.sh)"
        [ "$HAS_API_KEY" = true ]       && echo "  • API 키 파일 (~/.claude/hooks/.kandela-api-key)"
        [ "$CMD_COUNT" -gt 0 ]          && echo "  • 슬래시 명령 $CMD_COUNT개 (~/.claude/skills/kd-*/)"
        echo ""
        echo "$(_t uninstall_preserved)"
        echo "  • 기존 MCP 서버 설정 (kandela 외 항목)"
        echo "  • 기존 훅 (Kandela 외 항목)"
        echo "  • 프로젝트 파일 (CLAUDE.md 등)"
        echo ""
        echo "$(_t uninstall_server_data_note | sed "s|{{url}}|{server_url}|g")"
        echo ""

        # ── Confirmation ──
        if [ -t 0 ]; then
          read -r -p "$(_t uninstall_confirm)" REPLY </dev/tty
          if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
            echo "$(_t uninstall_cancelled)"
            exit 0
          fi
        else
          echo "$(_t uninstall_noninteractive_continue)"
        fi
        echo ""

        # ── Backup settings.json ──
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)
        if [ -f "$SETTINGS_FILE" ]; then
          BACKUP_FILE="$SETTINGS_FILE.kandela-backup-$TIMESTAMP"
          cp "$SETTINGS_FILE" "$BACKUP_FILE"
          info "$(_t uninstall_settings_backup | sed "s|{{path}}|$BACKUP_FILE|g")"
        fi
        if [ -f "$CLAUDE_JSON" ]; then
          CLAUDE_JSON_BACKUP="$CLAUDE_JSON.kandela-backup-$TIMESTAMP"
          cp "$CLAUDE_JSON" "$CLAUDE_JSON_BACKUP"
          info "$(_t uninstall_claudejson_backup | sed "s|{{path}}|$CLAUDE_JSON_BACKUP|g")"
        fi

        # ── Remove from settings.json (Python JSON surgery) ──
        if [ -f "$SETTINGS_FILE" ]; then
          $PYTHON - "$SETTINGS_FILE" <<'ENDOFPY'
import json, sys

sf = sys.argv[1]
with open(sf) as f:
    s = json.load(f)

changed = False

# Remove hooks referencing kandela-*.sh scripts or prompt-guard-gate URL
if "hooks" in s:
    for event in list(s["hooks"].keys()):
        original = s["hooks"][event]
        filtered = []
        for entry in original:
            keep = True
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                url = hook.get("url", "")
                if ("kandela-" in cmd or "memory-" in cmd) and ".sh" in cmd:
                    keep = False
                    break
                if "prompt-guard-gate" in url or "kandela" in url.lower():
                    keep = False
                    break
            if keep:
                filtered.append(entry)
        if len(filtered) != len(original):
            s["hooks"][event] = filtered
            changed = True
    # Remove empty event keys
    s["hooks"] = {{k: v for k, v in s["hooks"].items() if v}}
    if not s["hooks"]:
        del s["hooks"]
        changed = True

# Remove mcp__kandela__* and mcp__memory__* permissions (if present)
if "permissions" in s and "allow" in s["permissions"]:
    before = len(s["permissions"]["allow"])
    s["permissions"]["allow"] = [
        p for p in s["permissions"]["allow"]
        if not p.startswith("mcp__kandela__") and not p.startswith("mcp__memory__")
    ]
    if len(s["permissions"]["allow"]) != before:
        changed = True
    if not s["permissions"]["allow"]:
        del s["permissions"]["allow"]
    if not s["permissions"]:
        del s["permissions"]

if changed:
    with open(sf, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\\n")
    print("UPDATED")
else:
    print("UNCHANGED")
ENDOFPY
          if [ $? -eq 0 ]; then
            info "$(_t uninstall_settings_done)"
          else
            warn "$(_t uninstall_settings_error)"
          fi
        fi

        # ── Remove kandela from .claude.json ──
        if [ -f "$CLAUDE_JSON" ]; then
          $PYTHON - "$CLAUDE_JSON" <<'ENDOFPY'
import json, sys

cj = sys.argv[1]
with open(cj) as f:
    config = json.load(f)

changed = False
for key in ("kandela", "memory-mcp", "memory"):
    if "mcpServers" in config and key in config["mcpServers"]:
        del config["mcpServers"][key]
        changed = True
if changed and "mcpServers" in config and not config["mcpServers"]:
    del config["mcpServers"]

if changed:
    with open(cj, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\\n")
    print("UPDATED")
else:
    print("UNCHANGED")
ENDOFPY
          if [ $? -eq 0 ]; then
            info "$(_t uninstall_claudejson_done)"
          else
            warn "$(_t uninstall_claudejson_error)"
          fi
        fi

        # ── Remove hook files ──
        REMOVED_HOOKS=0
        for f in "$HOOKS_DIR"/kandela-*.sh "$HOOKS_DIR"/memory-*.sh; do
          if [ -f "$f" ]; then
            rm -f "$f"
            REMOVED_HOOKS=$((REMOVED_HOOKS + 1))
          fi
        done
        [ "$REMOVED_HOOKS" -gt 0 ] && info "$(_t uninstall_hooks_removed | sed "s|{{n}}|$REMOVED_HOOKS|g")" || info "$(_t uninstall_hooks_none)"

        # ── Remove API key and related state files ──
        REMOVED_STATE=0
        for state_file in \\
          "$HOOKS_DIR/.kandela-api-key" \\
          "$HOOKS_DIR/.kandela-install-version" \\
          "$HOOKS_DIR/.kandela-guard-level" \\
          "$HOOKS_DIR/.kandela-guard-tone" \\
          "$HOOKS_DIR/.kandela-guard-paused-until" \\
          "$HOOKS_DIR/.kandela-guard-stats.jsonl" \\
          "$HOOKS_DIR/.memory-api-key" \\
          "$HOOKS_DIR/.memory-install-version" \\
          "$HOOKS_DIR/.memory-guard-level" \\
          "$HOOKS_DIR/.memory-guard-tone" \\
          "$HOOKS_DIR/.memory-guard-paused-until" \\
          "$HOOKS_DIR/.memory-guard-stats.jsonl"
        do
          if [ -f "$state_file" ]; then
            rm -f "$state_file"
            REMOVED_STATE=$((REMOVED_STATE + 1))
          fi
        done
        [ "$REMOVED_STATE" -gt 0 ] && info "$(_t uninstall_state_removed | sed "s|{{n}}|$REMOVED_STATE|g")"

        # ── Remove slash command directories (kd-* and legacy dm.*) ──
        REMOVED_CMDS=0
        for cmd_dir in "$COMMANDS_DIR"/kd-*/ "$COMMANDS_DIR"/dm.*/; do
          if [ -d "$cmd_dir" ]; then
            rm -rf "$cmd_dir"
            REMOVED_CMDS=$((REMOVED_CMDS + 1))
          fi
        done
        [ "$REMOVED_CMDS" -gt 0 ] && info "$(_t uninstall_cmds_removed | sed "s|{{n}}|$REMOVED_CMDS|g")" || info "$(_t uninstall_cmds_none)"

        # ── Ask about local cache ──
        if [ -d "$CLAUDE_DIR/kandela-cache" ] || [ -d "$CLAUDE_DIR/memory-cache" ]; then
          echo ""
          if [ -t 0 ]; then
            read -r -p "$(_t uninstall_cache_prompt)" CACHE_REPLY </dev/tty
            if [[ "$CACHE_REPLY" =~ ^[Yy]$ ]]; then
              rm -rf "$CLAUDE_DIR/kandela-cache" "$CLAUDE_DIR/memory-cache"
              info "$(_t uninstall_cache_deleted)"
            else
              info "$(_t uninstall_cache_kept)"
            fi
          else
            info "$(_t uninstall_cache_noninteractive)"
          fi
        fi

        # ── Verification ──
        echo ""
        echo -e "${{CYAN}}── $(_t uninstall_verify_header) ──${{NC}}"
        WARN_COUNT=0

        # Check MCP entry removed
        if [ -f "$CLAUDE_JSON" ]; then
          if $PYTHON -c "import json; d=json.load(open('$CLAUDE_JSON')); s=d.get('mcpServers',{{}}); exit(0 if 'kandela' in s or 'memory-mcp' in s else 1)" 2>/dev/null; then
            warn "WARN: .claude.json에 kandela 항목이 남아있습니다"
            WARN_COUNT=$((WARN_COUNT + 1))
          else
            echo "  .claude.json:   ✓"
          fi
        else
          echo "  .claude.json:   ✓ (파일 없음)"
        fi

        # Check hook files removed
        REMAINING_HOOKS=$(ls "$HOOKS_DIR"/kandela-*.sh 2>/dev/null | wc -l | tr -d ' ')
        if [ "$REMAINING_HOOKS" -gt 0 ]; then
          warn "WARN: 훅 파일 $REMAINING_HOOKS개가 남아있습니다"
          WARN_COUNT=$((WARN_COUNT + 1))
        else
          echo "  훅 스크립트:    ✓"
        fi

        # Check API key removed
        if [ -f "$HOOKS_DIR/.kandela-api-key" ]; then
          warn "WARN: .kandela-api-key 파일이 남아있습니다"
          WARN_COUNT=$((WARN_COUNT + 1))
        else
          echo "  API 키 파일:    ✓"
        fi

        # Check slash commands removed
        REMAINING_CMDS=$( (ls -d "$COMMANDS_DIR"/kd-*/ "$COMMANDS_DIR"/dm.*/ 2>/dev/null || true) | wc -l | tr -d ' ')
        if [ "$REMAINING_CMDS" -gt 0 ]; then
          warn "WARN: 슬래시 명령 $REMAINING_CMDS개가 남아있습니다"
          WARN_COUNT=$((WARN_COUNT + 1))
        else
          echo "  슬래시 명령:    ✓"
        fi

        # Check settings.json hooks removed
        if [ -f "$SETTINGS_FILE" ] && grep -qE 'kandela-|memory-' "$SETTINGS_FILE" 2>/dev/null; then
          warn "WARN: settings.json에 Kandela 항목이 남아있을 수 있습니다"
          WARN_COUNT=$((WARN_COUNT + 1))
        else
          echo "  settings.json:  ✓"
        fi

        echo ""
        if [ "$WARN_COUNT" -eq 0 ]; then
          echo -e "${{GREEN}}$(_t uninstall_complete)${{NC}}"
        else
          echo -e "${{YELLOW}}$(_t uninstall_complete_warn | sed "s|{{n}}|$WARN_COUNT|g")${{NC}}"
        fi
        echo ""
        echo "  $(_t uninstall_backup_files)"
        [ -f "$SETTINGS_FILE.kandela-backup-$TIMESTAMP" ] && echo "    $SETTINGS_FILE.kandela-backup-$TIMESTAMP"
        [ -f "$CLAUDE_JSON.kandela-backup-$TIMESTAMP" ]   && echo "    $CLAUDE_JSON.kandela-backup-$TIMESTAMP"
        echo "  $(_t uninstall_backup_hint)"
        echo ""
        echo "  $(_t uninstall_server_data_note | sed "s|{{url}}|{server_url}|g")"
        echo ""
    """))

    return "".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Route registration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def register_install_routes(mcp: Any) -> None:
    """Register the /install endpoint on the FastMCP server.

    Called from ``register_dashboard_routes()`` in dashboard.py.
    """

    def _detect_server_url(request: Request) -> str:
        """Detect server URL from request: ?server= > Host header > env default."""
        server_url = request.query_params.get("server", "")
        if not server_url:
            host = request.headers.get("host", "")
            if host and "localhost" not in host and "127.0.0.1" not in host:
                scheme = "https" if request.url.scheme == "https" or host.endswith(".ai") or host.endswith(".dev") else "http"
                server_url = f"{scheme}://{host}"
            else:
                server_url = DEFAULT_SERVER_URL
        return server_url

    @mcp.custom_route("/install", methods=["GET"])
    async def install_script(request: Request) -> PlainTextResponse:
        """Serve the install script for ``curl | bash``."""
        server_url = _detect_server_url(request)
        lang = detect_lang(request)
        script = generate_install_script(server_url=server_url, lang=lang)
        checksum = hashlib.sha256(script.encode()).hexdigest()
        return PlainTextResponse(
            script,
            media_type="text/x-shellscript",
            headers={
                "Content-Disposition": "inline; filename=install.sh",
                "X-Script-SHA256": checksum,
                "X-Install-Version": str(INSTALL_VERSION),
            },
        )

    @mcp.custom_route("/uninstall", methods=["GET"])
    async def uninstall_script(request: Request) -> PlainTextResponse:
        """Serve the uninstall script for ``curl | bash``."""
        server_url = _detect_server_url(request)
        lang = detect_lang(request)
        script = generate_uninstall_script(server_url=server_url, lang=lang)
        checksum = hashlib.sha256(script.encode()).hexdigest()
        return PlainTextResponse(
            script,
            media_type="text/x-shellscript",
            headers={
                "Content-Disposition": "inline; filename=uninstall.sh",
                "X-Script-SHA256": checksum,
            },
        )
