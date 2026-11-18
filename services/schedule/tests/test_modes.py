"""mode_for_route_type covers every basic and extended range VBB uses."""

import pytest

from btp_schedule.modes import Mode, mode_for_route_type


@pytest.mark.parametrize(
    ("route_type", "mode"),
    [
        (109, Mode.SBAHN),
        (400, Mode.UBAHN),
        (499, Mode.UBAHN),
        (700, Mode.BUS),
        (799, Mode.BUS),
        (200, Mode.BUS),
        (299, Mode.BUS),
        (900, Mode.TRAM),
        (999, Mode.TRAM),
        (100, Mode.REGIONAL),
        (106, Mode.REGIONAL),
        (199, Mode.REGIONAL),
        (1000, Mode.FERRY),
        (1099, Mode.FERRY),
        (1200, Mode.FERRY),
        (1299, Mode.FERRY),
        (0, Mode.TRAM),
        (1, Mode.UBAHN),
        (2, Mode.REGIONAL),
        (3, Mode.BUS),
        (4, Mode.FERRY),
        (5, Mode.OTHER),
        (500, Mode.OTHER),
        (1100, Mode.OTHER),
        (1300, Mode.OTHER),
    ],
)
def test_mode_for_route_type(route_type: int, mode: Mode) -> None:
    """Each range boundary maps to its mode; anything else is OTHER."""
    assert mode_for_route_type(route_type) is mode
