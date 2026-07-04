"""Golden-value regression tests for the verbatim calculation core.

The expected numbers below were captured from the code as it stands today.
If any of these tests ever fails, a formula or calibration value changed --
which, per CLAUDE.md, must only happen on an explicit request.
"""

import pytest

from vmi.physics import calculate_crr_cd_a, g


def test_gravity_constant():
    assert g == 9.81


@pytest.mark.parametrize(
    "m_ref, expected",
    [
        (180, {"m_i": 180.0, "Crr": 0.0179, "CdA": 0.49664}),
        (20.5, {"m_i": 20.0, "Crr": 0.01835, "CdA": 0.44413}),
        (350, {"m_i": 350.0, "Crr": 0.01794, "CdA": 0.55352}),
        (500, {"m_i": 500.0, "Crr": 0.01794, "CdA": 0.60166}),
    ],
)
def test_auto_crr_cda_defaults(m_ref, expected):
    assert calculate_crr_cd_a(m_ref) == expected


def test_env_corrections_affect_cda_estimate():
    out = calculate_crr_cd_a(180, rear_load_ratio=0.6, ambient_temp=35,
                             ambient_pressure=0.95)
    assert out == {"m_i": 180.0, "Crr": 0.01491, "CdA": 0.54748}


def test_manual_values_pass_through_unchanged():
    out = calculate_crr_cd_a(180, crr=0.015, cd_a=0.42)
    assert out["Crr"] == 0.015
    assert out["CdA"] == 0.42
    assert out["m_i"] == 180.0


def test_out_of_table_mass_allowed_with_manual_values():
    out = calculate_crr_cd_a(600, crr=0.012, cd_a=0.5)
    assert out == {"m_i": 600.0, "Crr": 0.012, "CdA": 0.5}


def test_out_of_table_mass_without_manual_values_raises():
    with pytest.raises(ValueError):
        calculate_crr_cd_a(600)
