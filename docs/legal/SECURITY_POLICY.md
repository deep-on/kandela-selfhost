# Security Policy

## Security Principles

Memory MCP Server handles sensitive user data (personal memories, account information). Our security approach is guided by:

1. **Defense in depth** — Multiple layers of protection (encryption at rest, TLS in transit, API key authentication, input validation)
2. **Least privilege** — The server runs with minimal permissions; containers use non-root users
3. **Secure defaults** — CORS is restrictive by default; multi-user mode requires API key authentication
4. **Transparency** — Security issues are disclosed responsibly; this policy is public

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Previous minor | Security fixes only |
| Older | No |

We recommend always running the latest version.

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

### Contact

- **Email**: security@kandela.ai
- **Response time**: We aim to acknowledge within 48 hours

### What to Include

- Description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact assessment
- Suggested fix (if you have one)

### What to Expect

1. **Acknowledgment** within 48 hours
2. **Assessment** within 7 days — we will confirm the issue and assess severity
3. **Fix timeline** — Critical/High within 7 days; Medium within 30 days; Low within 90 days
4. **Notification** when the fix is released

## Responsible Disclosure

We follow a coordinated disclosure process:

- We ask reporters to give us reasonable time to fix issues before public disclosure (minimum 90 days for non-critical issues).
- We will credit reporters in release notes (unless they prefer anonymity).
- We will not take legal action against good-faith security researchers.

## Security Update Process

1. Security patches are released as soon as possible after verification.
2. Patches are applied to the production server immediately upon release.
3. Release notes include a security section describing the fix (after a reasonable disclosure period).
4. Users running self-hosted instances are notified via release notes.

## Severity Classification

| Severity | Definition | Response Time |
|----------|------------|---------------|
| Critical | Remote code execution, authentication bypass, data exfiltration | 24-48 hours |
| High | Privilege escalation, significant data exposure | 7 days |
| Medium | Limited data exposure, denial of service | 30 days |
| Low | Information disclosure with minimal impact | 90 days |

## Security Measures in Place

### Authentication & Authorization
- API key authentication (SHA-256 hashed storage) for multi-user mode
- PBKDF2-SHA256 (600,000 iterations) for password hashing
- Session tokens stored as SHA-256 hashes
- Per-user data isolation in multi-user mode

### Data Protection
- Encryption at rest required (see [DEPLOYMENT_SECURITY.md](DEPLOYMENT_SECURITY.md))
- TLS required for all network communication in production
- Request body size limits (10 MB) to prevent abuse
- CORS restrictions configurable via environment variable

### Input Validation
- Pydantic v2 input validation on all MCP tool inputs
- SQL parameterized queries throughout (no string interpolation)
- Allowlisted column names for dynamic SQL operations

### Operational Security
- Structured logging without sensitive data exposure
- Docker container runs with minimal privileges
- No default credentials shipped

## Incident Response

For data breach procedures, see [DATA_BREACH_PROCEDURE.md](DATA_BREACH_PROCEDURE.md).

## GDPR Compliance

- User data export (Art. 20): `export_user_data()` endpoint
- Account deletion (Art. 17): `delete_user_account()` with full cascade
- Consent tracking (Art. 7): `consents` table with audit trail
- See [PRIVACY_POLICY.md](PRIVACY_POLICY.md) for full privacy policy

## Security Contacts

| Role | Contact |
|------|---------|
| Security reports | security@kandela.ai |
| General inquiries | support@kandela.ai |
