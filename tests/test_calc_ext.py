"""Tests for the opt-in advanced physics helpers.

The key property (per CLAUDE.md): at default arguments every helper must
reproduce the original model exactly -- advanced features are no-ops when off.
"""

import numpy as np
import pytest

from vmi.calc_ext import (
    RHO_AIR_DEFAULT,
    air_density_isa,
    apply_regen_cap,
    check_energy_invariants,
    rolling_force,
    trapz_energy_wh,
)


class TestAirDensityISA:
    def test_sea_level_15c_matches_original_constant(self):
        assert air_density_isa(0, 15.0) == pytest.approx(RHO_AIR_DEFAULT, abs=1e-4)

    def test_golden_values(self):
        assert air_density_isa(0, 15.0) == pytest.approx(1.2250122659906946)
        assert air_density_isa(900, 25.0) == pytest.approx(1.0629357805253197)
        assert air_density_isa(0, 25.0, pressure_kpa=95.0) == pytest.approx(1.1100211158148419)

    def test_density_falls_with_altitude(self):
        assert air_density_isa(1500, 25.0) < air_density_isa(0, 25.0)


class TestRollingForce:
    def test_crr1_zero_matches_original_constant_crr(self):
        # With crr1=0 (default), must equal m*g*Crr*cos(theta) at any speed.
        f = rolling_force(180, 0.0179, np.array([0.0, 10.0, 30.0]), 9.81)
        expected = 180 * 9.81 * 0.0179
        assert np.allclose(f, expected)

    def test_crr1_adds_speed_dependence(self):
        f = rolling_force(180, 0.0179, np.array([0.0, 10.0]), 9.81, crr1=0.001)
        assert f[1] > f[0]
        assert f[0] == pytest.approx(180 * 9.81 * 0.0179)

    def test_gradient_reduces_normal_load(self):
        flat = rolling_force(180, 0.0179, 10.0, 9.81, theta=0.0)
        hill = rolling_force(180, 0.0179, 10.0, 9.81, theta=np.deg2rad(10))
        assert hill < flat


class TestTrapzEnergy:
    def test_constant_power_equals_rectangular(self):
        p = np.full(11, 1000.0)          # 1 kW for 10 s
        t = np.arange(11, dtype=float)
        out = trapz_energy_wh(p, t)
        assert out[-1] == pytest.approx(1000.0 * 10 / 3600.0)

    def test_first_element_zero_and_monotonic_for_positive_power(self):
        p = np.array([0.0, 500.0, 1000.0, 800.0])
        t = np.array([0.0, 1.0, 2.0, 3.0])
        out = trapz_energy_wh(p, t)
        assert out[0] == 0.0
        assert np.all(np.diff(out) >= 0)

    def test_short_input_returns_zeros(self):
        assert trapz_energy_wh(np.array([100.0]), np.array([0.0])).tolist() == [0.0]


class TestRegenCap:
    def test_none_cap_is_identity(self):
        p = np.array([-100.0, 0.0, 500.0, 5000.0])
        assert np.array_equal(apply_regen_cap(p, None), p)

    def test_cap_clips_above_and_below(self):
        p = np.array([-100.0, 0.0, 500.0, 5000.0])
        out = apply_regen_cap(p, 1000.0)
        assert out.tolist() == [0.0, 0.0, 500.0, 1000.0]


class TestInvariants:
    def test_clean_metrics_produce_no_warnings(self):
        assert check_energy_invariants({
            "aerodynamic_loss_per_km": 5.0,
            "motor_eff": 0.9,
        }) == []

    def test_negative_loss_and_bad_eff_are_flagged(self):
        warns = check_energy_invariants({
            "rolling_loss_per_km": -1.0,
            "controller_eff": 1.4,
        })
        assert len(warns) == 2
