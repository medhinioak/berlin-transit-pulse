"""Transport modes derived from GTFS route types, including VBB's extended types."""

from enum import StrEnum


class Mode(StrEnum):
    """Mode of a route, stored as routes.mode."""

    BUS = "bus"
    TRAM = "tram"
    UBAHN = "ubahn"
    SBAHN = "sbahn"
    REGIONAL = "regional"
    FERRY = "ferry"
    OTHER = "other"


def mode_for_route_type(route_type: int) -> Mode:
    """Map a basic (0-4) or extended GTFS route type to a mode; unknown types give OTHER."""
    if route_type == 109:
        return Mode.SBAHN
    if 400 <= route_type <= 499 or route_type == 1:
        return Mode.UBAHN
    if 700 <= route_type <= 799 or 200 <= route_type <= 299 or route_type == 3:
        return Mode.BUS
    if 900 <= route_type <= 999 or route_type == 0:
        return Mode.TRAM
    if 100 <= route_type <= 199 or route_type == 2:
        return Mode.REGIONAL
    if 1000 <= route_type <= 1099 or 1200 <= route_type <= 1299 or route_type == 4:
        return Mode.FERRY
    return Mode.OTHER
