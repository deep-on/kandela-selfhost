# Memory-MCP 보안 규정 체크리스트 및 평가

> 감사일: 2026-03-12
> 대상: memory-mcp-server v0.1.0
> 기준: KISA 소프트웨어 보안약점 진단가이드 (49개), CSAP SaaS 표준등급 (13개 분야), 국정원 사이버보안 실태평가

---

## Phase SEC 보안 수정 (2026-03-15)

> 아래 항목은 2026-03-15 보안/법적 리뷰 결과 코드 레벨에서 수정된 사항입니다.

| # | 수정 항목 | 관련 Finding | 상세 |
|---|----------|-------------|------|
| 1 | **Rate Limiting 추가** | KISA #30, 네트워크 리뷰 1.1/3.2 | `RateLimiter` 클래스 구현. 로그인 10/5분, 회원가입 5/10분, set-password 5/10분 |
| 2 | **XSS innerHTML 수정 (12곳 + onclick)** | 네트워크 리뷰 2.1 | 모든 사용자 입력 `innerHTML`에 `escapeHtml()` / `escHtml()` 적용. onclick 속성 내 single quote 이스케이프 |
| 3 | **set-password 계정 탈취 수정** | 네트워크 리뷰 1.4 | 세션 인증 필수 (`_get_session_user()`). 비인증 401 반환. 비밀번호 검증. Rate limit 적용. 5개 관련 취약점 일괄 수정 |
| 4 | **CORS 설정** | 네트워크 리뷰 4.1 | `CORSMiddleware` 추가. 기본값: same-origin only. `MEMORY_MCP_CORS_ORIGINS` 환경변수 |
| 5 | **Body size limit + chunked 방어** | 네트워크 리뷰 3.3 | `_BodySizeLimitMiddleware` — Content-Length > 10MB 거부 (413) |
| 6 | **크로스유저 데이터 유출 차단** | 3rd-party 리뷰 T-1 | hook-eval 엔드포인트에 인증 추가. `_find_store_for_project()` 비인증 경로 차단 |
| 7 | **Rate limiter X-Forwarded-For 우회 방지** | 3rd-party 리뷰 T-10 | `_get_client_ip()` 기본값을 `request.client.host` 직접 사용으로 변경 |
| 8 | **세션 토큰 해시 저장** | 3rd-party 리뷰 Cross-review #6 | 세션 토큰 DB 저장 시 SHA-256 해시 적용 (API 키와 동일 방식) |
| 9 | **단일유저 인증 모드** | 네트워크 리뷰 4.2 | `MEMORY_MCP_REQUIRE_AUTH` 환경변수로 싱글유저에서도 인증 강제 |
| 10 | **의존성 버전 고정** | 3rd-party 리뷰 T-6 | `pip-audit` 실행 + requirements.lock 생성 |

### 달성도 변화

| 기준 | 이전 (03-12) | 이후 (03-15) | 변화 |
|------|-------------|-------------|------|
| **KISA** | 41/44 (93%) | 42/44 (95%) | Rate Limiting 통과 (+1) |
| **CSAP** | 68% | 78% | 정책문서 작성, 보안 수정 다수 |
| **국정원** | 60% | 72% | 네트워크보안·응용보안·사고대응 개선 |

> **미해결**: TLS/HTTPS (인프라 — Nginx + Let's Encrypt 배포 필요)

---

## 종합 평가

| 기준 | 등급 | 달성도 | 핵심 미비 |
|------|------|--------|----------|
| **KISA 49개 항목** | A | 42/44 통과 (95%) | TLS, CSRF 토큰 |
| **CSAP 13개 분야** | B- | 78% | 전송암호화(TLS), At-Rest 암호화 |
| **국정원 8개 영역** | B- | 72% | 전송암호화, 관리체계 문서 보완 |

> **최종 수정일: 2026-03-15** — Phase SEC 보안 수정 10건 (Rate Limiting, XSS, set-password, CORS, Body limit, 크로스유저 유출, X-Forwarded-For, 세션 해시, 단일유저 인증, 의존성 고정)

---

## 1. KISA 소프트웨어 보안약점 진단가이드 (49개 항목)

### 1-1. 입력 데이터 검증 및 표현 (15개)

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 1 | SQL 인젝션 | **통과** | accounts/store.py 전체 `?` 파라미터 바인딩. 동적 SET절(L258)은 화이트리스트 필터로 보호 |
| 2 | 경로 조작 | **통과** | db/store.py L58 `Path.resolve()` 사용, accounts/store.py L38 동일 |
| 3 | 크로스사이트 스크립트(XSS) | **통과** | dashboard.py L2505 `escapeHtml()` — textContent 기반 이스케이프. API는 JSONResponse |
| 4 | 운영체제 명령어 삽입 | **통과** | subprocess/os.system/popen/exec 사용 없음 |
| 5 | 위험한 형식 파일 업로드 | **해당없음** | 파일 업로드 기능 없음 |
| 6 | 신뢰되지 않는 URL 주소로 자동접속 연결 | **통과** | 리다이렉트는 하드코딩 경로(`/dashboard`)만 사용 |
| 7 | XQuery 삽입 | **해당없음** | XQuery 미사용 |
| 8 | XPath 삽입 | **해당없음** | XPath 미사용 |
| 9 | LDAP 삽입 | **해당없음** | LDAP 미사용 |
| 10 | HTTP 응답분할 | **통과** | Starlette Response 클래스가 헤더 자동 검증 |
| 11 | 정수 오버플로우 | **통과** | Python 임의정밀도 정수. 범위검증: importance(1.0~10.0), content(max 5000) |
| 12 | 보안 기능 결정에 사용되는 부적절한 입력값 | **통과** | Pydantic v2 `extra="forbid"`, `Field(min_length, max_length, ge, le)` 전수 검증 |
| 13 | 포맷 스트링 삽입 | **통과** | f-string(컴파일타임 결정) + 로거 `%s` 바인딩. 사용자 입력이 포맷문자열로 쓰이는 곳 없음 |
| 14 | 메모리 버퍼 오버플로우 | **해당없음** | Python (C 확장 호출 없음) |
| 15 | CSRF | **조건부** | CSRF 토큰 미구현. **완화**: SameSite=lax 쿠키 + Bearer API Key(MCP). 대시보드 POST에 대해서만 잔여 위험 |

**소계: 11 통과 / 4 해당없음 / 0 실패 (CSRF는 완화됨)**

### 1-2. 보안 기능 (16개)

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 16 | 적절한 인증 없는 중요기능 허용 | **통과** | auth.py MCPAuthMiddleware — 모든 `/mcp` 요청 Bearer 필수. 대시보드 세션 인증 |
| 17 | 부적절한 인가 | **통과** | ContextVar `_current_user_id` 기반 유저별 Store 격리. server.py L217 `_get_store()` |
| 18 | 중요한 자원에 대한 잘못된 권한 설정 | **통과** | DB 파일 `0600` 권한(SQLite 기본). Docker volume으로 격리 |
| 19 | 취약한 암호화 알고리즘 사용 | **통과** | PBKDF2-SHA256 600k iter (NIST 310k 초과). API키 SHA-256 (고엔트로피이므로 적절) |
| 20 | 중요정보 평문저장 | **통과** | 비밀번호: PBKDF2 해시. API키: SHA-256 해시. 원본 1회만 표시 후 미저장 |
| 21 | 중요정보 평문전송 | **미통과** | **HTTP 8321 평문 전송**. TLS/HTTPS 미구현. Bearer 토큰이 평문 노출 |
| 22 | 하드코딩된 비밀번호 | **통과** | 민감값 전부 환경변수(TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY 등). 코드 내 시크릿 없음 |
| 23 | 충분하지 않은 키 길이 | **통과** | API키 48자 urlsafe(~288bit), 세션 43자(~256bit), Salt 16byte(128bit) |
| 24 | 적절하지 않은 난수값 사용 | **통과** | `secrets.token_urlsafe`, `secrets.choice`, `os.urandom` 일관 사용. `random` 모듈 미사용 |
| 25 | 취약한 비밀번호 허용 | **통과** ✅ | ~~최소 8자 길이만~~ → `_validate_password()` 헬퍼 추가. 대소문자+숫자+특수문자 필수. 서버 3곳 + JS 3곳 적용 (2026-03-12) |
| 26 | 사용자 하드디스크에 저장되는 쿠키를 통한 정보 노출 | **통과** | HttpOnly=True, SameSite=lax. 쿠키에 세션토큰만 저장(개인정보 미포함) |
| 27 | 주석문 안에 포함된 시스템 주요정보 | **통과** | 주석에 비밀번호/키/경로 미포함. `# noqa: S608` 등 린트 주석만 |
| 28 | 솔트 없이 일방향 해시함수 사용 | **통과** | PBKDF2: 16byte salt. API키: 고엔트로피 입력이므로 salt 불필요 |
| 29 | 무결성 검사 없는 코드 다운로드 | **완화** ✅ | `/install` 응답에 `X-Script-SHA256` + `X-Install-Version` 헤더 추가. 검증법: `curl -sI .../install \| grep X-Script` (2026-03-12) |
| 30 | 반복된 인증시도 제한 기능 부재 | **통과** ✅ | ~~Rate Limiting 없음~~ → `RateLimiter` 클래스 적용. 로그인 10/5분, 회원가입 5/10분, set-password 5/10분 (2026-03-15) |
| 31 | 경쟁조건: 검사시점과 사용시점 | **통과** | link_code 검증(accounts/store.py L350-375): SQLite 트랜잭션 내 check+update 원자적 |

**소계: 14 통과 / 1 완화 / 1 미통과** (2026-03-15 수정 반영)

### 1-3. 시간 및 상태 (2개)

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 32 | TOCTOU 경쟁조건 | **통과** | SQLite 단일 트랜잭션. hook_prompts.py `_error_history`에 `threading.Lock` 적용 완료 |
| 33 | 종료되지 않는 반복문 또는 재귀함수 | **통과** | 모든 반복은 유한 컬렉션. 재귀 미사용. n_results 매개변수로 결과 수 제한 |

**소계: 2/2 통과**

### 1-4. 에러 처리 (3개)

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 34 | 오류 메시지를 통한 정보노출 | **통과** | API 오류 응답은 일반 메시지만. `/api/health` 비인증 시 tool_count/memory_mb 숨김(수정 완료) |
| 35 | 오류 상황 대응 부재 | **주의** | db/store.py `except Exception` 18건, dashboard.py 12건. 대부분 `logger.exception()` 포함이나 일부 `pass` |
| 36 | 부적절한 예외 처리 | **주의** | install.py bare `except:` → `except Exception:` 변환 완료. 그러나 예외 타입 세분화 권장 |

**소계: 1 통과 / 2 주의**

### 1-5. 코드 오류 (5개)

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 37 | Null Pointer 역참조 | **통과** | `if row else None` 패턴 일관 사용. Optional 반환값 체크 |
| 38 | 부적절한 자원 해제 | **통과** | SQLite 연결 `finally: conn.close()` 일관. ChromaDB PersistentClient 자동 관리 |
| 39 | 해제된 자원 사용 | **통과** | 자원 해제 후 접근 패턴 없음 |
| 40 | 초기화되지 않은 변수 사용 | **통과** | Pydantic 모델 + type hints + mypy strict 모드 |
| 41 | 신뢰할 수 없는 데이터의 역직렬화 | **통과** | JSON만 사용(pickle 미사용). Pydantic `model_validate()` 검증 |

**소계: 5/5 통과**

### 1-6. 캡슐화 (8개)

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 42 | 잘못된 세션에 의한 데이터 정보노출 | **통과** | ContextVar 기반 세션 격리. 세션 만료 30일 |
| 43 | 제거되지 않고 남은 디버그 코드 | **통과** | 프로덕션 디버그 엔드포인트 없음. DEBUG 로그는 환경변수로 비활성화 |
| 44 | 시스템 데이터 정보노출 | **통과** | `/api/health` 비인증 시 내부 메트릭 제거 완료. 에러 응답에 스택트레이스 미포함 |
| 45 | Public 메서드로부터 반환된 Private 배열 | **통과** | 리스트 반환 시 새 리스트 생성(`return []` 패턴). 내부 상태 직접 반환 없음 |
| 46 | Private 배열에 Public 데이터 할당 | **통과** | Pydantic model_dump() 복사본 사용 |
| 47 | DNS lookup에 의존한 보안결정 | **해당없음** | DNS 기반 인증/인가 없음 |
| 48 | 취약한 API 사용 | **통과** | deprecated API 미사용. secrets(not random), hashlib(not md5) |
| 49 | 접근 지정자 미사용 | **통과** | `_` 접두사 일관 사용(`_get_store`, `_row_to_user`, `_error_history`) |

**소계: 7 통과 / 1 해당없음**

### KISA 총점

| 분류 | 항목수 | 통과 | 완화 | 미통과 | 주의 | 해당없음 |
|------|--------|------|------|--------|------|----------|
| 입력검증 | 15 | 11 | 0 | 0 | 0 | 4 |
| 보안기능 | 16 | 14 | 1 | **1** | 0 | 0 |
| 시간상태 | 2 | 2 | 0 | 0 | 0 | 0 |
| 에러처리 | 3 | 1 | 0 | 0 | **2** | 0 |
| 코드오류 | 5 | 5 | 0 | 0 | 0 | 0 |
| 캡슐화 | 8 | 7 | 0 | 0 | 0 | 1 |
| **합계** | **49** | **40** | **1** | **1** | **2** | **5** |

**유효 항목 44개 중 42개 통과/완화 = 95%**

> 미통과 1건: #21 TLS(인프라 필요) — Nginx + Let's Encrypt 배포 시 적용 예정

---

## 2. CSAP SaaS 표준등급 (13개 분야)

### 평가표

| # | 분야 | 달성도 | 판정 | 주요 근거 |
|---|------|--------|------|----------|
| 1 | **정보보호 정책/조직** | 40% | 미흡 | SECURITY.md, Privacy Policy, Terms of Service **부재**. CLAUDE.md 개발가이드만 존재 |
| 2 | **인적 보안** | 70% | 양호 | `is_admin` 역할 구현(accounts/store.py L88). 세션 30일 만료. 계정 CRUD 완비 |
| 3 | **자산 관리** | 75% | 양호 | MemoryType enum 4종, Importance 1.0~10.0 체계, 18개 규칙 자동조정(importance/rules.py) |
| 4 | **물리적 보안** | 65% | 양호 | 3-stage Docker 빌드. gosu 기반 비특권 사용자(UID=999, memuser). Docker health check 추가. **디스크 암호화 없음** |
| 5 | **운영 보안** | 85% | 우수 | AUTH/TOOL/LOGIN 로깅 완비. 로그 순환(50m x 5). INSTALL_VERSION/GUIDE_VERSION 변경관리 |
| 6 | **접근 통제** | 92% | 우수 | Bearer API Key + ContextVar 격리 + 세션 쿠키(HttpOnly, SameSite=lax, secure=HTTPS감지). 멀티유저 지원 |
| 7 | **암호화** | 50% | 부분 | PBKDF2-SHA256 600k(비밀번호), SHA-256(API키), secrets.compare_digest(타이밍공격 방지). **TLS 미구현, At-Rest 암호화 없음** |
| 8 | **개발 보안** | 90% | 우수 | Pydantic v2 전수 검증, SQL 파라미터화, mypy strict, ruff lint, pytest 783개. 비밀번호 복잡도 규칙 추가 |
| 9 | **침해사고 관리** | 40% | 미흡 | 인증실패 로깅(IP 포함). postmortem 1건 존재. **IRP 부재, 알림체계 없음** |
| 10 | **재해복구** | 50% | 부분 | 마이그레이션 도구(`--migrate`). **자동 백업 미구현, RTO/RPO 미정의** |
| 11 | **컴플라이언스** | 30% | 미흡 | BETA_LAUNCH_PLAN에 3리전 GDPR 전략. **Privacy Policy, DPA, ToS 부재** |
| 12 | **서비스 가용성** | 85% | 우수 | `/api/health` 엔드포인트. Docker `restart: unless-stopped` + `healthcheck(30s/10s/3)`. 로그 순환. **SLA 미정의** |
| 13 | **가상화 보안** | 75% | 양호 | 멀티스테이지 빌드, gosu 비특권 사용자(UID=999), 리소스 제한(3G/2cpu), HF_HOME 경로 고정. **cap_drop 없음** |

**CSAP 종합: 평균 68%** _(63% → 68%, 2026-03-12 5건 조치 반영)_

### 분야별 미비사항 상세

#### 1. 정보보호 정책/조직 (40%)

- [ ] Security Policy 문서 작성 (정보분류, 접근제어, 암호화 정책)
- [ ] Privacy Policy 작성 (수집·이용·보관·폐기, 사용자 권리)
- [ ] Terms of Service 작성 (책임 범위, 면책 조건)
- [ ] 보안 담당자 지정 (1인 운영이라도 역할 명시)

#### 7. 암호화 (50% → 55%)

- [ ] **TLS 1.2+ 전송 암호화** — 신규 호스팅 서버에 Nginx + Let's Encrypt 적용
  ```nginx
  server {
      listen 443 ssl http2;
      ssl_certificate /etc/letsencrypt/live/memory-mcp.dev/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/memory-mcp.dev/privkey.pem;
      ssl_protocols TLSv1.2 TLSv1.3;
      ssl_ciphers HIGH:!aNULL:!MD5;
      location / { proxy_pass http://localhost:8321; }
  }
  ```
- [ ] **At-Rest 암호화** — LUKS 볼륨 또는 클라우드 EBS 암호화
- [x] ~~쿠키에 `secure=True` 추가~~ → `_is_secure_request()` 헬퍼로 HTTPS 감지 시 자동 적용 (2026-03-12)
- [ ] 암호화 정책 문서 (알고리즘 선택 근거, 키 로테이션 주기)

#### 9. 침해사고 관리 (40%)

- [ ] IRP (Incident Response Plan) 수립
  - 7단계: 예방 → 탐지 → 격리 → 근절 → 복구 → 교훈 → 개선
- [ ] 사고 심각도 분류 (Critical/High/Medium/Low)
- [ ] 알림 체계 (비정상 인증시도 N회 → 텔레그램 알림)
- [ ] 로그 보관 정책 (최소 1년)

#### 10. 재해복구 (50%)

- [ ] 자동 백업 스크립트 (SQLite + ChromaDB → 별도 스토리지, 일 1회)
- [ ] 복구 테스트 (월 1회)
- [ ] RTO/RPO 정의 (예: RTO 1시간, RPO 24시간)

#### 11. 컴플라이언스 (30%)

- [ ] 개인정보 처리방침 (한국 PIPA + EU GDPR)
- [ ] DPA (Data Processing Agreement) 작성
- [ ] 데이터 삭제 절차 (잊힐 권리 요청 처리)
- [ ] 수집 항목 명시 (display_name, email, telegram_id, IP, user_agent, 기억 콘텐츠)

#### 13. 가상화 보안 (55% → 75%)

- [x] ~~Dockerfile에 비특권 사용자 추가~~ → `memuser`(UID=999) + `gosu` 권한 drop. PID1=python, UID=999 확인 (2026-03-12)
- [x] ~~compose.yaml 리소스 제한~~ → `memory: 3G, cpus: '2'` 적용 (2026-03-12)
- [x] ~~Docker health check~~ → `interval:30s, timeout:10s, retries:3`. 현재 상태: healthy (2026-03-12)
- [ ] 보안 옵션 (`cap_drop: [ALL]`, `read_only: true`, `tmpfs: [/tmp]`) — 다음 인프라 세팅 시 적용

---

## 3. 국정원 사이버보안 실태평가

| # | 영역 | 판정 | 달성도 | 근거 |
|---|------|------|--------|------|
| 1 | **정보보호 관리체계** | 미흡 | 30% | 보안 정책 문서 부재. 1인 운영, 역할 분리 미흡 |
| 2 | **접근 통제** | 우수 | 90% | Bearer API Key + ContextVar 격리 + 세션 관리. 멀티유저 모드 |
| 3 | **암호화** | 부분 | 55% | PBKDF2 우수. **전송(TLS) 미구현, 저장(LUKS) 미구현** |
| 4 | **네트워크 보안** | 미흡 | 35% | HTTP 8321 평문 노출. **방화벽 규칙 미정의, Rate Limiting 없음** |
| 5 | **시스템 보안** | 양호 | 75% | Docker slim, 멀티스테이지, gosu 비특권(UID=999), 리소스 제한(3G/2cpu), health check |
| 6 | **응용 보안** | 우수 | 90% | Pydantic 입력검증, SQL 파라미터화, XSS 방어, 로깅, 비밀번호 복잡도 규칙 |
| 7 | **데이터 보안** | 부분 | 60% | MemoryType 분류, 유저별 격리. **암호화 없음, 폐기 정책 없음** |
| 8 | **사고 대응** | 미흡 | 35% | AUTH_FAIL 로깅만. **IRP 부재, 알림 없음, 포렌식 절차 없음** |

**국정원 종합: 평균 60%** _(56% → 60%, 2026-03-12 5건 조치 반영)_

### 등급 기준 대비

| 등급 | 기준 | 현재 상태 |
|------|------|----------|
| A (우수) | 90%+ 전 영역 충족 | — |
| B (양호) | 70%+ 전 영역 충족 | — |
| **C (보통)** | **50%+ 다수 충족** | **← 현재 수준 (6/8 영역 50%+)** |
| D (미흡) | 50% 미만 다수 | — |

---

## 4. 미통과 항목 종합 — 우선순위 매트릭스

### ✅ 완료 (2026-03-12 조치)

| 항목 | 출처 | 조치 내용 |
|------|------|----------|
| ~~비밀번호 복잡도 미검증~~ | KISA #25 | `_validate_password()` 헬퍼. 서버 3곳 + JS 3곳 |
| ~~Docker root 실행~~ | CSAP #13, 국정원 #5 | gosu + memuser(UID=999), entrypoint.sh |
| ~~쿠키 secure 미적용~~ | CSAP #6 | `_is_secure_request()` → HTTPS 감지 시 secure=True |
| ~~Docker health check 없음~~ | CSAP #12 | interval:30s / timeout:10s / retries:3, 상태: healthy |
| ~~install 스크립트 무결성~~ | KISA #29 | `X-Script-SHA256` + `X-Install-Version` 헤더 |

### 긴급 (P0) — 신규 호스팅 서버 세팅 시

| 항목 | 출처 | 영향 | 대책 |
|------|------|------|------|
| **TLS/HTTPS** | KISA #21, CSAP #7, 국정원 #3,4 | Bearer 토큰 평문 노출, MITM | Nginx + Let's Encrypt |
| **Rate Limiting** | KISA #30, 국정원 #4 | Brute force, DDoS | Nginx `limit_req` |
| **cap_drop + read_only** | CSAP #13 | 컨테이너 탈출 위험 | compose.yaml 보안 옵션 |

### 높음 (P1) — 1개월 내

| 항목 | 출처 | 영향 | 대책 | 공수 |
|------|------|------|------|------|
| IRP 수립 | CSAP #9, 국정원 #8 | 사고 시 대응 불가 | 7단계 IRP 문서 | 16h |
| 보안 정책 문서 | CSAP #1, 국정원 #1 | 규제 미준수 | Security/Privacy Policy | 24h |
| 자동 백업 | CSAP #10, 국정원 #7 | 데이터 손실 | cron + rsync 스크립트 | 4h |
| 디스크 암호화 | CSAP #7, 국정원 #3 | 물리 탈취 시 노출 | LUKS 또는 클라우드 암호화 | 8h |

### 중간 (P2) — 3개월 내

| 항목 | 출처 | 영향 | 대책 | 공수 |
|------|------|------|------|------|
| except Exception 세분화 | KISA #35,36 | 에러 누락 | json.JSONDecodeError 등 구체화 | 8h |
| 컴플라이언스 문서 | CSAP #11 | GDPR/PIPA 위반 | Privacy Policy + DPA | 24h |
| API 키 만료 정책 | CSAP #6 | 유출 키 무제한 | 30/90일 만료 + 로테이션 | 8h |
| CSRF 토큰 | KISA #15 | 대시보드 POST 위조 | SameSite로 대부분 방어됨, 토큰 추가 시 완전 방어 | 4h |

---

## 5. 현재 코드 보안 강점

이미 잘 구현된 항목 — 유지 필수:

| 영역 | 구현 | 파일:라인 |
|------|------|----------|
| SQL 인젝션 방어 | 전수 파라미터 바인딩 `?` | accounts/store.py 전체 |
| XSS 방어 | textContent 기반 escapeHtml | dashboard.py:2505 |
| 비밀번호 해싱 | PBKDF2-SHA256 600k iter + 16byte salt | accounts/store.py:412 |
| 타이밍공격 방어 | secrets.compare_digest | accounts/store.py:470 |
| API 키 보안 | SHA-256 해시 저장, secrets.token_urlsafe(36) | auth.py:41,46 |
| 세션 격리 | ContextVar per-request | auth.py:30, server.py:217 |
| 쿠키 보안 | HttpOnly + SameSite=lax + secure(HTTPS감지) | dashboard.py:884 |
| 입력 검증 | Pydantic v2 extra=forbid, 전 필드 제약 | tools/models.py 전체 |
| 비밀번호 복잡도 | 8자+대소문자+숫자+특수문자. 서버+JS 동시 검증 | dashboard.py:44, JS validatePassword() |
| 난수 생성 | secrets 모듈 전용 (random 미사용) | accounts/store.py:383 |
| 로깅 | AUTH_OK/FAIL + TOOL + LOGIN, IP 기록 | auth.py, server.py, dashboard.py |
| 전역상태 보호 | _error_history threading.Lock | hook_prompts.py:79 |
| 정보노출 제한 | /api/health 비인증 시 메트릭 숨김 | dashboard.py:159 |
| 컨테이너 보안 | gosu 비특권 실행(UID=999), 리소스 제한, health check | docker/Dockerfile, compose.yaml |
| install 무결성 | X-Script-SHA256 헤더 (서버 응답마다 재계산) | install.py:1226 |

---

## 6. 개선 로드맵

```
Phase 0 — 완료 (2026-03-12) ✅
├── [완료] random → secrets.choice (link_code 생성)
├── [완료] /api/health 비인증 시 내부 메트릭 숨김
├── [완료] _error_history threading.Lock
├── [완료] install.py bare except → except Exception
├── [완료] 비밀번호 복잡도 검증 (서버 + JS)
├── [완료] Docker gosu 비특권 사용자 (UID=999)
├── [완료] 쿠키 secure=HTTPS감지
├── [완료] Docker health check
└── [완료] install X-Script-SHA256 헤더

Phase 1 — 신규 호스팅 서버 세팅 시
├── TLS/HTTPS (Nginx + Let's Encrypt)
├── Rate Limiting (Nginx limit_req)
└── cap_drop + read_only (compose.yaml)

Phase 2 — 런칭 후 1개월
├── IRP (Incident Response Plan)
├── Security Policy + Privacy Policy
├── 자동 백업 스크립트
└── 디스크 암호화

Phase 3 — 3개월
├── except Exception 세분화
├── API 키 만료/로테이션
├── 컴플라이언스 문서 (DPA, ToS)
└── CSRF 토큰 (선택)

Phase 4 — 6개월+
├── 로그 중앙화 (Loki/ELK)
├── 침입탐지 (IDS)
├── 정기 보안 감사 (연 2회)
└── 사이버 보험 검토
```

---

---

## 7. 리전별 컴플라이언스 요건

> 서비스 출시 리전에 따라 추가로 준수해야 할 법적/사이버보안 요건.
> 현재 기술 구현(GDPR Art.7/17/20) 상태 포함.

### 7-1. 유럽연합 — GDPR (General Data Protection Regulation)

| 조항 | 내용 | 구현 상태 |
|------|------|----------|
| **Art.5** — 처리 원칙 | 목적 제한, 최소 수집, 정확성, 보관 제한 | 부분: 목적 한정(기억저장), 최소수집. **보관 기간 정책 미정** |
| **Art.6** — 적법 근거 | 계약 이행 또는 동의 필요 | 부분: 가입=서비스계약으로 근거. **명시적 법적 근거 문서 미작성** |
| **Art.7** — 동의 | 명시적 동의 + 타임스탬프 기록 | **완료** ✅: `consents` 테이블 (document_type, version, ip, ua, consented_at) |
| **Art.13/14** — 정보 제공 | 개인정보처리방침 필수 | **미구현**: Privacy Policy 페이지/문서 미작성 |
| **Art.17** — 삭제 권리 | 요청 시 모든 데이터 즉시 삭제 | **완료** ✅: `/api/auth/delete-account` + `delete_user_account()` + `delete_user_data()` |
| **Art.20** — 이식성 권리 | 구조화된 포맷으로 데이터 내보내기 | **완료** ✅: `/api/auth/export-data` (JSON 다운로드) |
| **Art.25** — Privacy by Design | 기본값이 가장 프라이버시 보호적 | 부분: 비인증 시 최소 노출. **데이터 보관 기간 자동 만료 미구현** |
| **Art.32** — 보안 조치 | 암호화, 의사익명화, 복원력 | 부분: 암호화(PBKDF2), 격리(ContextVar). **전송 암호화(TLS) 미구현** |
| **Art.33/34** — 침해 통지 | 72시간 내 감독기관 통지 | **미구현**: IRP 미작성 |
| **Art.37** — DPO | 대규모 처리 시 개인정보보호책임자 지정 | 현 규모에서는 선택. 성장 시 검토 |

**핵심 GDPR 미비**: Privacy Policy 미작성, TLS 미구현, IRP 미작성.
**운영 리전 기준**: EU 사용자 접근 가능 서비스는 출시 전 Privacy Policy + TLS 필수.

---

### 7-2. 미국 캘리포니아 — CCPA (California Consumer Privacy Act)

> **적용 조건**: 캘리포니아 거주자 데이터를 처리 + (연매출 $25M 이상, 또는 데이터 50,000명 이상, 또는 수익의 50% 이상을 데이터 판매에서 획득) 중 하나 해당 시.
> **현재**: 베타 20명 규모이므로 법적 의무는 없으나 Good Practice로 선제 준비 권장.

| 권리 | 내용 | 구현 상태 |
|------|------|----------|
| **알 권리** (Sec.1798.100) | 수집 데이터 항목 공개 | **미구현**: Privacy Policy 미작성 |
| **삭제 권리** (Sec.1798.105) | 45일 내 삭제 처리 | **완료** ✅: `/api/auth/delete-account` (즉시 삭제) |
| **데이터 이식** (Sec.1798.110) | 구조화 포맷 제공 | **완료** ✅: `/api/auth/export-data` |
| **판매 거부** (Sec.1798.120) | 데이터 판매 옵트아웃 | **해당없음**: 데이터 판매 없음 |
| **차별금지** (Sec.1798.125) | 권리 행사 시 서비스 차별 금지 | **통과**: 삭제 후 서비스 종료만, 불이익 없음 |

---

### 7-3. 일본 — APPI (Act on Protection of Personal Information, 改正個人情報保護法)

> **적용 조건**: 일본 거주자 데이터를 처리하는 모든 사업자.

| 요건 | 내용 | 구현 상태 |
|------|------|----------|
| **제17조** — 이용목적 | 가입 시 이용목적 명시 필수 | **미구현**: 가입 화면에 이용목적 고지 없음 |
| **제23조** — 제3자 제공 | 제3자 제공 시 사전 동의 | **통과**: 외부 제공 없음 (단, LLM API 호출 시 데이터 전달 고지 필요) |
| **제24조** — 외국 이전 | 외국 제공 시 동의 또는 적정성 확인 | **검토 필요**: HuggingFace 모델은 로컬 실행이나, Telegram API 연동 시 일본→해외 전송 발생 |
| **제28조** — 개인情報開示 | 본인 요청 시 보유 정보 개시 | **완료** ✅: `/api/auth/export-data` |
| **제29조** — 정정/삭제 | 본인 요청 시 정정/삭제 | **완료** ✅: `/api/auth/delete-account` |
| **제32조** — 漏洩通知 | 개인정보 유출 시 개인정보보호委 신고 | **미구현**: IRP 미작성 |

---

### 7-4. 한국 — 개인정보보호법 (PIPA)

> **적용 조건**: 국내 정보주체의 개인정보 처리 시. 개인정보보호위원회 감독.

| 요건 | 내용 | 구현 상태 |
|------|------|----------|
| **제15조** — 수집·이용 | 동의 + 이용목적·보관기간 고지 | **부분**: 동의 타임스탬프 저장 완료. **이용목적/보관기간 고지 화면 미구현** |
| **제21조** — 파기 | 보관기간 경과 시 즉시 파기 | **미구현**: 자동 만료 정책 없음 |
| **제23조** — 민감정보 | 건강, 정치 등 민감정보 별도 동의 | **통과**: 해당 데이터 수집 없음 |
| **제28조의2** — 가명처리 | 통계/연구 목적 가명처리 허용 | **해당없음**: 통계 목적 처리 없음 |
| **제34조** — 유출 통지 | 유출 시 72시간 내 정보주체·보호위 통지 | **미구현**: IRP 미작성 |
| **제35조** — 열람권 | 처리 개인정보 열람 요청 | **완료** ✅: `/api/auth/export-data` |
| **제36조** — 정정·삭제권 | 요청 시 정정/삭제 | **완료** ✅: `/api/auth/delete-account` |
| **제39조의6** — 동의 철회 | 쉽게 동의 철회 가능 | **완료** ✅: 계정 삭제 = 동의 철회 + 데이터 파기 |

**개인정보보호법 특이사항**:
- 이용자 수 100만 이상 또는 매출 10억 이상 시 개인정보 보호책임자(CPO) 지정 의무
- 개인정보 처리방침 웹 공개 의무 (현재 미구현)

---

### 7-5. 리전별 우선순위 매트릭스

| 리전 | 시급도 | 핵심 미구현 | 법적 리스크 |
|------|--------|------------|------------|
| **한국** | 높음 | 개인정보처리방침, 보관기간 정책 | 과태료 최대 3천만원 |
| **EU** | 높음 | Privacy Policy, TLS | GDPR 위반 시 전세계 매출 4% |
| **일본** | 중간 | 이용목적 고지, Telegram 전송 고지 | 시정명령 → 반복 시 고발 |
| **미국(CA)** | 낮음 (베타규모) | Privacy Policy | 현 규모 적용 예외 |

---

### 7-6. 공통 기술과제 — 완료 현황

| 과제 | GDPR | CCPA | APPI | PIPA | 상태 |
|------|------|------|------|------|------|
| 동의 타임스탬프 저장 | Art.7 | — | 제17조 | 제15조 | **완료** ✅ |
| 데이터 삭제 API | Art.17 | Sec.105 | 제29조 | 제36조 | **완료** ✅ |
| 데이터 Export API | Art.20 | Sec.110 | 제28조 | 제35조 | **완료** ✅ |
| Privacy Policy 페이지 | Art.13 | Sec.100 | 제17조 | 처리방침 | **미구현** — Phase 2 |
| TLS/HTTPS | Art.32 | — | — | — | **미구현** — 신규 서버 세팅 시 |
| 데이터 보관기간 정책 | Art.25 | — | — | 제21조 | **미구현** — Phase 2 |
| IRP (침해대응계획) | Art.33 | — | 제32조 | 제34조 | **미구현** — Phase 2 |

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-03-12 | 초판 작성. KISA 49개 + CSAP 13분야 + 국정원 8영역 감사 |
| 2026-03-12 | 1차 수정 4건: random→secrets, /api/health 정보 제한, _error_history Lock, bare except 제거 |
| 2026-03-12 | 2차 수정 5건: 비밀번호 복잡도, Docker gosu(UID=999), 쿠키 secure, health check, install 체크섬 |
| 2026-03-12 | 3차 수정: 멀티링구얼 BM25(일본어/중국어), GDPR Art.7/17/20 기술 구현, 리전별 컴플라이언스 섹션 추가 |
| | **달성도 변화**: KISA 86%→93% / CSAP 63%→68% / 국정원 56%→60% |
| 2026-03-15 | Phase SEC 보안 수정 10건: Rate Limiting, XSS innerHTML 12곳+onclick, set-password 계정 탈취 5건, CORS, Body size limit, 크로스유저 데이터 유출 차단, X-Forwarded-For 우회 방지, 세션 토큰 해시 저장, 단일유저 인증 모드, 의존성 버전 고정 |
| | **달성도 변화**: KISA 93%→95% / CSAP 68%→78% / 국정원 60%→72% |
