# ADR-0017: Define "in Berlin" by DHID state code and mode by route type

- Status: Accepted
- Date: 2026-11-18
- Decider: Maintainer (@medhinioak)
- Review: rule set in issue #5, verified against the real feed in the same issue, 2026-11-18

## Context

The VBB feed covers all of Berlin and Brandenburg (plus some lines into Saxony, Saxony-Anhalt,
Mecklenburg and Poland). The v1 scope is Berlin, by mode (bus, tram, U-Bahn, S-Bahn, regional).
GTFS has no "city" field. VBB uses extended route types (109 S-Bahn, 400s U-Bahn, 700s bus,
900s tram, 100s regional, 1000s ferry). Lines cross the city boundary, e.g. S-Bahn to Potsdam
and RE1 to Frankfurt (Oder).

Every metric is filtered by these two flags, so the rule has to be simple, deterministic, and
checked against real data.

## Decision

- **Trip in Berlin:** a trip is in Berlin if any of its stops has a `stop_id` starting with
  `de:11000:`. That is Berlin's state code in the German stop-ID scheme (DHID).
- **Route in Berlin:** a route is in Berlin if any of its trips is.
- **Storage:** the flags are `trips.in_berlin` and `routes.in_berlin`, computed at import.
- **Mode:** `routes.mode` comes from `mode_for_route_type`:

  | Route type | Mode |
  | --- | --- |
  | 109 | sbahn |
  | 400–499 | ubahn |
  | 700–799, 200–299 | bus |
  | 900–999 | tram |
  | 100–199 | regional |
  | 1000–1099, 1200–1299 | ferry |
  | basic types 0–4 | tram, ubahn, regional, bus, ferry |
  | anything else | other |

  The mode follows the route type, not the line name. A rail replacement bus named `RE1` or
  `S41` is a `bus`.

## Verification (real zip `b6d49a90863e`, downloaded 2026-11-18)

Routes in Berlin out of all routes, by mode:

| bus | sbahn | tram | regional | ubahn | ferry |
| --- | --- | --- | --- | --- | --- |
| 281 of 1,101 | 47 of 51 | 31 of 50 | 28 of 74 | 9 of 9 | 6 of 8 |

- 202,750 of 256,783 trips are in Berlin.
- **Spot checks:** U8, S41, M10, 100 and RE1 are in Berlin.
- **Outside Berlin, as expected:**
  - No BVG route is outside Berlin.
  - The non-Berlin trams are in Potsdam, Cottbus, Frankfurt (Oder), Brandenburg an der Havel
    and Strausberg.
  - The non-Berlin route-type-109 routes are DB Regio lines around Leipzig, Dresden and Magdeburg.
- **Stops without a DHID:** 13,648 stops have none. Most are entrances and nodes. 341 are
  platforms, 29 of them in Berlin with a `de:11000:` parent station. Counting the parent station
  as well would change no trip and no route.

## Alternatives considered

- **Polygon of Berlin's boundary on stop coordinates:** precise, but it needs a geometry
  dependency and boundary data, and gives the same answer here.
- **Agency (BVG plus S-Bahn Berlin):** it would miss regional trains and other operators'
  buses in Berlin, and wrongly include S-Bahn trips that run only outside Berlin.
- **Stop name containing "(Berlin)":** it's a naming convention, not data. It's inconsistent
  across operators.
- **Counting a trip only if most of its stops are in Berlin:** it would drop the Berlin part of
  cross-border lines such as RE1. Per-stop metrics can filter by stop instead.

## Consequences

- **Easier:** one string prefix, computed while streaming stop_times at import. It needs no
  extra data and is cheap to explain on the site.
- **Harder:**
  - A trip that crosses the boundary counts as Berlin as a whole. Per-stop metrics that must
    exclude Brandenburg stops have to check the stop too, with the same prefix.
  - Platforms without a DHID rely on the trip having another Berlin stop.
- **Revisit if:** the check after a timetable change (next 2026-12-13) shows trips whose only
  Berlin stops lack a DHID. In that case, also count the parent station.
