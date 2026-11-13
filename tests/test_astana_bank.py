import pytest

from app.astana_bank import astana_bank


@pytest.mark.parametrize(("latitude", "longitude", "expected"), [
    (51.1283, 71.4306, "left"),  # Baiterek
    (51.1321, 71.4037, "left"),  # Khan Shatyr
    (51.161, 71.434, "right"),  # Samal, near Republic Avenue
    (51.165116, 71.561159, "right"),  # Kuidaiberdyuly 36/3
    (51.127275, 71.4459, "left"),  # Between crossings of the Ak Orda bend
    (51.1518972, 71.428229, "unknown"),  # Esil centerline
    (51.1527, 71.428229, "unknown"),  # Within the near-river margin
    (None, 71.4306, "unknown"),
    (51.1283, None, "unknown"),
    (float("nan"), 71.4306, "unknown"),
    (51.1283, float("inf"), "unknown"),
    (51.1283, 71.3, "unknown"),  # Beyond covered river reach
    (51.24, 71.4306, "unknown"),  # Outside urban study area
])
def test_astana_bank(latitude, longitude, expected):
    assert astana_bank(latitude, longitude) == expected
