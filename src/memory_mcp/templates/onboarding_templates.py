"""Domain-specific onboarding templates for /kd-init.

When a user initializes a project, suggest domain-relevant gotchas
based on the project type. Derived from:
- CodeChat 82K conversation analysis (domain distribution)
- Real-world log_analyzer findings (61 --no-deps violations, etc.)
- Common developer pain points per domain

Usage:
    templates = get_domain_templates("web_backend")
    for t in templates:
        print(t["content"])  # suggested gotcha to store
"""

from __future__ import annotations

DOMAIN_TEMPLATES: dict[str, list[dict[str, str]]] = {
    "web_backend": [
        {
            "content": "배포 시 docker compose up에 --no-deps 필수. 없으면 DB/Redis 등 의존 서비스가 재시작되어 활성 연결 끊김.",
            "tags": "gotcha,deploy,docker",
            "importance": "9.0",
        },
        {
            "content": "DB 마이그레이션은 전용 컨테이너에서만 실행. 프로덕션 컨테이너에서 직접 실행 금지 (advisory lock 없어 동시 배포 시 스키마 충돌).",
            "tags": "gotcha,migration,database",
            "importance": "9.0",
        },
        {
            "content": "테스트는 반드시 test/dev 컨테이너에서만 실행. 프로덕션 컨테이너에서 pytest 실행 시 teardown이 DB를 삭제할 수 있음.",
            "tags": "gotcha,testing,container",
            "importance": "9.5",
        },
        {
            "content": "환경변수(.env)에 시크릿 키, DB URL, API 키 등 민감 정보 관리. .env 파일은 .gitignore에 포함 필수.",
            "tags": "gotcha,security,env",
            "importance": "8.0",
        },
        {
            "content": "API 엔드포인트 변경 시 클라이언트 호환성 확인. 하위 호환 불가 변경은 API 버전 업 (v1→v2) 필요.",
            "tags": "decision,api,versioning",
            "importance": "7.0",
        },
    ],
    "web_frontend": [
        {
            "content": "빌드 시 환경변수 주입 방식 확인. NEXT_PUBLIC_ (Next.js), VITE_ (Vite) 등 프레임워크별 prefix 필요.",
            "tags": "gotcha,build,env",
            "importance": "8.0",
        },
        {
            "content": "SSR/SSG 프로젝트에서 window/document 접근은 클라이언트 사이드에서만. useEffect/onMounted 내에서 사용.",
            "tags": "gotcha,ssr,browser-api",
            "importance": "8.5",
        },
        {
            "content": "패키지 매니저 통일 (npm/yarn/pnpm). lock 파일 충돌 방지를 위해 팀 전체 동일 매니저 사용.",
            "tags": "decision,package-manager",
            "importance": "7.0",
        },
        {
            "content": "컴포넌트 네이밍 컨벤션 (PascalCase/kebab-case) 프로젝트 초기에 결정하고 일관성 유지.",
            "tags": "decision,naming,convention",
            "importance": "6.0",
        },
    ],
    "data_science": [
        {
            "content": "데이터 파일(.csv, .parquet) 경로는 상대 경로 사용. 절대 경로 하드코딩 금지 (다른 환경에서 실행 불가).",
            "tags": "gotcha,data,path",
            "importance": "8.0",
        },
        {
            "content": "모델 학습 시 랜덤 시드 고정 (random_state, seed). 재현 가능성 확보.",
            "tags": "gotcha,ml,reproducibility",
            "importance": "8.5",
        },
        {
            "content": "Jupyter 노트북의 셀 실행 순서 의존성 주의. 커널 재시작 후 전체 실행이 정상 동작하는지 확인.",
            "tags": "gotcha,jupyter,execution-order",
            "importance": "7.0",
        },
        {
            "content": "대용량 데이터셋은 Git에 포함하지 않음. .gitignore + DVC 또는 별도 스토리지 사용.",
            "tags": "gotcha,data,git",
            "importance": "8.0",
        },
    ],
    "devops": [
        {
            "content": "docker compose up에 --no-deps 필수. 없으면 의존 서비스가 재시작되어 장애 발생.",
            "tags": "gotcha,docker,deploy",
            "importance": "9.0",
        },
        {
            "content": "프로덕션 컨테이너에 직접 exec/cp 금지. 변경사항은 이미지 빌드 → 재배포 경로로.",
            "tags": "gotcha,docker,production",
            "importance": "9.0",
        },
        {
            "content": "SSH 접속 시 비표준 포트/호스트는 반드시 기록. ssh config 또는 기억에 저장.",
            "tags": "gotcha,ssh,infrastructure",
            "importance": "9.0",
        },
        {
            "content": "배포 전 health check 엔드포인트 확인. 배포 후 즉시 /health 또는 /api/health 호출로 정상 동작 검증.",
            "tags": "gotcha,deploy,healthcheck",
            "importance": "8.0",
        },
        {
            "content": "로그 로테이션 설정 확인. 미설정 시 디스크 풀 → 서비스 다운.",
            "tags": "gotcha,ops,logging",
            "importance": "7.5",
        },
    ],
    "database": [
        {
            "content": "마이그레이션은 전용 컨테이너/환경에서만 실행. 프로덕션 DB에 직접 DDL 금지.",
            "tags": "gotcha,migration,production",
            "importance": "9.0",
        },
        {
            "content": "인덱스 추가/삭제는 서비스 영향도 확인 후 진행. 대형 테이블 인덱스 생성은 CONCURRENTLY 옵션 사용.",
            "tags": "gotcha,index,performance",
            "importance": "8.5",
        },
        {
            "content": "백업 스케줄과 복구 절차 확인. 주기적으로 복구 테스트 수행.",
            "tags": "gotcha,backup,disaster-recovery",
            "importance": "8.0",
        },
    ],
    "mobile": [
        {
            "content": "앱 서명 키(keystore/certificate) 안전 보관. 분실 시 앱 업데이트 불가.",
            "tags": "gotcha,signing,security",
            "importance": "9.5",
        },
        {
            "content": "API 호출은 반드시 비동기 처리. 메인 스레드에서 네트워크 호출 시 ANR/UI 프리즈.",
            "tags": "gotcha,async,performance",
            "importance": "8.5",
        },
        {
            "content": "각 스토어(App Store/Play Store) 심사 가이드라인 준수. 위반 시 리젝/삭제 위험.",
            "tags": "gotcha,store,compliance",
            "importance": "8.0",
        },
    ],
}

# Domain aliases
DOMAIN_ALIASES: dict[str, str] = {
    "web": "web_backend",
    "backend": "web_backend",
    "api": "web_backend",
    "frontend": "web_frontend",
    "react": "web_frontend",
    "vue": "web_frontend",
    "angular": "web_frontend",
    "ml": "data_science",
    "ai": "data_science",
    "machine_learning": "data_science",
    "data": "data_science",
    "docker": "devops",
    "infra": "devops",
    "infrastructure": "devops",
    "ops": "devops",
    "db": "database",
    "sql": "database",
    "postgres": "database",
    "mysql": "database",
    "app": "mobile",
    "android": "mobile",
    "ios": "mobile",
    "flutter": "mobile",
}

AVAILABLE_DOMAINS = sorted(set(DOMAIN_TEMPLATES.keys()))


def get_domain_templates(domain: str) -> list[dict[str, str]]:
    """Get gotcha templates for a domain.

    Args:
        domain: Domain name or alias (e.g., "web", "devops", "react")

    Returns:
        List of template dicts with content, tags, importance.
        Empty list if domain not found.
    """
    # Resolve alias
    resolved = DOMAIN_ALIASES.get(domain.lower(), domain.lower())
    return DOMAIN_TEMPLATES.get(resolved, [])


def list_domains() -> list[str]:
    """List available domain names."""
    return AVAILABLE_DOMAINS
