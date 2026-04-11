# 클라이언트 설치 파일 IP 노출 분석

> 최종 업데이트: 2026-03-11

## 1. 분석 대상

사용자 머신에 설치되는 파일 (`install.py` INSTALL_VERSION 12 기준):

| 파일 유형 | 개수 | 설치 경로 |
|-----------|------|-----------|
| Hook 스크립트 (bash) | 5개 | `~/.claude/hooks/memory-*.sh` |
| 슬래시 명령 (markdown) | 16개 | `~/.claude/commands/dm.*/SKILL.md` |
| 설정 파일 | 2개 | `~/.claude/settings.json`, `~/.claude.json` |
| 메타데이터 | 2개 | `~/.claude/hooks/.memory-install-version`, `.memory-api-key` |

## 2. 노출되는 핵심 IP 요소

### 2.1 알고리즘/아키텍처 (HIGH 위험)

| 노출 항목 | 위치 | 경쟁 우위 위험도 |
|-----------|------|------------------|
| **적응형 컨텍스트 모니터링** — CPU 부하에 따른 4단계 폴링 간격 조절 (120s→60s→30s→10s) | `memory-context-monitor.sh` | ⚠️ HIGH |
| **위험 명령 감지 패턴** — 서비스 재시작/파괴적 명령/배포 명령 regex 분류 체계 | `memory-context-monitor.sh` | ⚠️ HIGH |
| **반복 실패 감지** — cksum 기반 명령 해시 + 30분 슬라이딩 윈도우 + 3회 임계값 | `memory-context-monitor.sh` | ⚠️ HIGH |
| **Importance 스코어링** — 9.0+/5.0~8.9 이원 체계, gotcha 시스템 | 슬래시 명령 전체 | ⚠️ MEDIUM |
| **Brief/Full 2단계 recall** — 토큰 절감을 위한 이중 모드 설계 | `memory-session-start.sh` | ⚠️ MEDIUM |
| **로컬 캐시 → 서버 배치 동기화** — JSONL + meta.json, 20건 배치, 100~3000자 필터 | `memory-session-start.sh` | ⚠️ MEDIUM |
| **3단계 프로젝트 감지** — 서버 workspace API → CLAUDE.md 탐색 → regex 추출 | `memory-session-start.sh` | ⚠️ MEDIUM |

### 2.2 API 엔드포인트 (MEDIUM 위험)

| 엔드포인트 | 노출 위치 | 파라미터 |
|------------|-----------|----------|
| `/api/health` | session-start hook | `guide_version`, `install_version` |
| `/api/workspaces` | session-start hook | 프로젝트-경로 매핑 JSON |
| `/api/cache-ingest` | session-start hook | `{project, entries}` + Bearer auth |
| `/api/hook-prompt/pre-compact` | pre-compact hook | `project=$PROJECT_ID` |
| `/api/hook-prompt/ops-warn` | context-monitor hook | `project=$ID&type=$TYPE` |
| `/api/hook-prompt/post-compact` | post-compact hook | `project=$PROJECT_ID` |

### 2.3 슬래시 명령 프롬프트 (HIGH 위험)

슬래시 명령은 Claude에 대한 **상세한 지시 프롬프트**를 포함하여 우리의 UX 설계 철학이 그대로 노출됨:

| 명령 | 노출되는 핵심 아이디어 |
|------|------------------------|
| `/dm.sync` | 10단계 JSONL 정제 파이프라인, 노이즈 제거 패턴, 중복 체크 전략 |
| `/dm.update` | 가이드 버전 마커 교체 + 기억 공백 분석 알고리즘 |
| `/dm.init` | 3-상태 CLAUDE.md 상태 머신, workspace 태깅 |
| `/dm.worker` | 자동 작업 폴링 (5분 간격, LaunchAgent/systemd) |
| `/dm.progress` | 종합 진행상황 보고서 구조화 |

### 2.4 운영 패턴 (LOW 위험)

- 에러 추적 상태 파일 경로: `/tmp/.memory-err-track-{project}`
- 컨텍스트 모니터 상태: `/tmp/.memory-ctx-monitor-{project}`
- 캐시 디렉토리 구조: `~/.claude/memory-cache/{project}/{session}.jsonl`

## 3. 보호 방안

### 3.1 기술적 방안

#### A. 서버사이드 로직 이동 (최우선)

**현재**: hook 스크립트에 알고리즘 로직이 bash로 하드코딩
**개선**: Dynamic Hook Prompt를 확장하여 핵심 로직을 서버 API로 이동

```
현재 흐름:
  Hook (bash 로직) → curl 서버 API → 결과 출력

개선 흐름:
  Hook (최소 셸) → curl 서버 API (입력 전송) → 서버에서 로직 처리 → 결과 반환
```

| 이동 대상 | 현재 위치 | 서버 API 이동 후 |
|-----------|-----------|-----------------|
| 위험 명령 regex 패턴 | context-monitor.sh | `/api/hook-prompt/ops-warn`에서 판단 |
| 반복 실패 감지 로직 | context-monitor.sh | `/api/hook-prompt/error-track` |
| 적응형 폴링 간격 계산 | context-monitor.sh | `/api/hook-prompt/ctx-interval` |
| 프로젝트 감지 로직 | session-start.sh | `/api/detect-project` |
| 캐시 필터링 (100~3000자) | session-start.sh | `/api/cache-ingest`에서 서버 필터 |

**효과**: hook 스크립트가 `curl`만 수행하는 thin client가 되어 알고리즘 노출 최소화

#### B. 슬래시 명령 서버 프롬프트화

**현재**: 16개 `.md` 파일에 상세 프롬프트가 로컬 설치
**개선**: 명령 실행 시 서버에서 프롬프트를 동적으로 가져오는 구조

```markdown
<!-- 현재 dm.sync/SKILL.md (200줄+) -->
상세 10단계 처리 지시...

<!-- 개선 후 dm.sync/SKILL.md (5줄) -->
`memory_get_command_prompt(command='sync', project='{project_id}')` 호출 후
반환된 지시를 따르세요.
```

**트레이드오프**: 오프라인 사용 불가, 서버 의존성 증가

#### C. 셸 스크립트 난독화 (보조적)

- `shc` (Shell Script Compiler)로 바이너리 변환
- 한계: 역컴파일 가능, 유지보수 복잡

#### D. MCP 도구 통합 (중기)

슬래시 명령 16개를 MCP 도구로 통합하면 프롬프트가 서버에만 존재:
- `/dm.init` → `memory_init_project` 도구
- `/dm.sync` → `memory_sync_cache` 도구
- 클라이언트에는 도구 이름만 노출, 실제 로직은 서버

### 3.2 행정적 방안

#### A. 이용약관 (Terms of Service)

베타 가입 시 동의 필수:
- 역공학(reverse engineering) 금지 조항
- 설치 파일의 2차 이용/복제/재배포 금지
- 경쟁 서비스 개발 목적 사용 금지

#### B. 특허 출원 (진행 중)

`patent_brainstorm/` 폴더에서 이미 진행 중인 특허:
- **특허 A**: 적응형 컨텍스트 기반 장기기억 시스템
- **특허 B**: LLM 코딩 도구의 세션 연속성 시스템
- 추가 후보: 위험 명령 감지 + 자동 gotcha 주입 방법

#### C. 방어적 공개 (Defensive Publication)

특허 출원이 어려운 항목에 대해:
- 적응형 폴링 간격 알고리즘
- 반복 실패 감지 + cksum 해시 기법
- JSONL 캐시 동기화 프로토콜

#### D. 영업비밀 관리

- 핵심 알고리즘 문서에 "CONFIDENTIAL" 표시
- 접근 권한 관리 (git 접근 제한)
- NDA 체결 (외부 협력 시)

### 3.3 우선순위 로드맵

| 순서 | 방안 | 효과 | 난이도 | 시점 |
|------|------|------|--------|------|
| 1 | 이용약관 작성 | 법적 보호 기반 | LOW | 베타 전 |
| 2 | 슬래시 명령 서버화 | 16개 프롬프트 노출 차단 | MEDIUM | 베타 후 1개월 |
| 3 | Hook 로직 서버 이동 | 5개 스크립트 알고리즘 보호 | MEDIUM | 베타 후 2개월 |
| 4 | 특허 추가 출원 | 법적 독점권 | HIGH | 진행 중 |
| 5 | MCP 도구 통합 | 완전 서버사이드 | HIGH | v1.0 |

## 4. 현실적 판단

**경쟁자가 파일을 분석해도 재현하기 어려운 이유:**
1. 알고리즘만으로는 부족 — ChromaDB 튜닝, Importance Rules 19개, BM25+RRF 하이브리드 검색의 실제 파라미터는 서버에만 존재
2. 슬래시 명령 프롬프트를 복사해도 서버 API 없이는 동작 불가
3. 핵심 가치는 축적된 기억 데이터와 사용 패턴에서 발생

**즉시 조치 필요 항목:**
- [x] context-monitor.sh의 regex 패턴 목록 → 서버 `/api/hook-prompt/ops-warn`으로 이미 이동 중
- [ ] 이용약관 초안 작성 (베타 전)
- [ ] 슬래시 명령 프롬프트 서버화 설계
