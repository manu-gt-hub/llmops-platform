/**
 * corpus.ts — The demo agent's knowledge base: a fictional company handbook.
 * Bundled with the Worker, so the agent's tools need no storage.
 */

export const CORPUS: Record<string, string> = {
  plans: `# Acme Cloud — Plans and pricing

## Plans

| Plan       | Price per seat / month | SSO | Support                          |
|------------|------------------------|-----|----------------------------------|
| Starter    | $12                    | No  | Community forum                  |
| Pro        | $29                    | No  | Email, 1 business day            |
| Enterprise | $55                    | Yes | 24/7 phone and dedicated manager |

Single sign-on (SSO) with SAML or OIDC is only included in the Enterprise plan.

## Billing

- Prices are per seat and per month, billed monthly by default.
- Annual billing gives a 15% discount on the yearly total.
- Seats can be added at any time; they are prorated to the end of the billing period.
- Mixed plans are not allowed inside one workspace, but a company can own several workspaces on different plans.`,

  sla: `# Acme Cloud — Service level agreement

## Uptime targets

- Starter: no SLA.
- Pro: 99.9% monthly uptime.
- Enterprise: 99.95% monthly uptime.

## Service credits

When the monthly uptime of a Pro or Enterprise workspace falls below the target of its plan, the customer receives a credit on the next invoice:

| Monthly uptime                        | Credit |
|---------------------------------------|--------|
| Below the plan target, 99.0% or more  | 10%    |
| Below 99.0%, 95.0% or more            | 25%    |
| Below 95.0%                           | 50%    |

Credits must be requested within 30 days of the end of the affected month.`,

  oncall: `# Acme Cloud — On-call policy

## Rotation

Engineers rotate weekly, Monday 09:00 to Monday 09:00 (CET). Each rotation has a primary and a secondary on-call engineer.

## Response times

| Severity | Example                              | Acknowledge within |
|----------|--------------------------------------|--------------------|
| Sev1     | Full outage, data loss               | 15 minutes         |
| Sev2     | Degraded service, major feature down | 1 hour             |
| Sev3     | Minor bug, workaround available      | Next business day  |

## Compensation

The on-call stipend is €150 per week of primary rotation. Secondary rotation is paid €50 per week.`,

  expenses: `# Acme Cloud — Travel and expenses policy

## Limits

- Hotel: up to €140 per night.
- Per diem for meals: €60 per travel day.
- Flights must be economy class.

## Approvals

| Amount of the expense          | Approver           |
|--------------------------------|--------------------|
| Up to €500                     | No approval needed |
| More than €500, up to €2,000   | Line manager       |
| More than €2,000               | Director           |`,
};
