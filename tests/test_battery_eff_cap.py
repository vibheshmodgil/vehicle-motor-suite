"""Tests for the map-aware battery DC limit (2026-07).

The battery limit is evaluated AFTER the motor/controller efficiency maps:
|T|*omega <= Vdc*Idc * eta(T, omega), solved per point by fixed-point
iteration in calc_ext.cap_torque_to_power_via_eff. These lock:
  * the strict no-op contract (no power / no eta_fn -> identity);
  * equivalence with the constant cap when eta is constant;
  * the converged fixed point for a torque-dependent eta;
  * regen symmetry.
Also locks the gradient %/degree conversions added alongside.
"""

import numpy as np
import pytest

from vmi.calc_ext import (cap_torque_to_power, cap_torque_to_power_via_eff,
                          smooth_efficiency_matrix)
from vmi.units import gradient_deg_to_pct, gradient_pct_to_deg


class TestCapViaEffNoOp:
    def test_none_power_is_identity(self):
        t = np.array([10.0, 180.0, -50.0])
        w = np.array([10.0, 100.0, 300.0])
        out = cap_torque_to_power_via_eff(t, w, None, lambda tt, ww: np.ones_like(ww))
        np.testing.assert_array_equal(out, t)

    def test_none_eta_fn_is_identity(self):
        t = np.array([10.0, 180.0])
        w = np.array([10.0, 100.0])
        np.testing.assert_array_equal(cap_torque_to_power_via_eff(t, w, 2000.0, None), t)


class TestCapViaEffConstantEta:
    def test_matches_constant_cap(self):
        # eta = 0.9 constant: identical to cap_torque_to_power at 0.9 * p_dc.
        t = np.array([100.0, 100.0, 100.0])
        w = np.array([10.0, 100.0, 400.0])
        via_eff = cap_torque_to_power_via_eff(t, w, 2000.0, lambda tt, ww: np.full_like(ww, 0.9))
        const = cap_torque_to_power(t, w, 2000.0 * 0.9)
        np.testing.assert_allclose(via_eff, const)

    def test_unity_eta_matches_raw_dc_cap(self):
        t = np.array([100.0, 100.0])
        w = np.array([10.0, 100.0])
        via_eff = cap_torque_to_power_via_eff(t, w, 2000.0, lambda tt, ww: np.ones_like(ww))
        np.testing.assert_allclose(via_eff, [100.0, 20.0])


class TestCapViaEffTorqueDependentEta:
    @staticmethod
    def _step_eta(t, w):
        # 90% above 50 Nm, 80% below: the limit moves once the point is
        # pulled down onto it, so the solver must iterate to the fixed point.
        return np.where(np.abs(np.asarray(t)) > 50.0, 0.9, 0.8)

    def test_converges_to_low_torque_eta(self):
        # p=2000 W, w=100 rad/s. First pass: eta(100 Nm)=0.9 -> limit 18 Nm.
        # 18 < 50 -> eta 0.8 -> limit 16 Nm, stable: eta(16)=0.8 -> 16.
        out = cap_torque_to_power_via_eff(
            np.array([100.0]), np.array([100.0]), 2000.0, self._step_eta)
        np.testing.assert_allclose(out, [16.0])

    def test_untouched_below_the_limit(self):
        # At 10 rad/s the limit is 2000*0.8/10 = 160 Nm minimum; 100 passes.
        out = cap_torque_to_power_via_eff(
            np.array([100.0]), np.array([10.0]), 2000.0, self._step_eta)
        np.testing.assert_allclose(out, [100.0])

    def test_regen_clipped_symmetrically(self):
        out = cap_torque_to_power_via_eff(
            np.array([-100.0]), np.array([100.0]), 2000.0,
            lambda t, w: np.full_like(w, 0.8))
        np.testing.assert_allclose(out, [-16.0])


class TestSmoothEfficiencyMatrix:
    def test_fully_populated_map_is_untouched_even_with_sigma(self):
        # Measured data must NEVER be altered: a map with no gaps passes
        # through identically, so real sharp features (e.g. the base-speed
        # corner where peak torque meets the power hyperbola) stay sharp.
        mat = np.array([[0.8, 0.85], [0.82, 0.9]])
        out = smooth_efficiency_matrix(mat, sigma=2.0)
        np.testing.assert_array_equal(out, mat)

    def test_all_nan_returns_unchanged(self):
        mat = np.full((3, 3), np.nan)
        out = smooth_efficiency_matrix(mat, sigma=1.0)
        assert np.all(np.isnan(out))

    def test_fills_nan_holes(self):
        mat = np.array([[0.8, 0.8, 0.8],
                        [0.8, 0.8, np.nan],
                        [0.8, np.nan, np.nan]])
        out = smooth_efficiency_matrix(mat, sigma=0.0)
        assert np.all(np.isfinite(out))
        # nearest-neighbor fill of a uniform 0.8 field stays close to 0.8.
        np.testing.assert_allclose(out, 0.8, atol=1e-9)

    def test_measured_cells_keep_exact_values(self):
        # Only the synthetic (previously-NaN) cells may be blended; every
        # finite input cell must come back bit-identical.
        rng = np.random.default_rng(42)
        mat = rng.uniform(0.6, 0.95, size=(10, 10))
        mat[6:, 7:] = np.nan
        valid = np.isfinite(mat)
        out = smooth_efficiency_matrix(mat, sigma=1.5)
        np.testing.assert_array_equal(out[valid], mat[valid])
        assert np.all(np.isfinite(out))

    def test_reduces_grid_boundary_discontinuity_end_to_end(self):
        # Reproduces the reported bug: a coarse motor map with the usual
        # datasheet blank region (NaN above the torque-speed envelope)
        # creates a real efficiency discontinuity at an internal grid line.
        # Smoothing should shrink that discontinuity by a large factor.
        tq = np.linspace(0, 180, 8)
        rpm = np.linspace(0, 6000, 8)
        T, R = np.meshgrid(tq, rpm, indexing="ij")
        base = 0.72 + 0.20 * np.exp(-((T - 90) ** 2) / (2 * 50 ** 2)
                                    - ((R - 3000) ** 2) / (2 * 2500 ** 2))
        mat = base.copy()
        base_rpm = 1500.0
        peak_power_w = 180.0 * base_rpm * 2 * np.pi / 60.0
        envelope = np.where(R <= base_rpm, 180.0,
                            peak_power_w / np.maximum(R * 2 * np.pi / 60.0, 1e-9))
        mat[T > envelope + 1e-6] = np.nan

        from scipy.interpolate import RegularGridInterpolator

        def eta_at_180(matrix):
            interp = RegularGridInterpolator((tq, rpm), matrix,
                                             bounds_error=False, fill_value=None)
            rpm_scan = np.linspace(1, 6000, 4000)
            vals = interp(np.column_stack([np.full_like(rpm_scan, 180.0), rpm_scan]))
            vals = np.where(np.isfinite(vals), vals, 0.90)  # same default-fallback
            return np.max(np.abs(np.diff(vals)))

        raw_jump = eta_at_180(mat)
        smoothed_jump = eta_at_180(smooth_efficiency_matrix(mat, sigma=1.0))
        assert raw_jump > 0.1, raw_jump  # confirms the bug reproduces
        assert smoothed_jump < raw_jump * 0.3, (raw_jump, smoothed_jump)


class TestGradientUnits:
    def test_zero(self):
        assert gradient_deg_to_pct(0.0) == pytest.approx(0.0)
        assert gradient_pct_to_deg(0.0) == pytest.approx(0.0)

    def test_45_degrees_is_100_pct(self):
        assert gradient_deg_to_pct(45.0) == pytest.approx(100.0)
        assert gradient_pct_to_deg(100.0) == pytest.approx(45.0)

    def test_known_values(self):
        # tan(4 deg) = 0.0699... -> 6.993 %
        assert gradient_deg_to_pct(4.0) == pytest.approx(6.9927, abs=1e-3)
        assert gradient_pct_to_deg(12.3) == pytest.approx(7.0127, abs=1e-3)

    def test_round_trip(self):
        for deg in (0.5, 4.0, 10.0, 17.6, 30.0):
            assert gradient_pct_to_deg(gradient_deg_to_pct(deg)) == pytest.approx(deg)
