# CloudFront: the project's public HTTPS URL (the default *.cloudfront.net domain).
# Two origins: the web bucket through Origin Access Control for the page, and the instance on
# port 80 for /api/*, open to CloudFront's origin-facing ranges only.
# Decisions and their reasons: docs/adr/0021-serve-the-site-through-cloudfront-with-an-s3-origin-and-a-cloudfront-only-ec2-origin.md

# --- Managed policies (AWS-owned, looked up by name) ---

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

# Caches only as long as the origin's Cache-Control says (the API sends max-age=30), and keys
# on the query string. Without a Cache-Control header the response isn't cached.
data "aws_cloudfront_cache_policy" "use_origin_cache_control" {
  name = "UseOriginCacheControlHeaders-QueryStrings"
}

# Forwards viewer headers and query strings, but not Host: the origin sees its own DNS name.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

data "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

# --- Response headers of the page ---

# The managed SecurityHeadersPolicy has no Content-Security-Policy, and S3 sends none. This
# policy adds the CSP and Referrer-Policy that nginx sends locally (deploy/compose/web/nginx.conf),
# with the managed policy's other headers. Keep the CSP in step with nginx.conf.
resource "aws_cloudfront_response_headers_policy" "web" {
  name    = "btp-release-web"
  comment = "Security headers and CSP for the static page"

  security_headers_config {
    content_security_policy {
      content_security_policy = "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
      override                = true
    }

    content_type_options {
      override = true
    }

    # DENY rather than the managed policy's SAMEORIGIN, matching frame-ancestors 'none'.
    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      override                   = true
    }

    xss_protection {
      protection = true
      mode_block = true
      override   = true
    }
  }
}

# --- Distribution ---

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "btp-release-web"
  description                       = "CloudFront reads the web bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  comment             = "berlin-transit-pulse release"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"
  is_ipv6_enabled     = true # viewers only; CloudFront reaches the origin over IPv4

  origin {
    origin_id                = "web"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  # An origin can't be an IP address, so this is the Elastic IP's public DNS name.
  origin {
    origin_id           = "api"
    domain_name         = aws_eip.server.public_dns
    connection_timeout  = 10
    connection_attempts = 3

    custom_origin_config {
      origin_protocol_policy = "http-only"
      http_port              = 80
      https_port             = 443
      origin_ssl_protocols   = ["TLSv1.2"] # required by the API, unused with http-only
      origin_read_timeout    = 10
    }
  }

  # Everything that isn't /api/*: the static page from S3. Objects without Cache-Control are
  # cached for a day; the deploy (#18) sets Cache-Control on upload or invalidates.
  default_cache_behavior {
    target_origin_id           = "web"
    viewer_protocol_policy     = "redirect-to-https" # the bucket refuses non-TLS requests
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.web.id
  }

  # nginx on the instance strips /api/ and proxies to metrics-api.
  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.use_origin_cache_control.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # The default *.cloudfront.net certificate. CloudFront then fixes the security policy at
  # TLSv1; TLSv1.2_2021 needs our own certificate on a custom domain (docs/parking-lot.md).
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Name = "btp-release"
  }
}

# --- Origin protection: port 80 from CloudFront only ---

# The prefix list's maximum size (about 55 entries) counts against the security group's
# rules quota (60 inbound by default), so it fits in one rule with room to spare.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc_security_group_ingress_rule" "server_http_from_cloudfront" {
  security_group_id = aws_security_group.server.id
  description       = "HTTP from CloudFront origin-facing ranges"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
}
