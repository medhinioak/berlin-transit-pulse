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
| A custom domain, with a matching reverse DNS (PTR) name for the Elastic IP | 2026-11-21 | Route 53 zone $0.50/month plus about €10–15/year; the server keeps AWS's default PTR until then ([ADR-0020](adr/0020-run-the-release-on-one-t4g-small-with-no-ssh.md)). An ACM certificate on it (free, in us-east-1) would also let CloudFront require TLS 1.2 (`TLSv1.2_2021`); the default certificate allows TLS 1.0 ([ADR-0021](adr/0021-serve-the-site-through-cloudfront-with-an-s3-origin-and-a-cloudfront-only-ec2-origin.md)) |
| A secret origin header between CloudFront and nginx | 2026-11-21 | Hardening: today any CloudFront distribution, including someone else's, can reach port 80. Worth it once the API has anything non-public or costly to serve ([ADR-0021](adr/0021-serve-the-site-through-cloudfront-with-an-s3-origin-and-a-cloudfront-only-ec2-origin.md)) |
