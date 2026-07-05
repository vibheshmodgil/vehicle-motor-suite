"""Golden-value tests for the battery-DC-limit and wheel-inertia helpers.

These lock two contracts:
  * the helpers are strict NO-OPS at their defaults (blank battery fields ->
    cap None; wheel inertia 0 -> plain mass), so every pre-existing number in
    the app is reproduced exactly when the new fields are left alone;
  * the capped/corrected values themselves, so a formula change can't slip
    through silently.
"""

import numpy as np
import pytest

from vmi.calc_ext import battery_power_cap_w, cap_torque_to_power, effective_mass


class TestBatteryPowerCap:
    def test_blank_fields_mean_no_cap(self):
        assert battery_power_cap_w(None, None) is None
        assert battery_power_cap_w(48.0, None) is None
        assert battery_power_cap_w(None, 50.0) is None
        assert battery_power_cap_w("", "50") is None

    def test_nonpositive_means_no_cap(self):
        assert battery_power_cap_w(0.0, 50.0) is None
        assert battery_power_cap_w(48.0, 0.0) is None
        assert battery_power_cap_w(-48.0, 50.0) is None

    def test_cap_is_v_times_i_times_eta(self):
        assert battery_power_cap_w(48.0, 50.0) == pytest.approx(2400.0)
        assert battery_power_cap_w(48.0, 50.0, 0.9) == pytest.approx(2160.0)
        assert battery_power_cap_w(72.0, 100.0, 0.855) == pytest.approx(6156.0)

    def test_invalid_eta_falls_back_to_1(self):
        assert battery_power_cap_w(48.0, 50.0, 0.0) == pytest.approx(2400.0)
        assert battery_power_cap_w(48.0, 50.0, 1.5) == pytest.approx(2400.0)
        assert battery_power_cap_w(48.0, 50.0, "bad") == pytest.approx(2400.0)


class TestCapTorqueToPower:
    def test_none_cap_is_identity(self):
        t = np.array([10.0, 180.0, -50.0])
        w = np.array([10.0, 100.0, 300.0])
        out = cap_torque_to_power(t, w, None)
        np.testing.assert_array_equal(out, t)

    def test_clips_only_above_the_cap(self):
        # cap 2000 W: at 10 rad/s the limit is 200 Nm (100 passes untouched),
        # at 100 rad/s the limit is 20 Nm (100 is clipped down to it).
        t = np.array([100.0, 100.0])
        w = np.array([10.0, 100.0])
        out = cap_torque_to_power(t, w, 2000.0)
        np.testing.assert_allclose(out, [100.0, 20.0])

    def test_regen_torque_clipped_symmetrically(self):
        out = cap_torque_to_power(np.array([-100.0]), np.array([100.0]), 2000.0)
        np.testing.assert_allclose(out, [-20.0])

    def test_scalar_input(self):
        assert float(cap_torque_to_power(180.0, 100.0, 2000.0)) == pytest.approx(20.0)


class TestEffectiveMass:
    def test_zero_inertia_is_plain_mass(self):
        assert effective_mass(180.0, 0.0, 0.266) == pytest.approx(180.0)
        assert effective_mass(180.0, -1.0, 0.266) == pytest.approx(180.0)

    def test_missing_radius_is_plain_mass(self):
        assert effective_mass(180.0, 0.5, None) == pytest.approx(180.0)
        assert effective_mass(180.0, 0.5, 0.0) == pytest.approx(180.0)

    def test_m_eff_is_m_plus_j_over_r_squared(self):
        # 0.7 kg.m^2 on r = 0.266 m -> + 0.7 / 0.070756 = +9.89315... kg
        assert effective_mass(180.0, 0.7, 0.266) == pytest.approx(189.8931539, abs=1e-6)
        assert effective_mass(100.0, 1.0, 0.5) == pytest.approx(104.0)
