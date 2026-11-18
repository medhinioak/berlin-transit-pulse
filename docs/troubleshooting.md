# Troubleshooting log

Problems hit while building and running this project, what caused them and how they were
fixed. Newest first. Each entry has the same fields so it can be skimmed:
**Seen** (date, PR or issue, run), **Symptom**, **Cause**, **Fix**, **Status**.

Status is one of: _fixed_, _workaround_ (remove when the upstream fix lands), _open_.

## Feed

### VBB feed has been missing some data since 2026-06-04

- **Seen:** 2026-11-18, while confirming feed access before building.
- **Symptom:** the feed page says the feed "has been lacking some data since 2026-06-04 16:00"
  with no restoration estimate. A first check (2026-11-18, 11:37) showed live delays for
  bus, tram and U-Bahn, but only 9% of S-Bahn trips with a non-zero delay.
- **Cause:** upstream data source problems, per VBB. Which parts are missing isn't stated.
- **Fix:** none yet. S-Bahn coverage is being checked at rush hour (issue: "Decide S-Bahn scope").
  Coverage per mode will be tracked by the data-quality checks.
- **Status:** _open_

### Rate limit reduced to 7 requests per minute

- **Seen:** announced 2026-10-27, effective 2026-11-02.
- **Symptom:** more frequent requests are rejected; generic User-Agents may be blocked.
- **Cause:** excessive traffic on a budget shared by all consumers.
- **Fix:** poll every 30 s, send gzip and If-None-Match, and a User-Agent with contact details.
- **Status:** _fixed_ (by design)

## Tooling

### Workspace packages don't import on macOS after `uv sync`

- **Seen:** 2026-11-18, #2 (repo scaffolding), first `make install` on the Mac.
- **Symptom:** `uv pip list` shows all `btp-*` packages installed, but
  `uv run python -c "import btp_common"` fails with `ModuleNotFoundError`.
- **Cause:** macOS had set the `hidden` file flag (`ls -lO`) on most of the editable
  `.pth` files in `.venv/lib/python3.12/site-packages/`. Python 3.12.13 skips hidden
  `.pth` files, so the `src/` paths never reach `sys.path`. What sets the flag wasn't
  pinned down; `.venv` itself was flagged too.
- **Fix:** `make install` runs `chflags -R nohidden .venv` after `uv sync` on macOS.
  If imports break after a plain `uv sync` or `uv add`, run `make install` again.
- **Status:** _workaround_
