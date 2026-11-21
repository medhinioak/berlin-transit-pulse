# ADR-0021: Serve the site through CloudFront with an S3 origin and a CloudFront-only EC2 origin

- Status: Accepted
- Date: 2026-11-21
- Decider: Maintainer (@medhinioak)
- Review: decisions fixed in issue #17 before implementation; the page's response headers and the TLS floor decided during planning; a `/code-review` of the branch dropped OPTIONS and X-XSS-Protection and fixed the API's redirects and docs behind the proxy; implementation reviewed in the pull request that closes #17, 2026-11-21

## Context

The release needs a public HTTPS URL for the live page and its API (M2 exit criteria). The
page is three static files (`web/`) that call `/api/v1/now/by-mode` with a relative path. The
API is public, read-only, and sends `Cache-Control: max-age=30`. Everything else runs on one
t4g.small with 2 GB of RAM ([ADR-0020](0020-run-the-release-on-one-t4g-small-with-no-ssh.md)),
which has no other inbound port and must not grow a load balancer or a NAT gateway
(€20 monthly ceiling, [cost](../cost.md)). There is no custom domain yet. CloudFront's
always-free allowance is 1 TB out and 10 million requests a month, and origin fetches from S3
and EC2 are free.

## Decision

One CloudFront distribution on the default `*.cloudfront.net` domain, with two origins:

- **Default behaviour** → the private web bucket through Origin Access Control (SigV4, always
  sign). The bucket policy allows `s3:GetObject` to `cloudfront.amazonaws.com` only for this
  distribution's ARN. Managed `CachingOptimized` cache policy.
- **`/api/*`** → the instance's public DNS name over HTTP on port 80, to nginx, which proxies to
  metrics-api. The managed `UseOriginCacheControlHeaders-QueryStrings` cache policy honours the
  API's own `max-age`. GET and HEAD only: the page calls the API from the same origin, so there
  are no CORS preflights, and OPTIONS requests would never be cached and would all reach the
  server (#17 had GET, HEAD and OPTIONS).
- **Origin protection:** the security group allows port 80 from the AWS-managed prefix list
  `com.amazonaws.global.cloudfront.origin-facing` and nothing else inbound.
- **Viewers:** redirect HTTP to HTTPS, HTTP/2 and HTTP/3, compression, `PriceClass_100`.
- **Headers:** the managed `SecurityHeadersPolicy` on `/api/*`. The page gets a custom response
  headers policy with the same headers, plus the Content-Security-Policy and
  `Referrer-Policy: no-referrer` that nginx sends locally. It differs from the managed policy
  in two ways: `X-Frame-Options: DENY`, which matches `frame-ancestors 'none'`, and no
  `X-XSS-Protection`, which browsers have dropped and nginx doesn't send.
- **The API behind the prefix:** nginx strips `/api/`, and the origin sees its own host name
  over HTTP, so the API can't build a correct absolute URL. It therefore makes none:
  trailing-slash redirects are off (`/v1/now/by-mode/` is a 404), and the self-hosted docs page
  derives the prefix from its own path in the browser.

## Alternatives considered

- **Everything from nginx on the instance** (one custom origin). Simpler: one origin, and the
  same headers as locally. Not chosen: the page would go down with the server, so its error
  state could never show. Every page cache miss would also reach the 2 GB server. S3 costs
  nothing at this size.
- **HTTPS from CloudFront to the origin.** It would need a certificate on the instance for a
  name we own (Let's Encrypt with renewal, or a domain). The data is public and read-only, no
  cookies or credentials pass through, and the hop runs from CloudFront edge locations to AWS's
  network. Not worth a certificate to renew on a server that one part-time maintainer looks
  after. With a custom domain, revisit.
- **A secret header from CloudFront, checked by nginx** (alone or with the prefix list). The
  prefix list already keeps the server off the open internet with no secret to store or rotate.
  What it doesn't stop: another AWS customer's distribution pointing at our origin. That person
  would get the same public, read-only API anyone gets through our own URL. Recorded in the
  [parking lot](../parking-lot.md) for when the API serves anything non-public or costly.
- **The managed `SecurityHeadersPolicy` on the page too**, as issue #17 first said. It has no
  Content-Security-Policy, and S3 sends none. The page would lose the CSP it has locally. A
  `<meta>` CSP can't carry `frame-ancestors`. A custom policy costs nothing.
- **An S3 website endpoint.** Public bucket, HTTP only to the origin, no OAC. Rejected.

## Consequences

- The site's URL is the distribution's domain (`cloudfront_domain_name` output). The page stays
  up when the server is down and shows its error state. The server sees at most one API request
  per edge location and query string every 30 seconds.
- **TLS floor:** with the default certificate, CloudFront fixes the viewer security policy at
  `TLSv1`, so `TLSv1.2_2021` (asked for in #17) can't be set. Modern browsers negotiate TLS 1.3
  anyway. Requiring 1.2 needs an ACM certificate on a custom domain (parking lot).
- Without `s3:ListBucket`, a missing page answers 403, not 404.
- Objects without `Cache-Control` stay in the edge caches for a day. The deploy (#18) must set
  `Cache-Control` on upload (as nginx does: `index.html` no-cache, JS and CSS 300 s) or
  invalidate.
- The CSP is now in two places: `deploy/compose/web/nginx.conf` and
  `deploy/terraform/release/cloudfront.tf`. Change them together.
- The API docs page (`/api/docs`) is served with Swagger UI from the metrics-api image (pinned
  npm tarball, SHA-256 checked) instead of FastAPI's default CDN page, which the CSP blocks.
  Its CSP adds only `img-src data:`. ReDoc is dropped.
- Cost: €0 a month within the always-free allowances. The Free plan account is billed the same
  way; CloudFront's flat-rate plans are a separate opt-in and aren't used.
- Revisit with a custom domain (certificate, HTTPS to the origin, TLS 1.2 floor), when the API
  stops being public, or if traffic nears the free allowance.
