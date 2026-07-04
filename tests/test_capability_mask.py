"""Tests for the efficiency-map capability mask (acceptance rule, 2026-07).

Rule: base RPM = peak power / peak torque (P[W]/T[Nm] rad/s -> RPM).
  * RPM <= base RPM: acceptable iff |T| <= peak torque
  * RPM  > base RPM: acceptable iff |T| * omega <= peak power
"""

import numpy as np
import pytest

from vmi.efficiency import EfficiencyMixin


class _Entry:
    def __init__(self, text):
        self._text = text

    def get(self):
        return self._text


class _Host(EfficiencyMixin):
    """Minimal stand-in exposing just the fields the mask reads."""

    def __init__(self, peak_torque="120", peak_power_kw="20"):
        self.motor1_max_torque = _Entry(peak_torque)
        self.motor1_max_power = _Entry(peak_power_kw)
        self.motor2_max_torque = _Entry(peak_torque)
        self.motor2_max_power = _Entry(peak_power_kw)


def _accepted(host, rpm, torque, motor=1):
    m = host._motor_capability_mask(np.array([[torque]]), np.array([[rpm]]), motor=motor)
    return bool(m[0, 0])


class TestAcceptanceRule:
    # 120 Nm / 20 kW -> base RPM = 20000/120 rad/s = 1591.5 RPM
    def test_constant_torque_region(self):
        h = _Host()
        assert _accepted(h, 1000, 120.0)        # at peak torque -> OK
        assert not _accepted(h, 1000, 121.0)    # above peak torque -> reject

    def test_power_limited_region(self):
        h = _Host()
        # At 3000 RPM (314.16 rad/s): 63.6 Nm -> 19.98 kW OK; 64.5 Nm -> 20.26 kW reject
        assert _accepted(h, 3000, 63.6)
        assert not _accepted(h, 3000, 64.5)
        # At 6000 RPM: 31.8 Nm -> 19.98 kW OK; 32.5 Nm -> 20.42 kW reject
        assert _accepted(h, 6000, 31.8)
        assert not _accepted(h, 6000, 32.5)

    def test_at_base_speed_peak_torque_is_acceptable(self):
        h = _Host()
        assert _accepted(h, 1591, 120.0)

    def test_symmetric_in_torque_for_regen(self):
        h = _Host()
        assert _accepted(h, 3000, -63.6)
        assert not _accepted(h, 3000, -64.5)

    def test_blank_or_invalid_params_disable_masking(self):
        assert _Host("", "20")._motor_capability_mask(
            np.array([[10.0]]), np.array([[1000.0]])) is None
        assert _Host("0", "20")._motor_capability_mask(
            np.array([[10.0]]), np.array([[1000.0]])) is None

    def test_grid_coverage_shape(self):
        h = _Host()
        S, T = np.meshgrid(np.linspace(1, 6000, 120), np.linspace(0.5, 120, 120))
        m = h._motor_capability_mask(T, S, motor=1)
        cov = float(np.mean(m))
        # Envelope of a 120 Nm / 20 kW motor over the 6000x120 rectangle.
        assert 0.55 < cov < 0.70
