# Pre-Beta Legal Review — Memory MCP Server

> Date: 2026-03-15
> Company: Deep-ON Inc. (Seoul, South Korea)
> Service: Memory MCP Server — multi-user long-term memory SaaS for LLM coding assistants
> Status: Pre-beta launch review
> Disclaimer: This review is an internal assessment, not legal advice. Consult a licensed attorney for final decisions.

---

## Executive Summary

Memory MCP Server is a multi-user SaaS that stores developers' project decisions, code snippets, session summaries, and work context. It collects personal data (email, IP, usage logs) and stores user-generated content in a persistent vector database. The service is deployed on Korean infrastructure and targets a global developer audience.

### Overall Readiness

| Area | Status | Risk Level |
|------|--------|------------|
| Privacy (PIPA) | Partial — consent infra exists, no published policy | **HIGH** |
| Privacy (GDPR) | Partial — Art.7/17/20 code exists, no published policy | **HIGH** |
| Terms of Service | Missing | **HIGH** |
| Open Source Licenses | Good — NOTICE file complete | LOW |
| Third-Party API Terms | Unreviewed | MEDIUM |
| E-Commerce Law | Not applicable (free beta) | LOW |
| AI Regulation | Low risk — tool, not AI system | LOW |
| Cloud/Infra Law | CSAP not required for private sector | LOW |

**Bottom line**: The codebase has good technical foundations (consent recording, account deletion, data export), but the service cannot legally launch without published Privacy Policy and Terms of Service documents, plus a consent checkbox on the signup page.

---

## Table of Contents

1. [Privacy & Data Protection (PIPA)](#1-privacy--data-protection-pipa)
2. [Privacy & Data Protection (GDPR)](#2-privacy--data-protection-gdpr)
3. [Terms of Service](#3-terms-of-service)
4. [Intellectual Property & Licenses](#4-intellectual-property--licenses)
5. [Third-Party Service Dependencies](#5-third-party-service-dependencies)
6. [E-Commerce Law (전자상거래법)](#6-e-commerce-law-전자상거래법)
7. [Information & Communications Network Act (정보통신망법)](#7-information--communications-network-act-정보통신망법)
8. [Cloud Service Law](#8-cloud-service-law)
9. [AI Regulation](#9-ai-regulation)
10. [Action Items Summary](#10-action-items-summary)

---

## 1. Privacy & Data Protection (PIPA)

### 1.1 Applicable Law

한국 개인정보보호법 (Personal Information Protection Act, PIPA) applies to any entity that collects/processes personal information of individuals in South Korea. Deep-ON Inc., based in Seoul, is fully subject to PIPA regardless of where users are located.

Reference: [개인정보보호법 (국가법령정보센터)](https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B0%9C%EC%9D%B8%EC%A0%95%EB%B3%B4%EB%B3%B4%ED%98%B8%EB%B2%95)

### 1.2 Data Inventory — What Is Collected

| Data Category | Items | Collection Point | Legal Basis |
|---------------|-------|-----------------|-------------|
| **Account info** | display_name, email, password_hash | Signup form | Consent |
| **Authentication** | API key hashes, session tokens | Key generation, login | Consent + Contract |
| **Memory content** | User-stored text (decisions, snippets, facts, summaries) | MCP tools / Telegram bot | Consent |
| **Usage metadata** | daily_request_count, project names, memory stats | Automatic | Legitimate interest |
| **Session/access logs** | IP address, user_agent, timestamps | Every request | Legitimate interest |
| **Telegram ID** | telegram_id (integer) | Telegram linking | Consent |
| **Consent records** | document_type, document_ver, consented_at, IP, UA | Signup | Legal obligation |
| **Waitlist** | email, name, reason | Waitlist form | Consent |

### 1.3 Current Compliance Status

| PIPA Requirement | Status | Notes |
|-----------------|--------|-------|
| 개인정보 수집/이용 동의 (Art.15) | **PARTIAL** | Backend records consent at signup, but no actual privacy policy document exists for users to review. Consent is recorded automatically without explicit user action (no checkbox). |
| 개인정보 처리방침 공개 (Art.30) | **MISSING** | No `PRIVACY.md` or privacy policy page exists. **PIPA requires public disclosure.** |
| 수집 항목/목적/보유기간 고지 (Art.15§1) | **MISSING** | Not communicated to users at any point. |
| 정보주체 권리 보장 (Art.35-37) | **PARTIAL** | Data export (`/api/auth/export-data`) and account deletion (`/api/auth/delete-account`) exist. However, rights to access correction, deletion of specific items, and processing suspension are incomplete. |
| 개인정보 파기 (Art.21) | **PARTIAL** | `delete_user_account()` cascades through all tables. `cleanup_expired_sessions()` and `cleanup_old_daily_usage()` exist. But no automated retention policy for inactive accounts (the "30-day inactivity warning" in the beta plan is not implemented). |
| 개인정보 처리 위탁 고지 (Art.26) | **NOT ASSESSED** | If using cloud providers (Oracle, AWS, etc.) to host the service, the cloud provider is a 위탁처리자. This must be disclosed in the privacy policy. |
| 개인정보 보호책임자 지정 (Art.31) | **MISSING** | A Data Protection Officer (or equivalent) must be designated and disclosed. |
| 안전성 확보 조치 (Art.29) | **PARTIAL** | PBKDF2 password hashing (600k iterations), SHA-256 API key hashing, parameterized queries, Pydantic validation. Missing: HTTPS (planned), encryption at rest, access control logs. |

### 1.4 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| P-1 | **Write and publish 개인정보처리방침 (Privacy Policy)** — Must include: collected items, purpose, retention period, third-party sharing, user rights, DPO contact info. Publish at `/privacy` endpoint and link from signup page and dashboard footer. | **MUST** | 1-2 days |
| P-2 | **Add explicit consent checkbox on signup page** — "I have read and agree to the [Privacy Policy] and [Terms of Service]" checkbox before account creation. Currently consent is auto-recorded without user action. | **MUST** | 0.5 day |
| P-3 | **Designate and disclose 개인정보 보호책임자** — Name, department, contact info in the privacy policy. For a small company, the CEO can serve as DPO. | **MUST** | 0.5 day |
| P-4 | **Implement data retention policy** — Define and enforce retention periods. Auto-delete or anonymize data of inactive accounts per the beta plan (30 days). | **SHOULD** | 1 day |
| P-5 | **Add individual memory deletion via dashboard** — Currently users can delete entire accounts but not individual memories via the web UI. PIPA Art.36 requires correction/deletion of specific personal information. | **SHOULD** | 1 day |
| P-6 | **Document processing delegation (위탁)** — List cloud hosting provider as a data processor in the privacy policy. | **SHOULD** | 0.5 day |
| P-7 | **Encrypt data at rest** — Use disk encryption (LUKS or cloud-provided volume encryption) per PIPA Art.29 안전성 확보 조치. | **SHOULD** | 0.5 day |

---

## 2. Privacy & Data Protection (GDPR)

### 2.1 Applicability

GDPR applies if the service is offered to individuals in the EU/EEA, even without a physical presence there. The beta launch plan includes a European region (Hetzner, Germany) in the expansion phase. Even without EU servers, if EU-based developers sign up, GDPR applies.

Note: South Korea received GDPR adequacy status in 2021, which simplifies EU-to-Korea data transfers but does not exempt Korean companies from GDPR compliance when serving EU users.

Reference: [Cooley — South Korea's AI Basic Act](https://www.cooley.com/news/insight/2026/2026-01-27-south-koreas-ai-basic-act-overview-and-key-takeaways)

### 2.2 Current Compliance Status

| GDPR Requirement | Status | Notes |
|-----------------|--------|-------|
| Legal basis for processing (Art.6) | **PARTIAL** | Consent recorded at signup, but no policy document defines the legal basis. |
| Privacy notice (Art.13/14) | **MISSING** | No privacy policy published. |
| Consent records (Art.7) | **GOOD** | `consents` table records user_id, document_type, document_ver, IP, UA, timestamp. |
| Right to erasure (Art.17) | **GOOD** | `delete_user_account()` performs cascading delete of all data including ChromaDB memories. |
| Right to data portability (Art.20) | **GOOD** | `export_user_data()` exports JSON with profile, sessions, API keys, consents. |
| Data Processing Agreement (Art.28) | **MISSING** | No DPA with cloud hosting providers. |
| Data Protection Impact Assessment (Art.35) | **MISSING** | Not performed. May be required for large-scale processing of personal data. |
| Cross-border transfer safeguards (Art.46) | **NOT YET APPLICABLE** | No EU operations yet. When EU region launches, Standard Contractual Clauses or adequacy decision mechanism needed. |
| DPO designation (Art.37) | **CONDITIONAL** | Required only for large-scale processing. For a 20-user beta, likely not required, but recommended. |

### 2.3 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| G-1 | **Include GDPR-compliant sections in Privacy Policy** — Legal basis, data controller info, EU user rights, cross-border transfer mechanisms. Can be combined with PIPA privacy policy in a single bilingual document. | **MUST** (if accepting EU users) | Included in P-1 |
| G-2 | **Add memory data to export** — Current `export_user_data()` exports account data but NOT the actual memory content (ChromaDB data). The caller must separately handle this. Add memory export to the export endpoint. | **SHOULD** | 1 day |
| G-3 | **Prepare DPA template** — For future cloud provider relationships. | NICE TO HAVE (beta) | 1 day |

---

## 3. Terms of Service

### 3.1 Current Status

**No Terms of Service exist.** The README contains a brief disclaimer ("AS IS", no warranty for beta), but this is not a legally binding agreement presented to users before they use the service.

### 3.2 Required Contents

A Terms of Service document is legally required under Korean law (전자상거래법, 정보통신망법) when operating an online service. It should include:

| Section | Description |
|---------|-------------|
| **서비스 정의** | What Memory MCP Server does, scope of service |
| **이용자 의무** | Prohibited content (illegal content, credentials, PII of third parties), acceptable use |
| **지적재산권** | User retains ownership of stored content; license grant to operate the service |
| **면책조항** | AS IS warranty disclaimer, no guarantee of data availability, beta service limitations |
| **서비스 변경/중단** | Right to modify, suspend, or terminate service with notice |
| **계정 해지** | User-initiated and provider-initiated account termination |
| **데이터 처리** | Reference to Privacy Policy |
| **분쟁 해결** | Governing law (Korean law), jurisdiction (Seoul courts) |
| **약관 변경** | Notification method for terms changes (email, dashboard notice) |
| **연령 제한** | Minimum age (14 in Korea per 개인정보보호법, 16 in EU per GDPR) |
| **배상 제한** | Limitation of liability |

### 3.3 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| T-1 | **Write and publish Terms of Service** — Bilingual (Korean primary, English). Publish at `/terms` endpoint. | **MUST** | 1-2 days |
| T-2 | **Add consent checkbox on signup** — Combined with P-2 above. | **MUST** | (included in P-2) |
| T-3 | **Add version tracking for terms** — The consent table already supports `document_ver`. Increment version when terms change and require re-consent. | **SHOULD** | 0.5 day |
| T-4 | **Add terms change notification mechanism** — Email or dashboard banner when terms are updated. | NICE TO HAVE | 1 day |

---

## 4. Intellectual Property & Licenses

### 4.1 Project License

The project uses a **proprietary license** (LicenseRef-Proprietary). The `LICENSE` file is correctly placed and contains appropriate copyright notice for Deep-ON Inc.

**Status: COMPLIANT**

### 4.2 Open Source Dependencies

The `NOTICE` file is comprehensive and correctly categorizes all major dependencies.

| License Type | Packages | Compliance Status |
|-------------|----------|-------------------|
| **LGPL v3** | kiwipiepy, kiwipiepy_model, python-telegram-bot | **COMPLIANT** — Used via pip install (dynamic linking), not modified, NOTICE file includes disclosure |
| **Apache 2.0** | chromadb, sentence-transformers, rank-bm25, transformers | **COMPLIANT** — NOTICE file includes copyright notices |
| **MIT** | mcp[cli] (FastMCP), pydantic | **COMPLIANT** — Permissive, no special obligations |
| **BSD-3** | PyTorch, uvicorn, NumPy | **COMPLIANT** — Permissive |

### 4.3 Embedding Model License

**paraphrase-multilingual-MiniLM-L12-v2**: Apache 2.0. Commercial use explicitly permitted.

Reference: [HuggingFace model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)

### 4.4 User Content Ownership

Users store code snippets, decisions, and project context. The Terms of Service must clarify:
- Users retain full ownership of their stored content
- Deep-ON Inc. receives only the minimum license necessary to operate the service (store, index, search, display back to user)
- Deep-ON Inc. does NOT use user content for training, advertising, or any purpose beyond service delivery

### 4.5 Brand/Trademark

The name "Reminol" was previously reviewed. "Memory MCP Server" is descriptive and likely not trademarkable. No trademark conflicts identified for current branding.

### 4.6 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| IP-1 | **Define content ownership in Terms of Service** — Users own their content, limited license to operate service. | **MUST** (in T-1) | (included in T-1) |
| IP-2 | **LGPL compliance for Docker distribution** — Ensure Docker image includes a way for users to access LGPL library source code (currently covered by NOTICE URLs). | **DONE** | — |

---

## 5. Third-Party Service Dependencies

### 5.1 Anthropic API (Claude)

Memory MCP stores context from Claude Code sessions, including tool outputs and session summaries. Per Anthropic's current terms:

- **API customers**: Anthropic does NOT use customer data for training without explicit opt-in.
- **Data retention**: API logs retained for 7 days (as of Sep 2025).
- **Storing responses**: The Anthropic API terms do not prohibit users/third-parties from storing API responses for their own use.

**Assessment**: Memory MCP stores user-authored decisions and facts, not raw Claude API responses. The memory content is user-directed ("store this decision"). This appears compliant with Anthropic's terms, but the Terms of Service should clarify that Memory MCP is independent of Anthropic.

Reference: [Anthropic Privacy Center](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data)

### 5.2 Groq API

Used by the Telegram bot for LLM intent classification. The Telegram bot is marked as a Pro-only feature in the beta plan.

**Assessment**: Review Groq's terms for commercial use and data handling. Groq API terms generally allow commercial use.

### 5.3 HuggingFace Models

The embedding model is downloaded from HuggingFace Hub and bundled in the Docker image. Apache 2.0 licensed — commercial use permitted.

### 5.4 Telegram Bot API

python-telegram-bot is LGPL v3 (covered in NOTICE). Telegram Bot API itself is free for all use cases per Telegram's terms.

### 5.5 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| TP-1 | **Add disclaimer that Memory MCP is not affiliated with Anthropic/Claude** — In Terms of Service. | **SHOULD** | (in T-1) |
| TP-2 | **Review Groq API terms** — Confirm commercial use and data handling compliance. | **SHOULD** | 0.5 day |
| TP-3 | **Review all LLM provider terms** — google-genai, openai, anthropic packages used by Telegram bot. | NICE TO HAVE | 0.5 day |

---

## 6. E-Commerce Law (전자상거래법)

### 6.1 Applicability

The 전자상거래 등에서의 소비자보호에 관한 법률 (E-Commerce Consumer Protection Act) applies to businesses selling goods or services online. Key question: does a **free beta service** trigger 통신판매업 신고 (mail-order sales registration)?

**Analysis**:
- The beta is **free** with no payment processing
- No goods or services are sold for money
- 통신판매업 신고 exemption applies when: annual transactions < 50 OR the operator is a 간이과세자 (simplified tax payer)

**Assessment**: A completely free beta service does NOT require 통신판매업 신고. However, once paid plans (Pro tier) are introduced, registration becomes mandatory.

Reference: [정부24 통신판매업신고](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=11300000006)

### 6.2 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| EC-1 | **통신판매업 신고 when paid plans launch** — Not needed for free beta. Plan ahead for Pro tier launch. | NOT YET | — |
| EC-2 | **청약철회 (cooling-off) policy for paid tier** — Korean law requires 7-day cooling-off period for digital services. Plan subscription/refund policy before monetization. | NOT YET | — |

---

## 7. Information & Communications Network Act (정보통신망법)

### 7.1 Applicability

정보통신망 이용촉진 및 정보보호 등에 관한 법률 applies to operators of information and communications services. Memory MCP Server qualifies as such.

### 7.2 Key Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| 운영자 정보 표시 (Art.42) — 상호, 대표자, 주소, 연락처 | **MISSING** | The dashboard and website must display company info: Deep-ON Inc., representative name, Seoul address, contact email/phone. |
| 이용약관 게시 (Art.27.1) | **MISSING** | Terms of Service must be published. |
| 개인정보 보호 (Art.28) | **PARTIAL** | See PIPA section. |
| 청소년 보호 | N/A for beta | Developer tool, not youth-targeted. |

### 7.3 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| ICT-1 | **Display operator information on dashboard/website** — Company name, representative, address, contact. Add to dashboard footer or a dedicated `/about` page. | **MUST** | 0.5 day |

---

## 8. Cloud Service Law

### 8.1 CSAP (Cloud Security Assurance Program)

CSAP is Korea's cloud security certification managed by KISA. It is **mandatory only for cloud services provided to government/public sector organizations**.

**Assessment**: Memory MCP Server targets private-sector developers. CSAP certification is NOT required for the beta launch or for private-sector SaaS in general.

Reference: [KISA CSAP](https://isms.kisa.or.kr/main/csap/intro/)

### 8.2 클라우드컴퓨팅법 (Cloud Computing Act)

The Cloud Computing Development and User Protection Act imposes general obligations on cloud service providers, including:
- User data protection
- Service level agreements
- Incident notification

For a small-scale beta, compliance is lightweight. Key obligation: notify users in advance of service changes/termination.

### 8.3 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| CL-1 | **Include service change/termination notice clause in Terms** — 30 days advance notice for material changes. | **SHOULD** (in T-1) | (included in T-1) |

---

## 9. AI Regulation

### 9.1 Korea AI Basic Act (AI 기본법)

South Korea's AI Basic Act took effect on January 22, 2026. Key provisions:

- Applies to "AI business operators" who develop or provide AI systems
- **High-impact AI** in healthcare, energy, public services requires impact assessments
- Transparency and labeling requirements for generative AI applications
- Grace period: at least 1 year before administrative fines

**Assessment**: Memory MCP Server is a **data storage and retrieval tool**, not an AI system itself. It stores memories for use by AI assistants but does not make autonomous decisions, generate content, or classify/predict outcomes. The embedding model is used purely for semantic search indexing, not for decision-making.

**Classification**: Memory MCP is likely categorized as a tool/infrastructure service, not an AI system subject to the AI Basic Act. It is analogous to a database, not an AI application.

However, marketing materials should avoid implying that Memory MCP itself is an "AI" — it is a memory management tool used alongside AI systems.

Reference: [South Korea AI Basic Act (Library of Congress)](https://www.loc.gov/item/global-legal-monitor/2026-02-20/south-korea-comprehensive-ai-legal-framework-takes-effect)

### 9.2 EU AI Act

The EU AI Act applies to providers of AI systems offered in the EU market. Similar analysis applies — Memory MCP is a storage/retrieval tool, not an AI system.

**Assessment**: EU AI Act is NOT applicable to Memory MCP Server.

### 9.3 AI-Generated Content Labeling

Memory stores session summaries and auto-extracted facts, some of which may be AI-generated. Korean AI Basic Act requires labeling of AI-generated content in certain contexts.

**Assessment**: Low risk. Memory content is for the user's own reference, not published to the public. No labeling obligation for private-use content storage.

### 9.4 Required Actions

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| AI-1 | **Avoid "AI" positioning in legal/regulatory context** — Market as "memory management tool for AI assistants", not as an AI system. | NICE TO HAVE | — |

---

## 10. Action Items Summary

### MUST DO (Before Beta Launch)

These are **legal blockers** — the service should not accept users without these in place.

| # | Action | Effort | Assignee |
|---|--------|--------|----------|
| **P-1** | Write and publish 개인정보처리방침 (Privacy Policy) at `/privacy` | 1-2 days | Legal/PM |
| **T-1** | Write and publish 이용약관 (Terms of Service) at `/terms` | 1-2 days | Legal/PM |
| **P-2** | Add consent checkbox to signup page ("I agree to Privacy Policy and Terms of Service") | 0.5 day | Dev |
| **P-3** | Designate 개인정보 보호책임자 (DPO) and include in privacy policy | 0.5 day | Management |
| **ICT-1** | Display operator info (Deep-ON Inc., representative, address, contact) on dashboard | 0.5 day | Dev |

**Estimated total: 3-5 days**

### SHOULD DO (Within First Week of Beta)

These are important for compliance but not absolute blockers for a closed beta with invite codes.

| # | Action | Effort |
|---|--------|--------|
| **P-4** | Implement data retention policy (inactive account cleanup) | 1 day |
| **P-5** | Add individual memory management (view/delete) to dashboard | 1 day |
| **P-6** | Document cloud provider as data processor (위탁처리) | 0.5 day |
| **P-7** | Enable encryption at rest (disk-level) | 0.5 day |
| **G-2** | Add memory content to data export (currently account-only) | 1 day |
| **T-3** | Implement terms version tracking with re-consent flow | 0.5 day |
| **TP-1** | Add "not affiliated with Anthropic" disclaimer | 0.5 day |
| **TP-2** | Review Groq API terms for commercial use | 0.5 day |

### NICE TO HAVE (Before Paid Launch)

| # | Action | Effort |
|---|--------|--------|
| **EC-1** | 통신판매업 신고 (mail-order business registration) | 1 day |
| **EC-2** | Design subscription refund/cooling-off policy | 1 day |
| **G-3** | Prepare Data Processing Agreement template | 1 day |
| **T-4** | Terms change notification system | 1 day |
| **TP-3** | Review all LLM provider (Google, OpenAI, Anthropic) terms | 0.5 day |

---

## Appendix A: Privacy Policy Outline

The Privacy Policy (개인정보처리방침) should contain at minimum:

1. **개인정보 처리 목적** — Account management, service delivery, security
2. **수집하는 개인정보 항목** — Email, display name, IP, user agent, telegram ID, memory content
3. **개인정보 보유/이용 기간** — Account active + 30 days after deletion, logs 90 days
4. **개인정보 제3자 제공** — Cloud hosting provider (name, purpose, items)
5. **개인정보 처리 위탁** — Cloud provider, hosting details
6. **정보주체의 권리** — Access, correction, deletion, processing suspension, data export
7. **개인정보 보호책임자** — Name, contact, department
8. **안전성 확보 조치** — Encryption (transit/at rest), access control, hashing
9. **개인정보 자동 수집 장치** — Cookies (session cookie), purpose, opt-out method
10. **고충 처리** — Contact info, 개인정보 분쟁조정위원회 reference
11. **처리방침 변경** — Effective date, change notification method

For GDPR compliance, add:
- Data controller identity and contact
- Legal basis for each processing activity
- Cross-border transfer mechanisms (when applicable)
- Right to lodge complaint with supervisory authority

## Appendix B: Signup Page Consent UI Specification

Current signup page (`/signup`) collects: invite code, display name, email, password. The backend auto-records consent without explicit user action.

**Required change**:

```html
<!-- Add before the submit button -->
<div class="field">
  <label>
    <input type="checkbox" id="agreeTerms" required>
    I have read and agree to the
    <a href="/terms" target="_blank">Terms of Service</a> and
    <a href="/privacy" target="_blank">Privacy Policy</a>.
  </label>
</div>
```

The signup API should verify that consent was explicitly given and record the specific document versions in the `consents` table.

## Appendix C: Dashboard Footer Specification

Add to all dashboard pages:

```
Deep-ON Inc. | 대표: [Name] | 주소: [Address]
Contact: [email] | Terms of Service | Privacy Policy
```

This satisfies 정보통신망법 Art.42 (operator information display).

---

## Sources

- [Korea PIPA — 국가법령정보센터](https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B0%9C%EC%9D%B8%EC%A0%95%EB%B3%B4%EB%B3%B4%ED%98%B8%EB%B2%95)
- [PIPA Compliance Overview — Google Cloud](https://cloud.google.com/security/compliance/pipa-korea)
- [South Korea PIPA — Odaseva](https://www.odaseva.com/compliance/south-korea-considerations-for-data-privacy-compliance)
- [South Korea's AI Basic Act — Cooley](https://www.cooley.com/news/insight/2026/2026-01-27-south-koreas-ai-basic-act-overview-and-key-takeaways)
- [AI Basic Act Overview — Library of Congress](https://www.loc.gov/item/global-legal-monitor/2026-02-20/south-korea-comprehensive-ai-legal-framework-takes-effect)
- [AI Basic Act — Cloud Security Alliance](https://cloudsecurityalliance.org/blog/2025/03/12/what-you-need-to-know-about-south-korea-s-ai-basic-act)
- [GDPR SaaS Compliance 2026 — Feroot](https://www.feroot.com/blog/gdpr-saas-compliance-2025/)
- [GDPR Compliance Guide 2026 — SecurePrivacy](https://secureprivacy.ai/blog/gdpr-compliance-2026)
- [Cross-Border Data Transfers Guide — SecurePrivacy](https://secureprivacy.ai/blog/cross-border-data-transfers-2025-guide)
- [Anthropic Privacy Center — Data Retention](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data)
- [Anthropic Terms Updates](https://privacy.claude.com/en/articles/9190861-terms-of-service-updates)
- [HuggingFace paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
- [KISA CSAP](https://isms.kisa.or.kr/main/csap/intro/)
- [통신판매업 신고 — 정부24](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=11300000006)
- [전자상거래법 — 국가법령정보센터](https://www.law.go.kr/lsInfoP.do?lsId=009318&ancYnChk=0)
- [개인정보보호위원회 (PIPC)](https://www.pipc.go.kr/np/default/page.do?mCode=H010000000)
- [PIPA 개정 안내 — Kim & Chang](https://www.kimchang.com/ko/insights/detail.kc?sch_section=4&idx=28769)

---

## Final Status (2026-03-15)

All 5 MUST items from Section 10 have been implemented:

| # | Action | Status |
|---|--------|--------|
| **P-1** | 개인정보처리방침 (Privacy Policy) — 12개 조항 | **DONE** — `docs/PRIVACY_POLICY.md` + `/docs/privacy-policy` HTML |
| **T-1** | 이용약관 (Terms of Service) — 14개 조항 | **DONE** — `docs/TERMS_OF_SERVICE.md` + `/docs/terms` HTML |
| **P-2** | Consent checkbox on signup (3-layer validation) | **DONE** — HTML required + JS check + backend 400 |
| **P-3** | DPO designation (개인정보 보호책임자) | **DONE** — 김동규, privacy@kandela.ai |
| **ICT-1** | Operator info display (운영자 정보 표시) | **DONE** — `docs/OPERATOR_INFO.md` + `/docs/operator-info` HTML |

Additionally completed (SHOULD items):

| # | Action | Status |
|---|--------|--------|
| **P-6** | Processing delegation (위탁처리) | **DONE** — Oracle Cloud listed in Privacy Policy |
| **TP-1** | Not affiliated with Anthropic disclaimer | **DONE** — Terms Art.4 item 3 |
| **G-1** | GDPR sections in Privacy Policy | **DONE** — Art.20 data portability, cross-border |
| **T-3** | Terms version tracking | **DONE** — consent_ver recorded as "2026-03-15" |

**Overall**: All legal blockers resolved. Service is legally ready for closed invite-only beta launch. TLS deployment is the remaining infrastructure prerequisite.
