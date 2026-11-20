# web

The minimal live page (M1, #10): trips running in Berlin now and their current delay, per
mode, from [`/v1/now/by-mode`](../services/metrics-api/README.md#get-v1nowby-mode). Plain
HTML, CSS and JavaScript: no framework, no build step, no third-party requests. The M4 UI
picks its framework with an ADR.

```sh
make up    # http://localhost:8080
```

The compose service `web` (nginx, [config](../deploy/compose/web/nginx.conf)) serves this
folder read-only and proxies `/api/` to `metrics-api:8000`. Edits show on reload; `app.js`
and `styles.css` are cached for 5 minutes, so force-reload after changing them.

| File | What |
| --- | --- |
| `index.html` | Page structure, column notes, VBB attribution (CC-BY 4.0) |
| `app.js` | Fetches every 60 s (no overlap, paused while the tab is hidden), renders with `textContent` only |
| `styles.css` | Mobile first (fits 320 px), light and dark, no animation |

## States

- **Loading**: until the first answer.
- **Error**: any failed request, non-200 answer or unexpected body hides the table and says
  "Live data is unavailable right now"; the next refresh tries again.
- **Stale**: the data is more than 5 minutes old. Its time is the older of `as_of` and the
  rows' `latest_feed_ts`. In live mode `as_of` is the query time, so the feed time is what
  shows a stopped feed. In fixtures mode `as_of` is the recording time (ADR-0019), so the stale
  note is always shown there. That is correct: it is a replay.
- **No trips running**: the API returns no rows. The page says the live data has no running
  trips (normal at night; otherwise the feed may be incomplete) instead of showing zeros.

A dash means the API has no value (e.g. no running trip with a known delay). The page never
computes figures except the "Off schedule" share (`trips_with_nonzero_delay / running_trips`) and
the unit change to minutes.

## Checks

No automated tests yet. Before a change: keyboard through the page, check 320 px and dark
mode, stop `metrics-api` (`docker compose -f deploy/compose/compose.yaml stop metrics-api`)
to see the error state, and confirm in the network tab that only this origin is requested
(the `Content-Security-Policy` header enforces it).
