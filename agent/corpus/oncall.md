# Acme Cloud — On-call policy

## Rotation

Engineers rotate weekly, Monday 09:00 to Monday 09:00 (CET). Each rotation
has a primary and a secondary on-call engineer.

## Response times

| Severity | Example                         | Acknowledge within |
|----------|---------------------------------|--------------------|
| Sev1     | Full outage, data loss          | 15 minutes         |
| Sev2     | Degraded service, major feature down | 1 hour        |
| Sev3     | Minor bug, workaround available | Next business day  |

If the primary does not acknowledge a Sev1 page in time, it escalates to the
secondary, then to the engineering manager.

## Compensation

The on-call stipend is €150 per week of primary rotation. Secondary rotation
is paid €50 per week. Incidents handled outside office hours also give one
day off in lieu per night.
