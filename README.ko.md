<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>AI 코딩 에이전트를 위한 영구 기억 서버를 직접 운영하세요.</b><br>
  싱글유저 모드. 데이터 완전 통제. 한 줄 명령으로 시작.
</p>

<p align="center">
  <a href="README.md">English</a> | <b>한국어</b> | <a href="README.ja.md">日本語</a> | <a href="README.de.md">Deutsch</a> | <a href="README.fr.md">Français</a> | <a href="README.es.md">Español</a> | <a href="README.pt.md">Português</a> | <a href="README.zh.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.11+-green" alt="Python">
  <img src="https://img.shields.io/badge/docker-compose-blue" alt="Docker">
  <img src="https://img.shields.io/badge/MCP_tools-13-brightgreen" alt="MCP Tools">
  <img src="https://img.shields.io/badge/ChromaDB-vector_store-orange" alt="ChromaDB">
  <img src="https://img.shields.io/badge/embeddings-50+_languages-purple" alt="Multilingual">
</p>

---

## 왜 필요한가

기억을 저장하고 검색하는 건 여러 곳에서 합니다.
Kandela가 푸는 문제는 그 다음 단계 — **AI가 기억을 가지고 삽질하지 않도록 제어하고, 프로젝트 간 지식이 자동으로 흐르게** 하는 것입니다.

### A/B 벤치마크 실측 결과

HIPAA 의료 데이터 파이프라인 시나리오(8세션, 14개 의사결정 트랩)를 Kandela ON/OFF로 비교:

| | Kandela ON | Kandela OFF | 차이 |
|---|:-:|:-:|:-:|
| **트랩 회피율** | **100%** | 11.9% | **+88.1pp** |
| **작업 시간** | 77.9분 | 86.6분 | **-10.1%** |
| **생성 코드** | 2,152줄 | 3,441줄 | **-37.5%** |
| **생성 파일** | 40개 | 62개 | **-35.5%** |

> 3회 반복(seeds=42,123,456), claude-sonnet-4-6, Groq Llama 3.3 70B (Operator).

**핵심 인사이트:**
- **코드에 없는 결정이 핵심**: 감사인 이름, OOM 사건, 데이터 손실 이력 등 코드를 읽어도 알 수 없는 정보를 기억
- **불필요한 코드 제거**: Kandela 없이는 이미 기각된 구현을 다시 만들어 37.5% 코드 낭비 발생

## 주요 기능

- **13개 MCP 도구**: 저장, 검색, 삭제, 수정, 자동 회상, on-demand 검색, Inbox, 프로젝트 관리 등
- **하이브리드 검색**: 시맨틱 + BM25 키워드 검색 (RRF 퓨전)
- **Importance 엔진**: 1~10 자동 점수 + 18개 규칙 기반 인프라 태깅
- **Lazy Retrieval**: brief 모드 (~260 tok) + `memory_context_search` on-demand 검색
- **세션 연속성**: 환경 변화 감지 (CWD, 호스트, 클라이언트) + 인프라 메모리 자동 포함
- **로컬 캐시 + Auto-Sync**: Stop Hook JSONL 캐시 → SessionStart 시 서버 자동 동기화
- **웹 대시보드**: 프로젝트별 메모리 조회, 검색, 통계, 성능 모니터링
- **원클릭 설치**: `curl ... | bash`로 Hooks + 슬래시 명령 자동 설치
- **Prompt Guard**: 오래된 기억 기반의 잘못된 결정 방지
- **Circuit Breaker**: 반복 실패 패턴 감지 + 자동 Gotcha 저장
- **크로스 프로젝트 가시성**: 프로젝트별 searchable 설정으로 크로스 검색 범위 제어
- **다국어 임베딩**: paraphrase-multilingual-MiniLM-L12-v2 (50+ 언어)

## 요구사항

- **Python >= 3.11**
- 첫 실행 시 임베딩 모델 자동 다운로드 (~449MB, 1~5분 소요)

## 5분 퀵스타트

### 방법 1: Docker (권장)

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### 방법 2: 로컬 설치

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### 클라이언트 연결 (Claude Code)

```bash
# 1. MCP 서버 등록
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Hooks + 슬래시 명령 자동 설치
curl -sf http://localhost:8321/api/install | bash

# 3. 프로젝트 초기화
/kd-init
```

### 클라이언트 연결 (Claude Desktop / Cursor)

`~/.claude.json` 또는 `.mcp.json`에 추가:

```json
{
  "mcpServers": {
    "memory": {
      "type": "http",
      "url": "http://localhost:8321/mcp"
    }
  }
}
```

> 설치 후 첫 대화에서 자동으로 기억 시스템이 활성화됩니다.

## 인증 (선택)

외부 네트워크에서 접근할 경우 API 키 인증을 권장합니다.

```bash
# .env에 설정
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

클라이언트 연결 시:
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> 로컬에서만 사용할 경우 인증 없이 사용 가능합니다.
> 외부에서 접근하려면 reverse proxy + 인증을 설정하세요.

## MCP 도구 (13개)

| 도구 | 설명 |
|------|------|
| `memory_store` | 기억 저장 (프로젝트, 내용, 타입, 태그, 중요도) |
| `memory_search` | 관련 기억 검색 (시맨틱, BM25 하이브리드, MMR, 필터) |
| `memory_context_search` | 대화 중 압축 검색 (~50 tok/건, on-demand) |
| `memory_delete` | 특정 기억 삭제 |
| `memory_update` | 기억 수정 (내용/타입/중요도/태그, 임베딩 자동 재계산) |
| `memory_inbox` | 미확인 메모 조회/확인 처리 |
| `memory_auto_recall` | 세션 시작 시 관련 기억 자동 로딩 (brief/full 모드) |
| `memory_summarize_session` | 현재 세션 요약 저장 |
| `memory_list_projects` | 등록된 프로젝트 목록 |
| `memory_stats` | 프로젝트별 기억 통계 |
| `memory_project_rename` | 프로젝트 이름 변경 |
| `memory_project_delete` | 프로젝트 삭제 (전체 기억 포함) |
| `memory_get_guide` | CLAUDE.md 가이드 템플릿 제공 |

## 웹 대시보드

`http://localhost:8321/dashboard`에서 접근 가능.

- 서버 상태, 메모리 통계, 스토리지 사용량
- 프로젝트별 메모리 목록 및 상세 조회
- 시맨틱 검색 (프로젝트 필터)
- 토큰 사용량 / ROI 분석
- 성능 모니터링 (endpoint별 p50/p95/p99)

## Hooks (Claude Code)

세션 시작/종료 시 자동으로 기억을 관리합니다:

| Hook | 이벤트 | 동작 |
|------|--------|------|
| SessionStart | 세션 시작 | `memory_auto_recall` 호출 |
| PreCompact | 컨텍스트 압축 전 | `memory_summarize_session` 호출 |
| Stop | 세션 종료 | 로컬 JSONL 캐시 저장 |

## 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `KANDELA_DB_PATH` | ChromaDB 저장 경로 | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | 임베딩 모델 이름 | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | HTTP 포트 | `8321` |
| `KANDELA_API_KEY` | API 키 (선택) | - |
| `KANDELA_REQUIRE_AUTH` | API 키 인증 강제 | `false` |
| `MCP_LOG_LEVEL` | 로그 레벨 | `INFO` |
| `HF_HOME` | HuggingFace 모델 캐시 경로 | `~/.cache/huggingface` |

## 프로젝트 구조

```
src/memory_mcp/
├── server.py          # MCP 서버 (13개 도구)
├── auth.py            # API 키 인증 미들웨어
├── dashboard.py       # REST API + 웹 대시보드
├── install.py         # 원클릭 설치 스크립트
├── db/
│   ├── store.py       # MemoryStore 핵심 CRUD (ChromaDB)
│   ├── bm25.py        # BM25 토크나이저 + 인덱스
│   ├── fusion.py      # RRF 퓨전 (시맨틱 + BM25)
│   └── session_env.py # 세션 환경 감지
├── importance/        # Importance 엔진 (규칙 + 점수)
├── templates/         # 가이드, 훅 프롬프트, 슬래시 명령
└── tools/models.py    # Pydantic 입력 모델
```

## 호스팅 서비스

셀프호스팅 외에 추가 기능이 포함된 호스팅 서비스도 제공합니다:
- 멀티유저 지원 + 계정 관리
- 텔레그램 봇 연동
- 원격 명령 (Remote Command)
- Activity 히트맵
- 티어별 기능 (Pro/Max)

자세한 내용: [kandela.ai](https://kandela.ai)

## 개발

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## 라이선스

- **서버**: [AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **클라이언트** (install.py가 생성하는 hooks, slash commands): [MIT](LICENSE-CLIENT)

## 면책조항

본 소프트웨어는 "있는 그대로(AS IS)" 제공되며, 명시적 또는 묵시적 보증 없이 사용됩니다.
사용자가 저장한 데이터의 백업은 사용자 본인의 책임입니다.
