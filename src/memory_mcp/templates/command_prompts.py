"""Server-side slash command prompts for IP protection.

Detailed command prompts are stored here on the server side,
instead of being distributed to client machines. Clients receive
thin stubs that call get_command_prompt() to fetch the
actual instructions.
"""

from __future__ import annotations

import re

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Command prompts — keyed by command name (without 'kd-' prefix)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMAND_PROMPTS: dict[str, str] = {}

# ── kd-init ──────────────────────────────────────────────────────

COMMAND_PROMPTS["init"] = """\
CLAUDE.md + .kandela-guide.md에 Kandela 가이드를 설정합니다.

프로젝트 ID: `{arguments}`

## 실행 절차

1. **인자 검증**:
   - `{arguments}`가 비어있으면: 현재 작업 디렉토리의 폴더명을 기본값으로 제안하세요.
     예) 현재 경로가 `/Users/alice/my-project`이면 → "프로젝트명을 `my-project`로 설정할까요? 엔터를 누르면 그대로 사용하고, 다른 이름을 입력하면 그것으로 설정합니다." 라고 묻고 사용자 응답을 기다리세요.
     사용자가 엔터(빈 입력)를 누르면 폴더명을 project_id로 사용, 다른 텍스트를 입력하면 그것을 project_id로 사용.
   - project_id가 `[a-zA-Z0-9_-]+` 패턴이 아니면 에러 (공백·특수문자 불가, `-`와 `_`는 허용)

2. **CLAUDE.md 상태 확인**:
   - CLAUDE.md가 없으면 → **3a**로
   - CLAUDE.md가 있고 `<!-- KANDELA-GUIDE-START`가 없으면 → **3b**로 (하위호환: `<!-- MEMORY-MCP-GUIDE-START`도 감지)
   - CLAUDE.md가 있고 `<!-- KANDELA-GUIDE-START`가 있으면 → "이미 설정됨. `/kd-update`로 업데이트하세요." 안내 후 종료

3. **가이드 가져오기**: `get_guide(project_id='{arguments}')` 도구를 호출하세요.

4. **CLAUDE.md 작성**:
   - 응답에서 `---` 구분선으로 나뉜 두 부분을 분리:
     - 첫 번째 부분 (`<!-- KANDELA-GUIDE-START` ~ `GUIDE-END -->`): CLAUDE.md용
     - `--- REFERENCE FILE (.kandela-guide.md) ---` 이후: .kandela-guide.md용
   - **3a** (파일 없음): 아래 구조로 새 CLAUDE.md를 생성:
     ```
     # CLAUDE.md

     ## 프로젝트 개요
     <!-- TODO: 프로젝트 설명을 작성하세요 -->

     {{CLAUDE.md용 가이드 내용}}
     ```
   - **3b** (파일 있음, 가이드 없음): 기존 CLAUDE.md 끝에 빈 줄 2개 + 가이드 내용을 추가

5. **.kandela-guide.md 작성**:
   - 프로젝트 루트에 `.kandela-guide.md` 파일을 생성 (기존 파일이 있으면 덮어쓰기)
   - 레퍼런스 부분 (`--- REFERENCE FILE` 이후)의 내용을 기록

6. **서버에 프로젝트 공간 생성**:
   - `store(project='{arguments}', content='프로젝트 초기화됨', memory_type='fact', importance=3.0, tags=['system', 'init'])` 호출
   - 이렇게 하면 서버에 프로젝트가 등록되어 `list_projects()`에 즉시 표시됩니다.

7. **워크스페이스 경로 저장**:
   - CLAUDE.md가 생성/수정된 디렉토리의 **절대 경로**를 확인하세요
   - `store(project='{arguments}', content='워크스페이스 경로: {{CLAUDE.md가 있는 디렉토리 절대경로}}', memory_type='fact', importance=9.0, tags=['workspace', 'path'])` 호출
   - 이 정보는 네스팅된 프로젝트에서 올바른 CLAUDE.md를 찾는 데 필수입니다

8. **도메인별 추천 gotcha**:
   - 프로젝트 코드/파일을 간단히 살펴보고 도메인을 판별하세요:
     - web_backend: Django, Flask, FastAPI, Express, Spring 등
     - web_frontend: React, Vue, Angular, Next.js 등
     - data_science: pandas, numpy, sklearn, pytorch 등
     - devops: Docker, Kubernetes, Terraform, CI/CD 등
     - database: PostgreSQL, MySQL, MongoDB, Redis 등
     - mobile: Android, iOS, Flutter, React Native 등
   - 판별된 도메인에 대해 사용자에게 알려주세요:
     "이 프로젝트는 **{도메인}** 도메인으로 보입니다. 자주 발생하는 실수를 방지하기 위해 추천 gotcha를 저장할까요?"
   - 사용자가 동의하면, 해당 도메인의 gotcha를 `store`로 저장:
     - web_backend: 배포 시 --no-deps, 마이그레이션 전용 컨테이너, 테스트 환경 분리, .env 보안, API 버전 관리
     - web_frontend: 환경변수 prefix, SSR window 접근, 패키지 매니저 통일, 네이밍 컨벤션
     - data_science: 상대경로 사용, 랜덤시드 고정, 노트북 실행순서, 대용량 데이터 Git 제외
     - devops: --no-deps, 프로덕션 exec/cp 금지, SSH 포트 기록, health check, 로그 로테이션
     - database: 전용 환경 마이그레이션, CONCURRENTLY 인덱스, 백업 절차
     - mobile: 서명 키 보관, 비동기 처리, 스토어 가이드라인
   - 각 gotcha는 importance 7.0~9.0, tags에 gotcha + 도메인명 포함

9. **완료 보고**:
   - 설정된 project_id를 알려주세요
   - "세션을 재시작하면 자동 회상(auto-recall)이 동작합니다" 안내
   - "CLAUDE.md에 핵심 규칙, .kandela-guide.md에 상세 레퍼런스가 설정되었습니다" 안내
   - "워크스페이스 경로: {{경로}}" 안내. 경로 변경 시 `/kd-workspace` 사용 안내
   - 도메인 gotcha를 저장했으면 "{{N}}개의 추천 gotcha를 저장했습니다" 안내
"""

# ── kd-link ──────────────────────────────────────────────────────

COMMAND_PROMPTS["link"] = """\
기존 Kandela 프로젝트를 현재 작업 디렉토리에 연결합니다.
메모리는 이미 서버에 있지만 CLAUDE.md가 없어서 세션에서 활용되지 못하는 프로젝트를 연결할 때 사용합니다.

프로젝트 ID: `{arguments}`

## `/kd-init`과의 차이
- `/kd-init`: 새 프로젝트를 처음 만들 때 (메모리 0건인 상태에서 시작)
- `/kd-link`: **이미 메모리가 있는** 기존 프로젝트를 현재 디렉토리에 연결할 때

## 실행 절차

1. **인자 검증**:
   - `{arguments}`가 비어있으면:
     - `list_projects` 도구를 호출하여 프로젝트 목록을 보여주세요
     - "연결할 프로젝트를 선택하세요: `/kd-link <project_id>`" 안내
     - 종료
   - project_id가 `[a-zA-Z0-9_-]+` 패턴이 아니면 에러

2. **프로젝트 존재 확인**:
   - `search(project='{arguments}', query='project overview')` 도구를 호출하여 해당 프로젝트에 메모리가 있는지 확인
   - 메모리가 0건이면 → "해당 프로젝트에 메모리가 없습니다. 새 프로젝트라면 `/kd-init {arguments}`를 사용하세요." 안내 후 종료

3. **CLAUDE.md 상태 확인**:
   - CLAUDE.md가 없으면 → **4a**로
   - CLAUDE.md가 있고 `<!-- KANDELA-GUIDE-START`가 없으면 → **4b**로 (하위호환: `<!-- MEMORY-MCP-GUIDE-START`도 감지)
   - CLAUDE.md가 있고 `<!-- KANDELA-GUIDE-START`가 있으면:
     - 기존 project_id를 추출하여 확인
     - 같은 프로젝트면 → "이미 연결됨" 안내 후 종료
     - 다른 프로젝트면 → 사용자에게 "현재 `기존_id`가 설정되어 있습니다. `{arguments}`로 변경할까요?" 확인 후 진행

4. **가이드 설정** (dminit과 동일한 절차):
   - `get_guide(project_id='{arguments}')` 도구를 호출
   - 응답에서 `---` 구분선으로 나뉜 두 부분을 분리:
     - 첫 번째 부분 (`<!-- KANDELA-GUIDE-START` ~ `GUIDE-END -->`): CLAUDE.md용
     - `--- REFERENCE FILE (.kandela-guide.md) ---` 이후: .kandela-guide.md용
   - **4a** (파일 없음): 아래 구조로 새 CLAUDE.md를 생성:
     ```
     # CLAUDE.md

     ## 프로젝트 개요
     <!-- TODO: 프로젝트 설명을 작성하세요 -->

     {{CLAUDE.md용 가이드 내용}}
     ```
   - **4b** (파일 있음, 가이드 없음): 기존 CLAUDE.md 끝에 빈 줄 2개 + 가이드 내용을 추가

5. **.kandela-guide.md 작성**:
   - 프로젝트 루트에 `.kandela-guide.md` 파일을 생성 (기존 파일이 있으면 덮어쓰기)

6. **워크스페이스 경로 저장**:
   - CLAUDE.md가 생성/수정된 디렉토리의 **절대 경로**를 확인하세요
   - 먼저 `search(project='{arguments}', query='워크스페이스 경로', tags=['workspace', 'path'], n_results=1)` 호출하여 기존 경로가 있는지 확인
   - 기존 경로가 있으면 `update`로 수정, 없으면 `store(project='{arguments}', content='워크스페이스 경로: {{절대경로}}', memory_type='fact', importance=9.0, tags=['workspace', 'path'])` 호출
   - 이 정보는 네스팅된 프로젝트에서 올바른 CLAUDE.md를 찾는 데 필수입니다

7. **기존 기억 즉시 로드**:
   - `auto_recall(project='{arguments}')` 도구를 호출하여 기존 기억을 불러옵니다
   - 불러온 기억의 개수와 주요 내용을 간략히 요약해주세요

8. **완료 보고**:
   - "프로젝트 `{arguments}` (N건의 기억)이 현재 디렉토리에 연결되었습니다" 안내
   - "다음 세션부터 자동 회상(auto-recall)이 동작합니다" 안내
   - "워크스페이스 경로: {{경로}}" 안내. 경로 변경 시 `/kd-workspace` 사용 안내
"""

# ── kd-update ────────────────────────────────────────────────────

COMMAND_PROMPTS["update"] = """\
CLAUDE.md 가이드 업데이트 + 기억 최신화를 수행합니다.

## Part 1: 가이드 버전 업데이트

1. **CLAUDE.md 확인**:
   - CLAUDE.md가 없으면 → "`/kd-init <project_id>`로 먼저 설정하세요" 안내 후 종료
   - `<!-- KANDELA-GUIDE-START` 마커가 없으면 → "`/kd-init <project_id>`로 먼저 설정하세요" 안내 후 종료 (하위호환: `<!-- MEMORY-MCP-GUIDE-START`도 감지)
   - `<!-- KANDELA-GUIDE-END -->` 마커가 없으면 → "END 마커가 누락되었습니다. CLAUDE.md를 확인하세요." 경고 후 종료 (내용 삼킴 방지) (하위호환: `<!-- MEMORY-MCP-GUIDE-END -->`도 감지)

2. **현재 정보 추출**:
   - START 마커에서 현재 버전 추출: `<!-- KANDELA-GUIDE-START v{{N}} -->`의 N (또는 `<!-- MEMORY-MCP-GUIDE-START v{{N}} -->`)
   - 버전 숫자를 추출할 수 없으면 N은 비워두세요 (서버가 업데이트 필요로 판단)
   - `memory project ID:` 줄에서 기존 project_id 추출

3. **최신 가이드 가져오기**: `get_guide(project_id='{{추출한 project_id}}', current_version={{추출한 N}})` 도구를 호출하세요.
   - N을 추출하지 못한 경우: `get_guide(project_id='{{추출한 project_id}}')` (current_version 생략)

4. **업데이트 필요 여부 확인**:
   - 응답의 `NEEDS_UPDATE:` 값을 확인하세요 (true 또는 false)
   - `NEEDS_UPDATE: false` → "이미 최신 버전입니다 (v{{N}})" 기록 후 Part 2로
   - `NEEDS_UPDATE: true` → 5로 진행
   - `NEEDS_UPDATE` 줄이 없는 경우 (하위 호환): `GUIDE_VERSION:` 값과 로컬 버전 비교로 판단

5. **CLAUDE.md 가이드 교체**:
   - `<!-- KANDELA-GUIDE-START ... -->` 줄부터 `<!-- KANDELA-GUIDE-END -->` 줄까지 (마커 줄 포함)를 새 가이드 내용으로 교체 (하위호환: `MEMORY-MCP-GUIDE` 마커도 동일 처리)
   - **중요**: 마커 바깥의 내용(프로젝트 개요, 기술 스택, 진행 상태, 배포 환경 등)은 절대 수정하지 마세요

6. **.kandela-guide.md 업데이트**:
   - 응답에서 `--- REFERENCE FILE (.kandela-guide.md) ---` 이후의 내용을 추출
   - 프로젝트 루트의 `.kandela-guide.md` 파일을 새 내용으로 덮어쓰기
   - 파일이 없으면 새로 생성

## Part 2: 기억 최신화

7. **최근 기억 확인**:
   - `auto_recall(project='{{project_id}}', mode='full')` 호출
   - 최근 세션 요약과 기억 목록을 확인

8. **공백 분석** — 현재 대화 컨텍스트(이전 메시지들)와 불러온 기억을 비교하여 다음을 판단:
   - 이 세션에서 수행한 작업 중 기억에 없는 것이 있는가?
   - 최근 기억의 마지막 날짜와 현재 날짜 사이에 공백이 있는가?
   - 핵심 결정/사실 중 저장되지 않은 것이 있는가?
   - 누락 항목이 없으면 → 최종 보고로 건너뛰기

9. **누락 기억 저장**:
   - 저장할 내용 요약을 사용자에게 보여주고 확인 받기
   - 확인 후:
     - 세션 요약: `summarize_session(project='{{project_id}}', summary='...', tags=[...])`
     - 핵심 결정/사실: `store(project='{{project_id}}', content='...', memory_type='...', importance=N, tags=[...])`
   - 저장 원칙 준수: 독립적으로 이해 가능하게, 장기기억만, 중복 금지

## 최종 보고

아래 형식으로 결과를 보고하세요:

```
## /kd-update 결과

### 가이드
- v{{old}} → v{{new}} 업데이트 완료 (또는 "이미 최신 v{{N}}")

### 기억
- 세션 요약 N건 저장
- 결정/사실 N건 저장
(또는 "기억이 최신 상태입니다")
```
"""

# ── kd-status ────────────────────────────────────────────────────

COMMAND_PROMPTS["status"] = """\
현재 활성 프로젝트의 상태를 보여줍니다.

## 실행 절차

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 찾아 project_id를 추출하세요.

2. **프로젝트 ID가 없으면**:
   - `list_projects` 도구를 호출하여 사용 가능한 프로젝트 목록을 보여주세요.
   - "CLAUDE.md에 memory project ID가 설정되지 않았습니다." 안내
   - "`/kd-init <project_id>` 또는 `/kd-link <project_id>`로 프로젝트를 설정하세요." 안내
   - 종료

3. **프로젝트 상태 조회**: `auto_recall(project='{{project_id}}', mode='brief')` 도구를 호출하세요.

4. **결과 표시**: 아래 형식으로 보여주세요:
   ```
   현재 프로젝트: {{project_id}}
   - 메모리: N건 (critical N건)
   - 최근 세션: YYYY-MM-DD 요약
   - 미확인 메모: N건 (있을 경우)
   ```

5. **미확인 메모 알림**: 미확인 메모가 있으면 `/kd-inbox`로 확인할 수 있다고 안내하세요.
"""

# ── kd-task ──────────────────────────────────────────────────────

COMMAND_PROMPTS["task"] = """\
대기 작업(pending tasks)을 확인하고 처리합니다.

## 실행 절차

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 찾아 project_id를 추출하세요.

2. **프로젝트 ID가 없으면**:
   - `list_projects` 도구를 호출하여 사용 가능한 프로젝트 목록을 보여주세요.
   - "`/kd-init <project_id>` 또는 `/kd-link <project_id>`로 프로젝트를 설정하세요." 안내
   - 종료

3. **대기 작업 조회**: `context_search(query='task pending', project='{{project_id}}', n_results=10)` 도구를 호출하세요.
   - 'task' + 'pending' 태그가 있는 메모리가 대기 작업입니다.

4. **작업이 없으면**: "대기 작업이 없습니다." 안내 후 종료

5. **작업 표시**: 대기 작업 목록을 보여주세요:
   ```
   대기 작업 N건:
   1. [날짜] 작업 내용 (ID: xxx)
   2. [날짜] 작업 내용 (ID: xxx)
   ```

6. **작업 처리**: 사용자에게 처리할 작업을 확인하고 실행하세요.

7. **작업 완료 표시**: 작업을 완료하면 `update`로 태그를 변경하세요:
   - `pending` 태그 제거
   - `completed` 태그 추가
   - 결과 요약을 별도의 `store`로 저장 (tags: ['task-result', 'task-ref:{{원본ID}}'])

8. **완료 확인**: 태그를 변경하여 작업 완료를 기록합니다.
"""

# ── kd-inbox ─────────────────────────────────────────────────────

COMMAND_PROMPTS["inbox"] = """\
미확인(unreviewed) 메모를 조회하고 확인 처리합니다.

## 실행 절차

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 찾아 project_id를 추출하세요.

2. **미확인 메모 조회**: `inbox(project='{{project_id}}')` 도구를 호출하세요.

3. **결과 표시**: 미확인 메모 목록을 사용자에게 보여주세요.

4. **확인 처리 여부**:
   - 메모가 없으면 → "미확인 메모가 없습니다" 안내 후 종료
   - 메모가 있으면 사용자에게 물어보세요:
     - **모두 확인**: `inbox(project='{{project_id}}', mark_reviewed=True)` 호출
     - **선택 확인**: 사용자가 선택한 항목만 `update`로 `unreviewed` 태그 제거
     - **나중에**: 종료
"""


# ── kd-workspace ─────────────────────────────────────────────────

COMMAND_PROMPTS["workspace"] = """\
프로젝트의 워크스페이스 경로(CLAUDE.md가 있는 디렉토리)를 조회하거나 변경합니다.

## 실행 절차

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 찾아 project_id를 추출하세요.
   - CLAUDE.md가 없으면 → "프로젝트를 먼저 설정하세요. `/kd-init <project_id>` 또는 `/kd-link <project_id>`" 안내 후 종료

2. **현재 경로 조회**:
   - `search(project='{{project_id}}', query='워크스페이스 경로', tags=['workspace', 'path'], n_results=1)` 호출
   - 결과에서 현재 저장된 경로를 추출

3. **인자 확인** (`{arguments}`):
   - 비어있으면 → 현재 경로를 표시하고 종료: "워크스페이스: {{경로}}" (경로가 없으면 "워크스페이스 경로가 설정되지 않았습니다. `/kd-workspace {{CWD 절대경로}}`로 설정하세요.")
   - 인자가 있으면 → 4로 진행

4. **경로 변경**:
   - 새 경로 = `{arguments}` (절대 경로로 변환)
   - 기존 워크스페이스 메모리가 있으면 `update(memory_id='...', content='워크스페이스 경로: {{새 절대경로}}')` 호출
   - 없으면 `store(project='{{project_id}}', content='워크스페이스 경로: {{새 절대경로}}', memory_type='fact', importance=9.0, tags=['workspace', 'path'])` 호출

5. **완료 보고**:
   - "워크스페이스 변경: {{이전경로}} → {{새경로}}" (이전 경로가 없었으면 "워크스페이스 설정: {{새경로}}")
"""

# ── kd-sync ──────────────────────────────────────────────────────

COMMAND_PROMPTS["sync"] = """\
로컬 캐시(~/.claude/memory-cache/)의 미동기화 세션 데이터를 정제하여 서버에 저장합니다.

## 실행 절차

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 찾아 project_id를 추출하세요.
   - 없으면 → "`/kd-init <project_id>`로 먼저 설정하세요" 안내 후 종료

2. **캐시 디렉토리 스캔**:
   - `~/.claude/memory-cache/{{project_id}}/` 내 `*.meta.json` 파일 목록 확인
   - 각 meta.json을 Read 도구로 읽어 `"synced": false`인 것만 대상으로 선별

3. **미동기화 세션이 없으면** → "동기화할 캐시가 없습니다." 안내 후 종료

4. **각 세션의 JSONL 파일을 Read 도구로 읽기**:
   - `~/.claude/memory-cache/{{project_id}}/{{session_id}}.jsonl`
   - 각 줄은 JSON 이벤트 (`{{"ts":..., "type":..., "content":..., "len":...}}`)

5. **내용 분석 및 정제**:
   - 중복/노이즈 제거: 짧은 확인 응답, 인사말, 반복 패턴
   - 의미 있는 내용 추출: 결정사항, 코드 패턴, 설정값, 문제 해결, 새로운 지식
   - 각 항목에 대해:
     - `memory_type` 분류: `fact`(환경/설정/구조) · `decision`(결정/이유) · `snippet`(코드/명령) · `summary`(세션요약)
     - `importance` 산정 (9.0+ = 잊으면 삽질, 5.0~8.9 = 있으면 도움)
     - 적절한 `tags` 부여
   - 이미 서버에 저장된 기억과 중복되지 않도록 `context_search`로 기존 기억 확인

6. **정제 결과를 사용자에게 보여주고 확인 받기**:
   ```
   ## 동기화 대상 (N세션, M이벤트)

   ### 세션 {{session_id}} ({{날짜}}, {{event_count}}건)
   - [decision] "설명..." (imp: 7.0, tags: [...])
   - [fact] "설명..." (imp: 8.0, tags: [...])

   ### 세션 요약
   - [summary] "이 세션에서 수행한 작업 요약" (imp: 5.0)

   저장하시겠습니까?
   ```

7. **확인 후 `store`로 각 항목 저장**
   - 세션 요약은 `summarize_session` 사용

8. **완료 후 meta.json 업데이트**:
   - Bash로 각 meta.json의 `synced`를 `true`로 변경:
     `python3 -c "import json; f=open('{{meta_path}}'); d=json.load(f); f.close(); d['synced']=True; f=open('{{meta_path}}','w'); json.dump(d,f,indent=2); f.close()"`

9. **오래된 캐시 정리**:
   - `synced: true`이고 7일 이상 된 파일 삭제 (Bash: `find ... -mtime +7 -delete`)

10. **최종 보고**:
    ```
    ## /kd-sync 결과
    - 처리 세션: N건
    - 저장 기억: M건 (fact N, decision N, snippet N, summary N)
    - 삭제 캐시: K건 (7일 이상 완료분)
    ```
"""

# ── kd-daily ─────────────────────────────────────────────────────────

COMMAND_PROMPTS["daily"] = """\
일일 회의록 조회 명령입니다. 인자($ARGUMENTS)를 확인하세요.

## 날짜 파싱
- 인자 없음 → 오늘 날짜 (YYYY-MM-DD, 로컬 KST 기준)
- `어제` 또는 `yesterday` → 어제 날짜
- `MM-DD` 형식 (예: `03-17`) → 올해 해당 날짜
- `YYYY-MM-DD` 형식 → 그대로 사용

## 실행 절차

1. **프로젝트 ID 확인**: CLAUDE.md에서 `memory project ID:` 줄을 읽어 project_id 추출.

2. **저장된 일일 회의록 조회**:
   - `search(query="일일 회의록", project=project_id, tags=["daily_log"], n_results=3, date_after=..., date_before=...)` 로 해당 날짜 기록 검색
   - 또는 `context_search(query=f"일일 회의록 {target_date}")` 사용

3. **결과 표시**:
   - **저장된 회의록이 있으면** → 내용 전체 출력
   - **오늘 날짜이고 아직 없으면** → 오늘 저장된 기억들을 조회하여 즉석 요약:
     - `search(query="작업 완료 결정 세션", project=project_id, n_results=20, date_after=오늘 00:00 UTC)`
     - 결과를 아래 형식으로 정리:

     ```
     # 일일 현황 — YYYY-MM-DD (임시)

     ## 완료한 작업
     - ...

     ## 주요 결정사항
     - ...

     ## 내일 이어할 사항
     - ...

     ※ 일지는 /kd-journal sync로 생성할 수 있습니다.
     ```
   - **과거 날짜이고 없으면** → "해당 날짜 회의록이 없습니다" 안내
"""

# ── guide (full rules — server-side only) ─────────────────────────────

COMMAND_PROMPTS["guide"] = """\
# Memory System 전체 가이드

## 저장 원칙: 코드에 없는 것만 저장 (CRITICAL)
**저장 전 자가 검증**: "이 정보를 코드/파일을 읽으면 알 수 있는가?" → Yes면 저장 금지.
- **저장 금지**: 코드/파일에서 읽을 수 있는 정보 (구조, import, 함수 시그니처, 설정 파일 내용, 클래스 상속 관계, 디렉토리 구조, 패키지 의존성 등).
- **저장 대상**: Why (결정 이유), Gotcha (실패 경험), 삭제된 히스토리, 인프라/배포 정보, 선호/컨벤션, 팀 합의, 크로스프로젝트 지식.
- **판단 예시**:
  - "server.py에 memory_store 함수가 있다" → 코드에서 읽을 수 있음 → 저장 금지
  - "memory_store에서 ChromaDB 대신 Pinecone을 쓰려다 비용 문제로 포기함" → 코드에 없음 → 저장 대상
  - "tests/ 폴더에 783개 테스트가 있다" → 실행하면 알 수 있음 → 저장 금지
  - "pytest가 dev 컨테이너에서만 돌려야 한다" → 코드에 없음 → 저장 대상
- **특히 반드시 저장** (코드에 절대 남지 않는 지식):
  - 서버 접속 정보: "staging SSH 포트 2222 (방화벽이 22 차단)"
  - 수동 절차: "마이그레이션 후 cache-invalidate 수동 호출 필수"
  - 외부 API 제약: "Groq API rate limit 30req/min, 초과 시 24h 차단"
  - 과거 사고 경험: "DB 0 flush로 847건 이메일 대기열 손실"
  - 환경 특이사항: "CI 서버 RAM 2GB, 전체 테스트 동시 실행 시 OOM"
- 각 항목은 대화 컨텍스트 없이 독립적으로 이해 가능하게 작성.
- type: `fact`(환경/인프라/선호) · `decision`(결정+이유) · `snippet`(배포명령/SSH/URL) · `summary`(세션요약)
- **글로벌 기억**: `is_global=True`로 모든 프로젝트에 공유 (선호/컨벤션/스타일)

## Importance (1.0~10.0)
| 구간 | 의미 | 예시 |
|------|------|------|
| 9.0+ | 잊으면 삽질. 항상 로드 | gotcha, 배포경로, SSH, 반복실수, 삭제된 기능 이유 |
| 5.0~8.9 | 있으면 도움. 시맨틱 검색 | 설계결정 이유, 팀 합의, 인프라 설정값 |
| <3.0 | 직접 설정 금지 (auto-saved 전용) | |

판단: "코드를 읽어도 알 수 없는가?" + "잊으면 삽질?" → 둘 다 Yes=9.0+ / 서버가 패턴 자동 보정

## 활용 규칙
1. **세션 시작**: auto_recall(mode='brief') → gotcha + 결정 이유 + 인프라 + 보유 리소스(API 키 등) 우선 로드
2. **대화 중**: `context_search(query='주제')` — 압축 포맷, 빠른 on-demand 검색
3. **상세 검색**: `search` — 전체 메타데이터 + 고급 필터
4. **미확인 메모**: `inbox` 또는 `/kd-inbox` — 외부 메모 리뷰
5. **충돌 방지**: 기존 결정과 다를 때 → 언급 + 변경 이유
6. **중복 방지**: 불확실하면 검색 먼저
7. **실패→학습**: 도구/API/명령 실패 후 다른 방법으로 성공 시 **즉시** gotcha 저장 (importance=9.0, tags=['gotcha','도구명']).
8. **실패→검색**: 실패 시 재시도 전에 `context_search(query='[도구명] gotcha')` 확인.
9. **실패→보고**: 도구/API/명령 실패 시 `report_failure(project, tool_name, error_summary, attempt)` 호출 **필수**. 서버가 관련 gotcha 반환 + 3회 시 STOP 지시.
10. **긴 세션 체크포인트**: 20+ 메시지 또는 주요 마일스톤 시 `summarize_session` 호출
11. **외부 문서 학습**: 워크스페이스(CWD) 외부의 문서 파일 위치가 확인되면 gotcha로 저장 (importance=9.0, tags=['gotcha','file-location']).
12. **리소스 확인**: API 키/설정/환경변수가 "없다"고 판단하기 전에 `context_search(query='API key .env 환경변수')` + 프로젝트의 `.env`, config 파일 확인 **필수**. 이미 있는 리소스를 모르고 재신청/재구현하는 낭비를 방지.
13. **워크스페이스 경계**: 명시적 지시 없이 CWD 외부 파일을 편집·삭제·생성하지 않는다. 읽기(참조)는 허용하되, 쓰기 작업은 반드시 사용자 확인 후 진행.

## 자동 동작
- 세션 시작 시 auto_recall, 세션 종료 시 로컬 JSONL 캐시 자동 저장
- 컨텍스트 부족 감지 시 저장 지시가 자동 표시됨 — 지시에 따라 수행
"""

# ── kd-activation ──────────────────────────────────────────────

COMMAND_PROMPTS["activation"] = """\
# Memory Activation 활용률 통계

Memory Activation 시스템의 효과를 보여주는 통계를 조회합니다.

## 실행 절차

1. **통계 조회**: `stats(project='{project}')` 도구를 호출하세요.

2. **활용률 API 조회**: 아래 형식으로 활용률 데이터를 가져오세요.
   - 서버 API: GET /api/stats/utilization?project={project}&days=7
   - Bash로 curl 호출: `curl -s "http://<server>:8321/api/stats/utilization?project={project}&days=7"`

3. **결과를 아래 포맷으로 사용자에게 보고**:

## Memory Activation 통계 — {project}

### 활용률 (Utilization Rate)
- **전체**: {{rate}}% ({{success}}/{{total}})
- **최근 7일**: {{recent_rate}}%

활용률은 주입된 기억이 실제로 행동에 반영된 비율입니다.
- 높을수록 기억 시스템이 잘 작동하는 것입니다.
- 낮은 경우 gotcha를 deny 모드로 승격하거나 표현을 개선해야 합니다.

### 일별 추이 (최근 7일)
| 날짜 | 주입 | 활용 | 무시 | 활용률 |
|------|------|------|------|--------|
| ... 데이터를 테이블로 표시 ... |

### 가장 많이 무시된 기억 (deny 승격 후보)
아래는 주입되었지만 가장 많이 무시된 기억입니다. deny 승격을 고려하세요.

1. "{{content}}" — 무시 {{N}}회, 활용률 {{rate}}%
2. ...

---

**참고**: 데이터가 아직 없으면 "아직 충분한 데이터가 수집되지 않았습니다. Memory Activation이 동작하면 자동으로 통계가 쌓입니다."로 안내하세요.
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMAND_PROMPTS["journal"] = """\
# 프로젝트별 일일 일지

## 인자: {arguments}

### 인자 없음 (오늘 일지 조회)
1. search(query='journal 오늘날짜', project='{project}', tags=['journal'], n_results=5) 호출
2. 결과 표시. 없으면 "오늘 일지 없음. /kd-journal sync로 생성하세요." 안내

### `sync` (전체 프로젝트 일지 일괄 생성 + 통합 보고)
1. Bash로 서버 API 호출:
   curl -sf -X POST "http://<server>:8321/api/journal-sync" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{"date": "오늘날짜"}}'
2. 응답의 created/skipped/already_exists 보고
3. 전체 일지 크로스 조회:
   search(query='journal 오늘날짜', cross_project=true, tags=['journal'], n_results=20)
4. 프로젝트별 통합 보고 + "내일 할 일" 종합

### `sync YYYY-MM-DD` 또는 `sync MM-DD`
위 sync와 동일하되 해당 날짜 사용

### `YYYY-MM-DD` 또는 `MM-DD` (특정 날짜 일지 조회)
search(query='journal 해당날짜', cross_project=true, tags=['journal'], n_results=20)
프로젝트별 분류하여 보고
"""

COMMAND_PROMPTS["visibility"] = """\
# 프로젝트 크로스 검색 가시성 제어

다른 프로젝트에서 이 프로젝트의 기억을 크로스 검색할 수 있는지 설정합니다.

## 인자: {arguments}

## 실행 절차

인자에 따라 분기:

### 인자 없음 (상태 확인)
1. 현재 프로젝트의 searchable 상태를 확인합니다.
2. Bash로 curl 호출: `curl -s -X PUT "http://<server>:8321/api/project-settings/{project}" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{}}'`
3. 응답의 `searchable` 값을 보고:
   - `true`: "이 프로젝트는 다른 프로젝트에서 검색 **가능**합니다."
   - `false`: "이 프로젝트는 다른 프로젝트에서 검색 **차단** 상태입니다."

### `on`
1. searchable을 true로 설정합니다.
2. `curl -s -X PUT "http://<server>:8321/api/project-settings/{project}" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{"searchable": true}}'`
3. "✅ 프로젝트 '{project}'가 크로스 검색 **허용**으로 설정되었습니다."

### `off`
1. searchable을 false로 설정합니다.
2. `curl -s -X PUT "http://<server>:8321/api/project-settings/{project}" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{"searchable": false}}'`
3. "🔒 프로젝트 '{project}'가 크로스 검색 **차단**으로 설정되었습니다."

### `list`
1. `list_projects()` 도구를 호출하여 전체 프로젝트 목록을 가져옵니다.
2. 각 프로젝트의 searchable 상태를 확인합니다 (기본값: 허용).
3. 표 형식으로 보고합니다.

### `all-on`
1. dry-run: Bash로 curl 호출:
   `curl -s -X PUT "http://<server>:8321/api/bulk-visibility" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{"searchable": true, "confirm": false}}'`
2. 결과의 would_update, would_skip, target_projects를 사용자에게 보여주고 확인 요청.
3. 사용자 확인 후 실행:
   `curl -s -X PUT "http://<server>:8321/api/bulk-visibility" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{"searchable": true, "confirm": true}}'`
4. 응답의 previous_state를 로컬 파일에 저장:
   Bash로 `echo '<previous_state JSON>' > ~/.claude/memory-cache/bulk_previous.json`
5. "✅ N개 프로젝트 searchable=on 완료. 되돌리려면 /kd-visibility restore" 안내.

### `all-off`
all-on과 동일하되 searchable: false. 로컬 파일도 동일하게 갱신.

### `restore`
1. Bash로 로컬 파일 읽기: `cat ~/.claude/memory-cache/bulk_previous.json`
2. 파일 없으면: "이전 벌크 변경 기록이 없습니다." 안내 후 종료.
3. 파일 있으면: 내용을 사용자에게 보여주고 확인 요청.
4. 확인 후 실행:
   `curl -s -X PUT "http://<server>:8321/api/bulk-visibility" -H "Content-Type: application/json" -H "Authorization: Bearer <key>" -d '{{"restore": <파일내용>, "confirm": true}}'`
5. restore 후 로컬 파일은 **덮어쓰지 않음** (원래 상태 보존).
6. "✅ N개 프로젝트 이전 상태로 복원 완료." 안내.
"""

VALID_COMMANDS = frozenset(COMMAND_PROMPTS.keys())


def get_command_prompt(
    command: str,
    arguments: str = "",
    project: str = "",
) -> dict[str, str]:
    """Return the full prompt for a slash command.

    Args:
        command: Command name (e.g., 'init', 'sync', 'kd-init', 'dm.init').
        arguments: User arguments ($ARGUMENTS substitution).
        project: Project ID for substitution.

    Returns:
        dict with 'content' key, or 'error' key if command not found.
    """
    # Normalize: strip 'dm.' or 'kd-' prefix if present
    cmd = re.sub(r"^(dm\.|kd-)", "", command.strip().lower())

    if cmd not in COMMAND_PROMPTS:
        available = ", ".join(sorted(f"kd-{c}" for c in COMMAND_PROMPTS))
        return {
            "error": f"Unknown command: '{command}'. Available: {available}",
        }

    prompt = COMMAND_PROMPTS[cmd]
    # Substitute {arguments} and {project}
    prompt = prompt.replace("{arguments}", arguments or "$ARGUMENTS")
    if project:
        prompt = prompt.replace("{project}", project)

    return {"content": prompt, "command": f"kd-{cmd}"}
