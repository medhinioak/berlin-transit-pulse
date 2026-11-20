// Fetches the live indicator from /api/v1/now/by-mode every minute and renders it.
// API data is written with textContent only, never innerHTML.
"use strict";

const API_URL = "/api/v1/now/by-mode";
const REFRESH_MS = 60_000;
const REQUEST_TIMEOUT_MS = 10_000;
const STALE_AFTER_S = 5 * 60;
const AGE_TICK_MS = 10_000;

// Fixed display order; a mode the page doesn't know goes last under its API name.
const MODE_LABELS = new Map([
  ["ubahn", "U-Bahn"],
  ["sbahn", "S-Bahn"],
  ["tram", "Tram"],
  ["bus", "Bus"],
  ["regional", "Regional rail"],
  ["ferry", "Ferry"],
  ["other", "Other"],
]);
const MODE_ORDER = [...MODE_LABELS.keys()];

const NO_VALUE = "–";
const berlinTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Berlin",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const oneDecimal = new Intl.NumberFormat("en-GB", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const percent = new Intl.NumberFormat("en-GB", { style: "percent", maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat("en-GB");

const el = {
  status: document.getElementById("status"),
  updated: document.getElementById("updated"),
  asOfTime: document.getElementById("as-of-time"),
  asOfAge: document.getElementById("as-of-age"),
  results: document.getElementById("results"),
  rows: document.getElementById("rows"),
  empty: document.getElementById("empty"),
};

let refreshTimer = null;
let ageTimer = null;
let inFlight = false;
let lastFetchAt = 0; // Date.now() of the last request
let asOf = null; // Date of the data currently shown, or null

/** Age of a time in seconds, never negative (the browser clock may be behind). */
function ageSeconds(date) {
  return Math.max(0, (Date.now() - date.getTime()) / 1000);
}

/** "12 seconds ago", "3 minutes ago", "2 hours ago", "2 days ago". */
function formatAge(seconds) {
  const units = [
    [86_400, "day"],
    [3_600, "hour"],
    [60, "minute"],
  ];
  for (const [size, name] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size);
      return `${n} ${name}${n === 1 ? "" : "s"} ago`;
    }
  }
  const s = Math.floor(seconds);
  return `${s} second${s === 1 ? "" : "s"} ago`;
}

/**
 * Shows a message in the status line; kind is "", "stale" or "error". The live region
 * stays rendered (empty when there is no message), so screen readers announce changes.
 */
function setStatus(text, kind) {
  el.status.textContent = text;
  el.status.className = kind ? `status status-${kind}` : "status";
}

/**
 * Time the data shown is from: the older of the API's as_of and the newest feed time.
 * In live mode as_of is the query time, so the feed time is what shows a stopped feed;
 * in fixtures mode as_of is the recording time (ADR-0019) and is the older one.
 */
function dataTime(data) {
  const times = [new Date(data.as_of).getTime()];
  for (const m of data.modes) {
    const t = new Date(m.latest_feed_ts).getTime();
    if (!Number.isNaN(t)) times.push(t);
  }
  // The feed time is the same on every row; with no rows only as_of is known.
  const feed = times.length > 1 ? Math.max(...times.slice(1)) : times[0];
  return new Date(Math.min(times[0], feed));
}

/** Minutes with one decimal, without a "-0.0" for small negative delays. */
function formatMinutes(seconds) {
  const tenths = Math.round(seconds / 6);
  return oneDecimal.format(tenths === 0 ? 0 : tenths / 10);
}

/** Updates the "x ago" text and the stale note for the data currently shown. */
function renderAge() {
  if (asOf === null) return;
  const age = ageSeconds(asOf);
  el.asOfAge.textContent = formatAge(age);
  const staleText =
    "This data is more than 5 minutes old. The live feed may be delayed or unavailable.";
  if (age > STALE_AFTER_S) {
    if (el.status.textContent !== staleText) setStatus(staleText, "stale");
  } else {
    setStatus("", "");
  }
}

/** Number cell: a formatted value, or a dash when the value is missing. */
function numCell(text) {
  const td = document.createElement("td");
  td.className = "num";
  td.textContent = text;
  return td;
}

/** One table row for one mode from the API. */
function modeRow(m) {
  const tr = document.createElement("tr");
  const th = document.createElement("th");
  th.scope = "row";
  th.textContent = MODE_LABELS.get(m.mode) ?? String(m.mode);
  tr.append(th);

  const running = Number.isFinite(m.running_trips) ? m.running_trips : null;
  const delayed = Number.isFinite(m.trips_with_nonzero_delay) ? m.trips_with_nonzero_delay : null;

  tr.append(numCell(running === null ? NO_VALUE : integer.format(running)));
  tr.append(
    numCell(Number.isFinite(m.avg_delay_s) ? formatMinutes(m.avg_delay_s) : NO_VALUE),
  );
  tr.append(
    numCell(running && delayed !== null ? percent.format(delayed / running) : NO_VALUE),
  );
  tr.append(
    numCell(
      Number.isFinite(m.cancelled_trips_today) ? integer.format(m.cancelled_trips_today) : NO_VALUE,
    ),
  );
  return tr;
}

/** Sort key: position in MODE_ORDER, unknown modes after all known ones. */
function modeRank(mode) {
  const i = MODE_ORDER.indexOf(mode);
  return i === -1 ? MODE_ORDER.length : i;
}

/** Renders a successful response. */
function render(data) {
  asOf = dataTime(data);
  el.asOfTime.dateTime = asOf.toISOString();
  el.asOfTime.textContent = berlinTime.format(asOf);
  el.updated.hidden = false;

  const modes = [...data.modes].sort((a, b) => modeRank(a.mode) - modeRank(b.mode));
  el.rows.replaceChildren(...modes.map(modeRow));
  el.empty.hidden = modes.length > 0;
  el.results.hidden = false;
  renderAge();
}

/** Hides the data and shows the error message; the next refresh retries. */
function renderError() {
  asOf = null;
  el.results.hidden = true;
  el.updated.hidden = true;
  setStatus("Live data is unavailable right now. Trying again in a minute.", "error");
}

/** Throws unless the body looks like a live-indicator response. */
function validate(data) {
  const ok =
    data !== null &&
    typeof data === "object" &&
    data.kind === "live-indicator" &&
    Array.isArray(data.modes) &&
    !Number.isNaN(new Date(data.as_of).getTime());
  if (!ok) throw new Error("unexpected response");
  return data;
}

/** Fetches once, renders, and schedules the next refresh (never overlapping). */
async function refresh() {
  if (inFlight) return;
  inFlight = true;
  lastFetchAt = Date.now();
  clearTimeout(refreshTimer);
  // AbortController rather than AbortSignal.timeout, which older iOS Safari lacks.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(API_URL, {
      headers: { Accept: "application/json" },
      // The API allows 30 s of caching (meant for the CDN, M2); the page always asks again,
      // so a reload during an outage shows the error, not a cached answer.
      cache: "no-cache",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(validate(await response.json()));
  } catch (err) {
    console.warn("refresh failed:", err);
    renderError();
  } finally {
    clearTimeout(timeout);
    inFlight = false;
    if (!document.hidden) refreshTimer = setTimeout(refresh, REFRESH_MS);
  }
}

/** Starts the age timer, and refreshes now or when the last request is REFRESH_MS old. */
function start() {
  clearInterval(ageTimer);
  ageTimer = setInterval(renderAge, AGE_TICK_MS);
  renderAge();
  clearTimeout(refreshTimer);
  const wait = lastFetchAt + REFRESH_MS - Date.now();
  if (wait <= 0) refresh();
  else refreshTimer = setTimeout(refresh, wait);
}

/** Stops both timers while the tab is hidden. */
function stop() {
  clearTimeout(refreshTimer);
  clearInterval(ageTimer);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stop();
  else start();
});

if (!document.hidden) start();
