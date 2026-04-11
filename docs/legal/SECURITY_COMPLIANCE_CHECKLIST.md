# Memory MCP Server — 사이버보안 및 프라이버시 규정 준수 체크리스트

> 13개 프레임워크 기반 종합 점검
> 점검일: 2026-03-15
> 대상: memory-mcp-server (ChromaDB + FastMCP, 멀티유저 SaaS)
> 기존 감사: SECURITY_AUDIT.md (2026-03-12), NETWORK_SECURITY_REVIEW.md (2026-03-15), SECURITY_LEGAL_REVIEW_SUMMARY.md (2026-03-15)

---

## 적용 기준

### 국내 7종

| # | 프레임워크 | 버전/일자 | 비고 |
|---|-----------|----------|------|
| K1 | KISA 소프트웨어 보안약점 진단가이드 | 2021.11 (49개 항목) | 행안부·KISA 공동 발행 |
| K2 | KISA 모바일 대민서비스 보안 가이드 | 2021.10 | 웹 API 보안 항목 준용 |
| K3 | ISMS-P 인증기준 | 2024.07 개정 (101개: 관리체계 16 + 보호대책 64 + 개인정보 21) | 한국인터넷진흥원 |
| K4 | 개인정보의 안전성 확보조치 기준 안내서 | 2024.10 개정 | 개인정보보호위원회, 기존 기술적·관리적 보호조치 기준 통합 |
| K5 | 생성형AI 개발·활용 개인정보 처리 안내서 | 2025.08 | 개인정보보호위원회 |
| K6 | 모바일 전자정부 서비스 관리 지침 | 행안부예규 제328호 (2025.05) | API 보안 항목 준용 |
| K7 | KISA CSAP (클라우드서비스 보안인증) | SaaS 표준등급 (2024) | 13개 분야 |

### 글로벌 6종

| # | 프레임워크 | 버전 | 비고 |
|---|-----------|------|------|
| G1 | OWASP Top 10 | 2025 (A01~A10) | Web Application |
| G2 | OWASP API Security Top 10 | 2023 (API1~API10) | API 특화 |
| G3 | NIST SP 800-53 Rev.5 / CSF 2.0 | Rev.5 Update 1 (2024.02 CSF 2.0) | 연방 보안 통제 |
| G4 | ISO 27001:2022 Annex A | 93개 통제 (조직 37 + 인력 8 + 물리 14 + 기술 34) | 정보보안 관리체계 |
| G5 | CWE/SANS Top 25 | 2025 | 가장 위험한 소프트웨어 약점 |
| G6 | SOC 2 Type II | TSC 2017 (Security/Availability/Confidentiality/Privacy) | SaaS 데이터 보관 서비스 |

---

## 점검 결과 요약

> **최종 갱신: 2026-03-15** — Phase SEC 보안 수정 반영

| 등급 | 항목 수 | 내용 |
|------|---------|------|
| 🔴 즉시 조치 (Critical/High) | 5건 | TLS 미구현, 포트 바인딩, Slowloris, At-Rest 암호화, 자동 백업 |
| 🟠 론칭 전 권장 (Medium) | 16건 | CSRF 토큰, 세션 idle timeout, 컨테이너 하드닝, IRP, 개인정보처리방침 보완 등 |
| 🟡 출시 후 보완 (Low) | 14건 | 로그 PII, 쿠키 prefix, API 키 만료, 예외 세분화, 감사 로그 중앙화 등 |
| 🟢 양호 (Compliant) | 56건 | SQL 인젝션 방어, PBKDF2 해싱, Pydantic 입력검증, 유저별 격리, 동의 기록, Rate Limiting, CORS, XSS 방어, 세션 해시 등 |

**총 91건** (중복 근거 통합) — 2026-03-15 수정으로 🔴 12→5건, 🟢 47→56건

---

## 1. 인증 및 접근통제

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 1.1 | MCP API Bearer 토큰 인증 | K1 #16, G1 A07, G2 API2, G3 IA-2, G4 A.8.5, K3 2.5.1 | Bearer API 키 필수 (auth.py MCPAuthMiddleware). 멀티유저 모드에서 모든 /mcp 요청에 적용 | 🟢 | — |
| 1.2 | 대시보드 세션 인증 | K1 #16, G1 A07, G3 IA-2, K3 2.5.2 | 쿠키 기반 세션 (HttpOnly, SameSite=lax, conditional Secure). PBKDF2 비밀번호 인증 | 🟢 | — |
| 1.3 | 유저별 데이터 격리 | K1 #17, G1 A01, G2 API1, G3 AC-4, G4 A.8.3, K3 2.5.4 | ContextVar `_current_user_id` → UserStoreRegistry → 유저별 ChromaDB 컬렉션 격리 | 🟢 | — |
| 1.4 | 관리자 권한 분리 | K1 #17, G2 API5, G3 AC-5, G4 A.5.15, K3 2.5.3 | `is_admin` 플래그로 관리자 기능 분리. 초대코드 생성, 유저 목록 등 관리자 전용 | 🟢 | — |
| 1.5 | **비인증 API 엔드포인트 데이터 노출 (싱글유저 모드)** | G1 A01, G2 API5, G3 AC-3, G6 CC6.1, K7 #6 | `MEMORY_MCP_REQUIRE_AUTH` 환경변수로 싱글유저에서도 인증 강제 가능 (2026-03-15 수정) | 🟢 | 기 조치 완료 |
| 1.6 | **크로스유저 데이터 유출 (Hook 엔드포인트)** | G1 A01, G2 API1, G3 AC-4, G4 A.8.3, G6 CC6.1 | hook-eval 엔드포인트에 인증 추가. 비인증 경로에서 `_find_store_for_project()` 차단 (2026-03-15 수정) | 🟢 | 기 조치 완료 |
| 1.7 | **set-password 계정 탈취** | G1 A07, G2 API2, G3 IA-5, K1 #16 | 수정 완료: 세션 인증 필수로 변경 (2026-03-15) | 🟢 | 기 조치 완료 |
| 1.8 | **Rate Limiting — 인증 엔드포인트** | K1 #30, G1 A07, G2 API4, G3 AC-7, G5 CWE-307, K3 2.5.6 | RateLimiter 클래스 적용: 로그인 10/5분, 회원가입 5/10분, 비밀번호 설정 5/10분 | 🟢 | 기 조치 완료 |
| 1.9 | **Rate Limiting — 일반 API** | G2 API4, G3 SC-5, G5 CWE-770, K7 #4 | 일반 API 엔드포인트 (search, projects 등) rate limit 없음. Nginx 미배포 | 🟠 | Nginx limit_req 필요 (인증 엔드포인트는 조치 완료) |
| 1.10 | **X-Forwarded-For 스푸핑으로 Rate Limit 우회** | G2 API2, G3 SC-8, G5 CWE-346 | `_get_client_ip()` 기본값을 `request.client.host` 직접 사용으로 변경 (2026-03-15 수정) | 🟢 | 기 조치 완료 |
| 1.11 | 세션 만료 — 절대 타임아웃 | G3 AC-12, G4 A.8.1, K3 2.5.5 | 30일 절대 만료 구현 (SESSION_EXPIRY_DAYS=30) | 🟢 | — |
| 1.12 | **세션 유휴 타임아웃 미구현** | G3 AC-11, G4 A.8.1, K3 2.5.5, G6 CC6.1 | `last_active` 컬럼 있으나 유휴 타임아웃 미적용. 탈취 세션 30일간 유효 | 🟡 | idle timeout 7일 권장 |
| 1.13 | 비밀번호 복잡도 | K1 #25, G3 IA-5, G5 CWE-521, K4 제4조 | 8자 이상 + 대소문자 + 숫자 + 특수문자 필수. 서버+JS 이중 검증 | 🟢 | 기 조치 완료 |
| 1.14 | API 키 엔트로피 | K1 #23, G3 IA-5, K4 제7조 | `secrets.token_urlsafe(36)` = ~288bit 엔트로피. Brute force 불가능 | 🟢 | — |
| 1.15 | **API 키 만료/로테이션 미구현** | G3 IA-5(1), G4 A.5.17, G6 CC6.1, K3 2.5.7 | API 키 무기한 유효. 유출 시 수동 폐기만 가능. 자동 만료 없음 | 🟠 | 30/90일 만료 + 로테이션 권장 |
| 1.16 | **초대코드 Race Condition (이중 사용)** | K1 #31, G5 CWE-362, G3 SI-10 | verify_invite_code와 use_invite_code가 별도 트랜잭션. 동시 요청 시 하나의 코드로 2계정 생성 가능 | 🟠 | SECURITY_LEGAL_REVIEW T-2 |
| 1.17 | **초대/링크 코드 키스페이스** | G3 IA-5, G5 CWE-330 | 초대코드 8자 [A-Z0-9]=36^8≈2.8B, 링크코드 6자=36^6≈2.2B. 분산 공격 시 열거 가능 | 🟠 | 12자+ 또는 token_urlsafe 권장 |

---

## 2. 통신 보안

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 2.1 | **TLS/HTTPS 미구현** | K1 #21, K3 2.7.1, K4 제6조, K7 #7, G1 A04, G3 SC-8, G4 A.8.24, G6 CC6.7, K6 §12 | HTTP 8321 평문 전송. Bearer 토큰, 세션 쿠키, 비밀번호, 메모리 내용 평문 노출. MITM 공격 취약 | 🔴 | P0: Nginx + Let's Encrypt |
| 2.2 | **포트 전체 인터페이스 바인딩** | G3 SC-7, G4 A.8.20, K7 #4, K3 2.9.1 | `ports: "8321:8321"` → 0.0.0.0 바인딩. 공용 네트워크에 직접 노출 | 🔴 | 127.0.0.1:8321:8321로 변경 필요 |
| 2.3 | **CORS 정책** | G1 A05, G2 API8, G3 SC-8, K1 #6 | CORSMiddleware 적용 완료. 기본값: 추가 origin 없음 (same-origin only). MEMORY_MCP_CORS_ORIGINS 환경변수로 설정 가능 | 🟢 | 기 조치 완료 |
| 2.4 | **HTTP 요청 본문 크기 제한** | G2 API4, G3 SC-5, G5 CWE-770 | _BodySizeLimitMiddleware 적용 (Content-Length > 10MB 거부). 단, chunked transfer encoding 미검사 | 🟠 | Nginx client_max_body_size 보완 필요 |
| 2.5 | **Slowloris / 연결 고갈 공격** | G3 SC-5, G5 CWE-400, K7 #4 | Uvicorn 기본 설정만. 연결 수 제한 없음 | 🔴 | Nginx 리버스 프록시 배포 시 해결 |
| 2.6 | Stateless HTTP 전송 (MCP) | G2 API8, G3 SC-8 | `stateless_http=True` 사용. WebSocket/SSE 공격면 제거 | 🟢 | — |
| 2.7 | 쿠키 보안 플래그 | K1 #26, G3 SC-23, G4 A.8.12, K4 제4조 | HttpOnly=True, SameSite=lax, Secure=HTTPS감지 시 적용 | 🟢 | 기 조치 완료 |
| 2.8 | X-Content-Type-Options | G1 A05, G3 SC-8, K2 §4.2 | **미설정**. JSON 응답을 브라우저가 HTML로 해석할 가능성 | 🟡 | `nosniff` 헤더 추가 권장 |

---

## 3. 데이터 보호 및 암호화

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 3.1 | 비밀번호 해싱 | K1 #19,#28, G3 IA-5(1), G5 CWE-916, K4 제7조 | PBKDF2-SHA256, 600k iterations, 16byte salt. NIST SP 800-63B 기준 초과 | 🟢 | — |
| 3.2 | API 키 해시 저장 | K1 #20, G3 IA-5, G5 CWE-256 | SHA-256 해시만 DB 저장. 원본은 발급 시 1회만 표시 | 🟢 | 고엔트로피이므로 salt 불필요 |
| 3.3 | **세션 토큰 해시 저장** | K1 #20, G3 IA-5, G5 CWE-256, G6 CC6.1 | 세션 토큰 DB 저장 시 SHA-256 해시 적용 (API 키와 동일 방식) (2026-03-15 수정) | 🟢 | 기 조치 완료 |
| 3.4 | **At-Rest 암호화 미구현** | K4 제7조, K7 #7, G3 SC-28, G4 A.8.24, G6 CC6.1, K3 2.7.2 | ChromaDB + SQLite 파일이 Docker 볼륨에 평문 저장. 물리 접근 시 전체 데이터 노출 | 🔴 | LUKS 또는 클라우드 디스크 암호화 |
| 3.5 | 난수 생성기 | K1 #24, G5 CWE-330, G3 SC-13 | `secrets.token_urlsafe`, `secrets.choice`, `os.urandom` 일관 사용. `random` 모듈 미사용 | 🟢 | — |
| 3.6 | 타이밍 공격 방어 | G5 CWE-208, G3 SC-13 | `secrets.compare_digest()` 사용 (비밀번호 검증) | 🟢 | — |
| 3.7 | 하드코딩된 비밀값 없음 | K1 #22, G5 CWE-798, G3 IA-5 | 모든 민감값 환경변수 처리. 코드 내 시크릿 없음 | 🟢 | — |
| 3.8 | **데이터 보관기간 자동 만료 미구현** | K4 제9조, K3 3.4.2, G3 SI-12, G6 CC6.5 | Privacy Policy에 보관기간 명시했으나 자동 삭제 코드 미구현 | 🟠 | TTL 기반 자동 만료 필요 |
| 3.9 | **PII 로그 기록** | K4 제4조, G3 AU-3, G4 A.8.15, K3 2.9.4 | IP, 이메일, user_id 접두사가 로그에 기록됨. 로그 보관/접근 정책 미정의 | 🟠 | 이메일 해시/마스킹, 로그 보관 정책 필요 |
| 3.10 | **백업 보안** | K7 #10, G3 CP-9, G4 A.8.13, G6 CC6.1, K3 2.9.3 | 자동 백업 없음. Docker managed volume만 사용 | 🔴 | 일일 자동 백업 + 암호화 |
| 3.11 | 데이터 삭제 (잊힐 권리) | K3 3.5.1, G3 SI-12, K4 제11조 | `delete_user_account()` — 계정·세션·API키·동의·메모리 전체 cascade 삭제 완비 | 🟢 | — |
| 3.12 | **계정 삭제 시 대기자 명단 미삭제** | K3 3.5.1, K4 제11조, G3 SI-12 | waitlist 테이블(email, name, reason)이 계정 삭제 시 잔존 | 🟠 | SECURITY_LEGAL_REVIEW T-5 |
| 3.13 | 데이터 이식성 (Export) | K3 3.5.2, G3 AC-21, K4 제12조 | `/api/auth/export-data` JSON 다운로드 구현 | 🟠 | 메모리 콘텐츠 export 미포함 (GDPR Art.20 불완전) |

---

## 4. 입력 검증 및 인젝션 방어

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 4.1 | SQL 인젝션 | K1 #1, G1 A05, G5 CWE-89, G3 SI-10, K6 §12 | 전수 파라미터 바인딩 `?`. update_user() 동적 SET절은 화이트리스트 보호 | 🟢 | — |
| 4.2 | XSS (Cross-Site Scripting) | K1 #3, G1 A05, G5 CWE-79, G3 SI-10, K2 §4.3 | `escapeHtml()` / `escHtml()` 적용. innerHTML 25건 전수 감사 완료 | 🟢 | onclick 속성 self-XSS edge case는 LOW |
| 4.3 | 경로 조작 | K1 #2, G5 CWE-22, G3 SI-10 | `Path.resolve()` 사용. 경로 기반 입력 검증 | 🟢 | — |
| 4.4 | OS 명령어 삽입 | K1 #4, G5 CWE-78, G3 SI-10 | subprocess/os.system/exec 미사용 | 🟢 | — |
| 4.5 | Pydantic 입력 검증 | K1 #12, G1 A05, G2 API3, G3 SI-10, K3 2.8.1 | Pydantic v2 `extra="forbid"`, Field constraints 전수 적용. mypy strict | 🟢 | — |
| 4.6 | 역직렬화 | K1 #41, G1 A08, G5 CWE-502, G3 SI-10 | JSON만 사용 (pickle 미사용). Pydantic model_validate() 검증 | 🟢 | — |
| 4.7 | SSRF | G1 A01 (통합), G2 API7, G5 CWE-918, G3 SI-10 | 사용자 입력 URL 페치 없음 | 🟢 | — |
| 4.8 | Content-Type 검증 | G2 API8, G3 SI-10 | POST 엔드포인트에서 `request.json()` 사용. 명시적 Content-Type 검증 없음 | 🟡 | 415 응답 미들웨어 권장 |
| 4.9 | **CSRF 토큰 미구현** | K1 #15, G1 A01, G5 CWE-352, G3 SI-10, K2 §4.3 | SameSite=lax 쿠키로 대부분 완화. 그러나 서브도메인 공격, 구형 브라우저 미방어 | 🟠 | P2: 대시보드 POST에 CSRF 토큰 추가 |

---

## 5. 소프트웨어 공급망 보안

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 5.1 | **의존성 버전 고정** | G1 A03, G1 A08, G3 SA-12, G4 A.8.28, G5 CWE-1395 | `pip-audit` 실행 + requirements.lock 생성 (2026-03-15 수정) | 🟢 | 기 조치 완료 |
| 5.2 | **Docker 이미지 무결성** | G1 A03, G3 SA-12, G4 A.8.9 | 멀티스테이지 빌드 사용. 단, base 이미지 digest 고정 없음 | 🟠 | `FROM python:3.11-slim@sha256:...` 형태 권장 |
| 5.3 | install 스크립트 무결성 | K1 #29, G1 A08, G3 SI-7 | `X-Script-SHA256` + `X-Install-Version` 헤더 제공 | 🟢 | 기 조치 완료 |
| 5.4 | 취약한/폐기된 컴포넌트 | G1 A03, G3 RA-5, G4 A.8.8 | 정기적 의존성 취약점 스캔 미실시 | 🟠 | dependabot 또는 `pip-audit` CI 통합 권장 |

---

## 6. 보안 설정 및 인프라

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 6.1 | Docker 비특권 실행 | K7 #13, G3 CM-7, G4 A.8.2, K3 2.9.2 | gosu + memuser (UID=999). PID1=python | 🟢 | 기 조치 완료 |
| 6.2 | **Docker 보안 옵션 미적용** | K7 #13, G3 CM-7, G4 A.8.2, K3 2.9.2 | `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges:true`, `tmpfs: [/tmp]` 미적용 | 🟠 | compose.yaml 보안 옵션 추가 |
| 6.3 | Docker 리소스 제한 | K7 #13, G3 SC-5, G6 CC7.1 | memory: 3G, cpus: 2 적용 | 🟢 | — |
| 6.4 | Docker Health Check | K7 #12, G3 SI-6, G6 CC7.1 | `/api/health` 기반 healthcheck 설정 (30s/10s/3) | 🟢 | — |
| 6.5 | 로그 순환 | G3 AU-4, G4 A.8.15, K7 #5 | json-file 드라이버, max-size:50m, max-file:5 | 🟢 | — |
| 6.6 | **텔레그램 봇 root 실행** | K7 #13, G3 CM-7, G4 A.8.2 | telegram-bot 컨테이너에 비특권 사용자 미설정 | 🟡 | memuser 패턴 적용 권장 |
| 6.7 | 환경변수 시크릿 관리 | G3 IA-5, G4 A.5.33, G6 CC6.1 | .env 파일 기반 표준 Docker 패턴. `docker inspect`로 확인 가능 | 🟡 | 프로덕션: Docker Secrets 또는 Vault 권장 |
| 6.8 | 디버그 코드/엔드포인트 | K1 #43, G1 A02, G3 CM-7, K3 2.8.3 | 프로덕션 디버그 엔드포인트 없음. DEBUG 로그는 환경변수로 제어 | 🟢 | — |
| 6.9 | /api/health 정보 노출 | K1 #44, G1 A02, G2 API8 | 비인증 시 내부 메트릭 숨김 처리 완료 | 🟢 | 기 조치 완료 |

---

## 7. 에러 처리 및 로깅

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 7.1 | 오류 메시지 정보 노출 | K1 #34, G1 A02, G3 SI-11, G5 CWE-209, K3 2.8.2 | API 오류 응답은 일반 메시지. 스택트레이스 미포함 | 🟢 | — |
| 7.2 | **응답 데이터 누출** | G2 API3, G3 SI-11, G5 CWE-209 | 일부 에러에서 상태 정보 노출: "Password already set", "Email already registered: user@..." | 🟠 | 인증 관련 에러 일반화 필요 |
| 7.3 | **except Exception 남용** | K1 #35,#36, G5 CWE-755, G3 SI-11 | db/store.py 18건, dashboard.py 12건의 광범위 예외 처리. 대부분 로깅 포함이나 일부 pass | 🟡 | 예외 타입 세분화 권장 |
| 7.4 | 인증 실패 로깅 | G3 AU-2, G4 A.8.15, K3 2.9.4, G6 CC7.2, K1 #30, G1 A09 | AUTH_OK/FAIL, LOGIN_FAIL/OK 이벤트 IP 포함 로깅 | 🟢 | — |
| 7.5 | MCP 도구 호출 로깅 | G3 AU-2, G4 A.8.15, K3 2.9.4 | TOOL 이벤트 로깅 (도구명, 프로젝트, 소요시간) | 🟢 | — |
| 7.6 | **API 키 접두사 로그 노출** | G3 AU-3, G5 CWE-532, K1 #20 | auth.py: api_key[:8], store.py: raw_key[:12] 로그 기록. 부분 시크릿 노출 | 🟡 | 4자로 축소 또는 해시 기반 식별자 |
| 7.7 | **로그 중앙화/장기 보관 미구현** | K7 #9, G3 AU-6, G4 A.8.15, G6 CC7.2, K3 2.9.4 | 로컬 파일 로깅만. 중앙 수집/분석 없음. 보관 기간 정책 미정의 | 🟡 | Loki/ELK 도입 권장 (Phase 4) |
| 7.8 | **보안 이벤트 알림 체계 없음** | K7 #9, G3 IR-4, G4 A.5.25, G6 CC7.3, K3 2.11.2 | 비정상 인증시도 N회 시 관리자 알림 없음. 텔레그램 봇 알림 미연동 | 🟠 | 임계값 초과 시 텔레그램 알림 권장 |

---

## 8. 개인정보보호 및 컴플라이언스

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 8.1 | 개인정보처리방침 | K3 3.1.1, K4 제3조, GDPR Art.13/14, PIPA 제30조 | `docs/PRIVACY_POLICY.md` + `/docs/privacy-policy` HTML 제공. 11개 조문 | 🟢 | docs/ 버전 placeholder 미치환 상태 |
| 8.2 | 이용약관 | K3 3.1.1, PIPA 제15조 | `docs/TERMS_OF_SERVICE.md` + `/docs/terms` HTML 제공 | 🟢 | — |
| 8.3 | 동의 체크박스 | K3 3.1.2, K4 제3조, GDPR Art.7, PIPA 제15조 | 가입 시 개인정보처리방침 + 이용약관 동의 필수. HTML+JS+백엔드 3중 검증 | 🟢 | — |
| 8.4 | 동의 기록 저장 | GDPR Art.7, K3 3.1.2, K4 제3조 | `consents` 테이블: user_id, document_type, version, ip, user_agent, consented_at | 🟢 | — |
| 8.5 | 개인정보보호책임자(DPO) | K3 1.1.3, GDPR Art.37, PIPA 제31조 | HTML 버전에 실명+이메일 기재. docs/ 버전은 placeholder | 🟠 | docs/ placeholder 치환 필요 |
| 8.6 | **데이터 유출 통지 절차** | GDPR Art.33/34, PIPA 제34조, K3 2.11.5, G3 IR-6, G6 CC7.3 | Privacy Policy에 침해사고 통지 조항 추가 + `docs/DATA_BREACH_PROCEDURE.md` 작성 (2026-03-15) | 🟢 | 기 조치 완료 |
| 8.7 | **IRP (침해사고 대응계획)** | K7 #9, K3 2.11.1, G3 IR-1, G4 A.5.24, G6 CC7.4 | `docs/DATA_BREACH_PROCEDURE.md` 5단계 IRP 수립 + Privacy Policy 통지 조항 포함 (2026-03-15) | 🟢 | 기 조치 완료 |
| 8.8 | 수집 항목 명시 | K3 3.1.1, K4 제3조, PIPA 제15조 | Privacy Policy에 수집 항목 명시: display_name, email, telegram_id, IP, user_agent, 기억 콘텐츠 | 🟢 | — |
| 8.9 | **보관기간 자동 파기 미구현** | K3 3.4.2, K4 제9조, PIPA 제21조, GDPR Art.5(1)(e) | 정책상 보관기간 명시했으나 자동 삭제 로직 미구현 | 🟠 | = 3.8 중복 |
| 8.10 | 제3자 제공/위탁 명시 | K3 3.2.1, PIPA 제26조 | Privacy Policy에 Oracle Cloud 위탁 명시 | 🟢 | — |
| 8.11 | **연령 확인 미구현** | PIPA 제22조, K3 3.1.3 | ToS에 14세 이상 조건 명시했으나 가입 시 실제 확인 없음 | 🟡 | "14세 이상입니다" 체크박스 추가 권장 |
| 8.12 | 사업자 정보 공개 | 전자상거래법 제13조, K3 3.1.1 | `docs/OPERATOR_INFO.md` + `/docs/operator-info` HTML 제공 | 🟢 | docs/ 버전 placeholder 미치환 |
| 8.13 | **메모리 콘텐츠 Export 미포함** | GDPR Art.20, PIPA 제35조, K3 3.5.2 | export_user_data()가 계정 메타데이터만 export. ChromaDB 메모리 콘텐츠 미포함 | 🟠 | 데이터 이식성 불완전 |

---

## 9. 생성형AI 개인정보 처리 (K5 특화)

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 9.1 | AI 학습 데이터 분리 | K5 §3 (AI 학습 단계) | 사용자 메모리 데이터를 AI 학습에 사용하지 않음. 임베딩 모델은 사전학습된 모델 로컬 실행 | 🟢 | — |
| 9.2 | 외부 LLM API 데이터 전송 고지 | K5 §4 (시스템 적용), APPI 제24조 | 텔레그램 봇이 Groq/Gemini/Anthropic API로 사용자 메시지 전송. Privacy Policy에 외부 API 전송 고지 | 🟢 | LLM 프로바이더별 DPA 검토 필요 |
| 9.3 | 임베딩 모델 로컬 실행 | K5 §3, G3 SC-8 | paraphrase-multilingual-MiniLM-L12-v2 로컬 실행. 외부 API 호출 없음 | 🟢 | — |
| 9.4 | **텔레그램 봇 오픈 모드 리소스 남용** | K5 §4, G2 API4, G5 CWE-770 | 멀티유저 모드에서 ALLOWED_TELEGRAM_USERS 미설정 시 모든 Telegram 유저 메시지 수신 → LLM API 비용 소진 | 🟠 | fail-closed 정책 필요 |

---

## 10. 가용성 및 재해복구

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 10.1 | Health Check | K7 #12, G3 SI-6, G6 CC7.1 | `/api/health` + Docker healthcheck (30s/10s/3) | 🟢 | — |
| 10.2 | 자동 재시작 | G3 CP-10, G6 CC7.1 | `restart: unless-stopped` | 🟢 | — |
| 10.3 | **자동 백업 미구현** | K7 #10, K3 2.9.3, G3 CP-9, G4 A.8.13, G6 CC6.1 | SQLite + ChromaDB 자동 백업 없음. Docker managed volume만 사용 | 🔴 | = 3.10 중복 |
| 10.4 | **RTO/RPO 미정의** | K7 #10, G3 CP-2, G6 CC7.5, K3 2.12.1 | 복구 목표 시간/지점 미정의. 복구 테스트 미실시 | 🟠 | RTO 1h / RPO 24h 정의 + 월 1회 복구 테스트 |
| 10.5 | **SLA 미정의** | G6 CC2.2, K7 #12, K3 2.12.2 | 서비스 수준 약정 문서 없음 | 🟡 | 베타 면책 조항 존재하나 정식 SLA 필요 |
| 10.6 | 마이그레이션 도구 | G3 CP-10, K3 2.9.3 | `--migrate` CLI 옵션으로 DB 마이그레이션 지원 | 🟢 | — |

---

## 11. 보안 정책 및 관리체계

| # | 항목 | 근거 | 현황 | 등급 | 비고 |
|---|------|------|------|------|------|
| 11.1 | **정보보호 정책 문서** | K3 1.2.1, K7 #1, G3 PL-1, G4 A.5.1, G6 CC1.1 | SECURITY_AUDIT.md + SECURITY_COMPLIANCE_CHECKLIST.md + docs/SECURITY_POLICY.md 작성 (2026-03-15) | 🟢 | 기 조치 완료 |
| 11.2 | **보안 담당자 미지정** | K3 1.1.2, K7 #1, G3 PM-2, G4 A.6.1, G6 CC1.1 | 1인 운영. 보안 담당 역할 미명시 | 🟠 | 1인이라도 역할 문서화 필요 |
| 11.3 | **변경 관리 절차** | K3 2.9.1, G3 CM-3, G4 A.8.32, G6 CC8.1 | INSTALL_VERSION/GUIDE_VERSION으로 일부 추적. 정식 변경 관리 절차 없음 | 🟡 | Git tag + CHANGELOG 기반 관리 권장 |
| 11.4 | **정기 보안 감사 미실시** | K3 1.4.1, G3 CA-2, G4 A.5.35, G6 CC4.1 | SECURITY_AUDIT.md (1회 실시). 정기 감사 일정 없음 | 🟡 | 연 2회 보안 감사 권장 |
| 11.5 | 취약점 관리 프로세스 | K3 2.8.4, G3 RA-5, G4 A.8.8, G6 CC7.1 | ruff lint, mypy strict, pytest 783개. 단, 정기 취약점 스캔 미실시 | 🟡 | pip-audit + Trivy CI 통합 |

---

## 12. SOC 2 Type II 특화 점검

| # | 항목 | 근거 (TSC) | 현황 | 등급 | 비고 |
|---|------|-----------|------|------|------|
| 12.1 | **Security (CC6)**: 논리적 접근 통제 | CC6.1~CC6.8 | Bearer API 키 + 세션 인증 + 유저별 격리. 단, TLS 없이 전송 보안 미비 | 🟠 | TLS 배포 시 양호 |
| 12.2 | **Availability (A1)**: 서비스 가용성 | A1.1~A1.3 | Docker healthcheck + restart + 리소스 제한. 단, 백업/RTO/RPO 미정의 | 🟠 | 백업 + SLA 필요 |
| 12.3 | **Confidentiality (C1)**: 기밀성 | C1.1~C1.2 | 유저별 데이터 격리 + 인증. 단, at-rest 암호화 없음 | 🟠 | 디스크 암호화 필요 |
| 12.4 | **Privacy (P)**: 개인정보 | P1~P8 | Privacy Policy + 동의 기록 + 삭제/export API. 단, 데이터 보관기간 자동 파기 미구현 | 🟠 | 자동 만료 + export 보완 |
| 12.5 | **Processing Integrity (PI1)**: 처리 무결성 | PI1.1~PI1.5 | Pydantic 입력 검증 + ChromaDB CRUD 정합성. 자동화된 무결성 검증 없음 | 🟡 | 데이터 정합성 자동 체크 권장 |

---

## 우선순위별 조치 로드맵

### Phase 0 — 즉시 조치 (🔴 원 12건 → 잔여 5건, 2026-03-15 갱신)

| 항목 | 참조 | 상태 | 비고 |
|------|------|------|------|
| TLS/HTTPS (2.1) | K1#21, G1A04, G3SC-8 | 🔴 미해결 | Nginx + Let's Encrypt 배포 필요 |
| 포트 바인딩 (2.2) | G3SC-7 | 🔴 미해결 | Nginx 배포 시 127.0.0.1로 변경 |
| ~~크로스유저 데이터 유출 (1.6)~~ | G1A01, G2API1 | ✅ 해결 | hook 엔드포인트 인증 추가 (2026-03-15) |
| ~~비인증 API 접근 (1.5)~~ | G1A01, G6CC6.1 | ✅ 해결 | MEMORY_MCP_REQUIRE_AUTH 환경변수 (2026-03-15) |
| ~~Rate Limiting — 일반 API (1.9)~~ | G2API4, G5CWE-770 | 🟠 부분 | 인증 엔드포인트 완료, 일반 API는 Nginx 필요 |
| Slowloris (2.5) | G3SC-5 | 🔴 미해결 | Nginx 배포 시 자동 해결 |
| At-Rest 암호화 (3.4) | K4제7조, G3SC-28 | 🔴 미해결 | LUKS 또는 클라우드 디스크 암호화 |
| 자동 백업 (3.10/10.3) | K7#10, G3CP-9 | 🔴 미해결 | cron + rsync + 암호화 백업 스크립트 |
| ~~IRP 수립 (8.7)~~ | K7#9, G3IR-1, G4A.5.24 | ✅ 해결 | DATA_BREACH_PROCEDURE.md (2026-03-15) |
| ~~데이터 유출 통지 절차 (8.6)~~ | GDPR Art.33, PIPA제34조 | ✅ 해결 | Privacy Policy 통지 조항 추가 (2026-03-15) |
| ~~정보보호 정책 (11.1)~~ | K3 1.2.1, G4A.5.1 | ✅ 해결 | 보안 정책 문서 다수 작성 (2026-03-15) |
| ~~의존성 버전 고정 (5.1)~~ | G1A03, G3SA-12 | ✅ 해결 | pip-audit + requirements.lock (2026-03-15) |

### Phase 1 — 론칭 전 권장 (🟠 18건, 1개월 내)

| 항목 | 참조 | 공수 |
|------|------|------|
| CSRF 토큰 (4.9) | K1#15, G5CWE-352 | 4h |
| 세션 토큰 해시 저장 (3.3) | G5CWE-256 | 2h |
| 세션 idle timeout (1.12) | G3AC-11 | 2h |
| Docker 보안 옵션 (6.2) | K7#13, G3CM-7 | 1h |
| 초대코드 Race Condition (1.16) | K1#31, G5CWE-362 | 2h |
| 초대코드 엔트로피 (1.17) | G3IA-5 | 1h |
| API 키 만료 (1.15) | G3IA-5, G6CC6.1 | 8h |
| 응답 데이터 누출 (7.2) | G2API3, G5CWE-209 | 2h |
| 보안 이벤트 알림 (7.8) | K7#9, G3IR-4 | 4h |
| 데이터 보관기간 자동 파기 (3.8/8.9) | K4제9조, PIPA제21조 | 8h |
| PII 로그 마스킹 (3.9) | K4제4조, G3AU-3 | 4h |
| 대기자 명단 삭제 (3.12) | K3 3.5.1 | 1h |
| 메모리 콘텐츠 Export (8.13) | GDPR Art.20 | 4h |
| DPO placeholder 치환 (8.5) | K3 1.1.3 | 0.5h |
| X-Forwarded-For 스푸핑 (1.10) | G5CWE-346 | 0.5h |
| HTTP body chunked 검증 (2.4) | G2API4 | 1h |
| 텔레그램 봇 오픈 모드 (9.4) | G2API4 | 2h |
| RTO/RPO 정의 (10.4) | K7#10, G3CP-2 | 4h |

### Phase 2 — 출시 후 보완 (🟡 14건, 3개월 내)

| 항목 | 참조 | 공수 |
|------|------|------|
| except 세분화 (7.3) | K1#35, G5CWE-755 | 8h |
| API 키 접두사 로그 (7.6) | G5CWE-532 | 1h |
| 로그 중앙화 (7.7) | G3AU-6, G6CC7.2 | 16h |
| X-Content-Type-Options (2.8) | G1A05 | 0.5h |
| Content-Type 검증 (4.8) | G2API8 | 2h |
| 텔레그램 봇 비특권 (6.6) | K7#13 | 1h |
| 시크릿 관리 고도화 (6.7) | G3IA-5, G6CC6.1 | 8h |
| 연령 확인 (8.11) | PIPA제22조 | 1h |
| SLA 정의 (10.5) | G6CC2.2 | 4h |
| 변경 관리 (11.3) | K3 2.9.1, G3CM-3 | 4h |
| 정기 보안 감사 (11.4) | K3 1.4.1, G3CA-2 | 연 2회 |
| 취약점 스캔 CI (11.5/5.4) | G3RA-5, G4A.8.8 | 4h |
| Docker 이미지 고정 (5.2) | G1A03 | 1h |
| 처리 무결성 체크 (12.5) | SOC2 PI1 | 8h |

---

## 프레임워크별 커버리지 매핑

### OWASP Top 10:2025 커버리지

| OWASP | 항목 | 관련 체크 | 상태 |
|-------|------|----------|------|
| A01 | Broken Access Control | 1.1~1.6, 4.9 | 🟠 (1.5, 1.6 미해결) |
| A02 | Security Misconfiguration | 6.1~6.9 | 🟠 (6.2 미적용) |
| A03 | Software Supply Chain Failures | 5.1~5.4 | 🔴 (5.1 lockfile 없음) |
| A04 | Cryptographic Failures | 2.1, 3.1~3.7 | 🔴 (TLS 미구현) |
| A05 | Injection | 4.1~4.8 | 🟢 |
| A06 | Insecure Design | 전반 | 🟢 (Pydantic, ContextVar 설계) |
| A07 | Authentication Failures | 1.7~1.17 | 🟠 (Rate limit 부분) |
| A08 | Software/Data Integrity Failures | 4.6, 5.3 | 🟢 |
| A09 | Security Logging & Alerting Failures | 7.1~7.8 | 🟠 (알림 체계 없음) |
| A10 | Mishandling of Exceptional Conditions | 7.3 | 🟡 |

### OWASP API Security Top 10:2023 커버리지

| OWASP API | 항목 | 관련 체크 | 상태 |
|-----------|------|----------|------|
| API1 | Broken Object Level Authorization | 1.3, 1.6 | 🔴 (크로스유저 유출) |
| API2 | Broken Authentication | 1.1, 1.7~1.8 | 🟢 |
| API3 | Broken Object Property Level Authorization | 4.5, 7.2 | 🟠 |
| API4 | Unrestricted Resource Consumption | 1.9, 2.4, 2.5, 9.4 | 🔴 |
| API5 | Broken Function Level Authorization | 1.4, 1.5 | 🟠 |
| API6 | Unrestricted Access to Sensitive Business Flows | 1.5 | 🔴 |
| API7 | Server Side Request Forgery | 4.7 | 🟢 |
| API8 | Security Misconfiguration | 6.1~6.9, 2.3 | 🟠 |
| API9 | Improper Inventory Management | — | 🟢 (단일 버전 API) |
| API10 | Unsafe Consumption of APIs | 9.2, 9.3 | 🟢 |

### CWE/SANS Top 25:2025 주요 항목 커버리지

| CWE | 약점 | 관련 체크 | 상태 |
|-----|------|----------|------|
| CWE-79 | XSS | 4.2 | 🟢 |
| CWE-89 | SQL Injection | 4.1 | 🟢 |
| CWE-352 | CSRF | 4.9 | 🟠 |
| CWE-22 | Path Traversal | 4.3 | 🟢 |
| CWE-78 | OS Command Injection | 4.4 | 🟢 |
| CWE-862 | Missing Authorization | 1.5, 1.6 | 🔴 |
| CWE-306 | Missing Auth for Critical Function | 1.5 | 🔴 |
| CWE-502 | Deserialization | 4.6 | 🟢 |
| CWE-798 | Hardcoded Credentials | 3.7 | 🟢 |
| CWE-330 | Insufficient Randomness | 3.5 | 🟢 |
| CWE-362 | Race Condition | 1.16 | 🟠 |
| CWE-400 | Uncontrolled Resource Consumption | 1.9, 2.5 | 🔴 |
| CWE-770 | Allocation Without Limits | 2.4 | 🟠 |
| CWE-256 | Unprotected Credentials Storage | 3.3 | 🟠 |
| CWE-307 | Brute Force | 1.8 | 🟢 (인증 엔드포인트) |
| CWE-209 | Error Message Info Exposure | 7.1, 7.2 | 🟠 |
| CWE-532 | Sensitive Log Info | 7.6 | 🟡 |
| CWE-916 | Weak Password Hash | 3.1 | 🟢 |
| CWE-521 | Weak Password Requirements | 1.13 | 🟢 |
| CWE-755 | Improper Exception Handling | 7.3 | 🟡 |
| CWE-1395 | Dependency on Vulnerable 3rd Party | 5.1, 5.4 | 🔴 |

### NIST SP 800-53 Rev.5 / CSF 2.0 주요 통제 커버리지

| 통제 패밀리 | 관련 체크 | 상태 |
|------------|----------|------|
| AC (Access Control) | 1.1~1.17 | 🟠 |
| AU (Audit & Accountability) | 7.1~7.8 | 🟠 |
| CM (Configuration Management) | 6.1~6.9, 11.3 | 🟠 |
| CP (Contingency Planning) | 10.1~10.6 | 🔴 |
| IA (Identification & Authentication) | 1.1~1.17 | 🟠 |
| IR (Incident Response) | 8.6~8.7 | 🔴 |
| PL (Planning) | 11.1~11.5 | 🔴 |
| RA (Risk Assessment) | 11.4~11.5 | 🟡 |
| SA (System Acquisition) | 5.1~5.4 | 🔴 |
| SC (System & Communications Protection) | 2.1~2.8, 3.1~3.7 | 🔴 |
| SI (System & Information Integrity) | 4.1~4.9, 7.1~7.3 | 🟠 |

### ISO 27001:2022 Annex A 주요 통제 커버리지

| 카테고리 | 통제 수 | 적용 | 양호 | 미비 |
|----------|---------|------|------|------|
| 조직 통제 (A.5) | 37 | 22 | 12 | 10 |
| 인력 통제 (A.6) | 8 | 3 | 2 | 1 |
| 물리 통제 (A.7) | 14 | 5 | 4 | 1 |
| 기술 통제 (A.8) | 34 | 28 | 20 | 8 |

### ISMS-P 영역별 달성도

| 영역 | 항목 수 | 적용 가능 | 달성 | 달성률 |
|------|---------|----------|------|--------|
| 1. 관리체계 수립·운영 | 16 | 10 | 4 | 40% |
| 2. 보호대책 요구사항 | 64 | 45 | 32 | 71% |
| 3. 개인정보 처리 단계별 | 21 | 18 | 13 | 72% |

### SOC 2 Type II TSC 달성도

| 기준 | 적용 | 양호 | 부분 | 미비 |
|------|------|------|------|------|
| Security (CC) | 필수 | 60% | 25% | 15% |
| Availability (A1) | 적용 | 50% | 30% | 20% |
| Confidentiality (C1) | 적용 | 50% | 30% | 20% |
| Privacy (P) | 적용 | 65% | 25% | 10% |
| Processing Integrity (PI) | 선택 | 70% | 20% | 10% |

---

## 현재 코드 보안 강점 (유지 필수)

| 영역 | 구현 | 근거 |
|------|------|------|
| SQL 인젝션 방어 | 전수 파라미터 바인딩 `?` | K1#1, G5 CWE-89 |
| XSS 방어 | escapeHtml + textContent | K1#3, G5 CWE-79 |
| 비밀번호 해싱 | PBKDF2-SHA256 600k iter + 16byte salt | K1#19, G3 IA-5 |
| 타이밍 공격 방어 | secrets.compare_digest | G5 CWE-208 |
| API 키 보안 | SHA-256 해시 저장, 288bit 엔트로피 | K1#20, G3 IA-5 |
| 세션 격리 | ContextVar per-request | G1 A01, G2 API1 |
| 쿠키 보안 | HttpOnly + SameSite=lax + Secure(조건부) | K1#26, G3 SC-23 |
| 입력 검증 | Pydantic v2 extra=forbid 전수 | K1#12, G2 API3 |
| 비밀번호 복잡도 | 대소문자+숫자+특수 서버+JS 이중검증 | K1#25, G5 CWE-521 |
| 난수 생성 | secrets 모듈 전용 | K1#24, G5 CWE-330 |
| 로깅 | AUTH/TOOL/LOGIN IP 기록 | G3 AU-2, G1 A09 |
| 컨테이너 보안 | gosu UID=999, 리소스 제한, health check | K7#13, G3 CM-7 |
| 동의 기록 | GDPR Art.7 준수 타임스탬프+IP+UA | GDPR Art.7, K3 3.1.2 |
| 계정 삭제 | cascade 전체 삭제 (GDPR Art.17) | GDPR Art.17, K3 3.5.1 |

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-03-15 | 초판 작성. 13개 프레임워크 기반 91개 항목 종합 점검 |
| 2026-03-15 | Phase SEC 수정 반영: 🔴 12→5건, 🟢 47→56건. Rate Limiting, XSS, set-password, CORS, Body limit, 크로스유저 유출, X-Forwarded-For, 세션 해시, 단일유저 인증, 의존성 고정, IRP, 보안정책 해결 |
