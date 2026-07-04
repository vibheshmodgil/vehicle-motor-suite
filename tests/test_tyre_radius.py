"""Tests for the tyre static-radius calculation (bug fix 2026-07).

Static radius = rim diameter/2 + section height:
  metric 'W/A-R':  R*25.4/2 + W*(A/100)   (W in mm, A in %, R rim inches)
  inch   'W.WW-R': R*25.4/2 + W*25.4      (bias-ply, ~100% aspect)
"""

import pytest

from vmi.ui_helpers import HelpersMixin

radius = HelpersMixin.tyre_static_radius_m


@pytest.mark.parametrize(
    "spec, expected_m",
    [
        # Inch (bias-ply) sizes
        ("3.00-10", 0.2032),   # 10*25.4/2 + 3.00*25.4
        ("3.50-10", 0.2159),
        ("2.75-17", 0.2858),
        ("3.00-17", 0.2921),
        # Metric sizes
        ("90/90-12", 0.2334),  # 12*25.4/2 + 90*0.90
        ("90/100-12", 0.2424),
        ("100/80-12", 0.2324),
        ("110/70-12", 0.2294),
        ("110/80-12", 0.2404),
        ("120/70-10", 0.2110),
        ("80/100-17", 0.2959),
        ("90/90-17", 0.2969),
        ("110/70-17", 0.2929),
        ("140/70-17", 0.3139),
        ("90/90-19", 0.3223),
        ("110/90-19", 0.3403),
        ("120/90-17", 0.3239),
        ("150/80-16", 0.3232),
        ("170/80-15", 0.3265),
    ],
)
def test_static_radius_from_spec(spec, expected_m):
    assert radius(spec) == pytest.approx(expected_m, abs=1e-4)
