# Parking lot

Ideas that are not in the plan. Reviewed at every milestone gate: promote, keep or drop.
New ideas go here instead of into the current branch.

Last review: 2026-11-21, M1 gate (#14). Kept: EKS, Brandenburg lines, push notifications and the
AI review bot (each PR already gets a `/code-review`; revisit at the M2 gate). Promoted: the
`JsonFormatter` fix, now #80 in M2.

| Idea | Added | Notes |
| --- | --- | --- |
| A one-afternoon EKS deployment instead of (or next to) the ECS proof of concept | 2026-11-18 | Control plane is roughly $0.10/hour; only if real Kubernetes on AWS is worth showing |
| Brandenburg lines | 2026-11-18 | Non-goal for v1; the feed already contains them |
| Public push notifications for degraded lines | 2026-11-18 | Non-goal for v1; needs accounts or a channel and a privacy policy |
| An AI review bot on pull requests | 2026-11-18 | Set up once CI exists |
