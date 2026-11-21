// Starts Swagger UI for the API docs page (btp_metrics_api/docs.py).
// The page is served at /docs directly and at /api/docs behind nginx and CloudFront, so the
// prefix comes from the page's own path: the spec is fetched from it, and "Try it out" calls
// go through it. A static file rather than an inline script, so the CSP needs no hash.
"use strict";

const base = window.location.pathname.replace(/\/docs$/, "");

fetch(`${base}/openapi.json`)
  .then((response) => {
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  })
  .then((spec) => {
    spec.servers = [{ url: base || "/" }];
    window.SwaggerUIBundle({ spec, dom_id: "#swagger-ui" });
  })
  .catch((error) => {
    document.getElementById("swagger-ui").textContent =
      `Could not load the API description (${error.message}).`;
  });
