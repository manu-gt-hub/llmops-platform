# 0005 — A DevSecOps baseline that costs nothing to run

## Context

A public endpoint that spends money on every request, an admin page that can
pause the service, and a supply chain of npm packages and GitHub Actions.

## Decision

- **Supply chain:** `npm audit --audit-level=high` and gitleaks (full
  history) fail CI; CodeQL on PRs and weekly; Dependabot weekly.
  (While building this, `npm audit` flagged two critical advisories in
  vitest 3's tinypool; vitest 5 fixed them.)
- **Credentials:** least-privilege Cloudflare token (Workers Scripts, D1,
  Workers AI); secrets only as GitHub / Worker secrets; workflows with
  `contents: read`.
- **Service:** `/admin` behind Basic auth checked in constant time and off
  when `ADMIN_TOKEN` is unset; cross-site POSTs refused; strict CSP (no
  inline scripts), HSTS, no framing; model output always escaped; input
  size limit, per-IP rate limit and budgets against cost abuse; errors never
  returned to clients.

## Consequences

- ➕ Every control is in the repo and covered by a test or a CI step.
- ➖ One shared admin password, no per-user audit trail. Next step:
  Cloudflare Access in front of `/admin/*` (free up to 50 users).
