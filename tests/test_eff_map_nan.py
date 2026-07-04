"""Datasheet efficiency maps with blank (NaN) cells above the torque-speed
curve must stay blank, and the autofilled peak power must come from the
populated cells, not the full map rectangle (2026-07 fix: the old median
back-fill painted 'valid' efficiency over the unreachable region and the
rectangle corner power made the capability mask a no-op)."""

import numpy as np
import pytest

from vmi.efficiency import EfficiencyMixin


class _Entry:
    def __init__(self, text=""):
        self._text = str(text)

    def get(self):
        return self._text

    def delete(self, _start, _end):
        self._text = ""

    def insert(self, _index, text):
        self._text = str(text)


class _Host(EfficiencyMixin):
    """Minimal stand-in exposing the widgets normalize/autofill touch."""

    def __init__(self):
        for m in (1, 2):
            setattr(self, f"motor{m}_max_speed", _Entry())
            setattr(self, f"motor{m}_rated_speed", _Entry())
            setattr(self, f"motor{m}_max_torque", _Entry())
            setattr(self, f"motor{m}_max_power", _Entry())
            setattr(self, f"motor{m}_max_speed_manual", False)
            setattr(self, f"motor{m}_rated_speed_manual", False)
            setattr(self, f"motor{m}_max_torque_manual", False)
            setattr(self, f"motor{m}_max_power_manual", False)


class TestNormalizeKeepsNaN:
    def test_nan_cells_stay_nan(self):
        h = _Host()
        tq = np.array([0.0, 60.0, 120.0])
        rpm = np.array([100.0, 400.0, 700.0])
        eff = np.array([[0.80, 0.85, 0.82],
                        [0.88, 0.90, np.nan],   # unreachable corner
                        [0.75, np.nan, np.nan]])
        _tqo, _rpmo, out = h._normalize_efficiency_map_data(tq, rpm, eff)
        assert np.isnan(out[1, 2]) and np.isnan(out[2, 1]) and np.isnan(out[2, 2])
        assert out[0, 0] == pytest.approx(0.80)

    def test_percent_maps_still_convert(self):
        h = _Host()
        tq = np.array([10.0, 20.0])
        rpm = np.array([100.0, 200.0])
        eff = np.array([[80.0, 85.0], [90.0, np.nan]])
        _tqo, _rpmo, out = h._normalize_efficiency_map_data(tq, rpm, eff)
        assert out[0, 0] == pytest.approx(0.80)
        assert np.isnan(out[1, 1])

    def test_all_nan_still_raises(self):
        h = _Host()
        with pytest.raises(ValueError):
            h._normalize_efficiency_map_data(
                np.array([1.0, 2.0]), np.array([1.0, 2.0]),
                np.full((2, 2), np.nan))


class TestAutofillFromValidCells:
    def test_full_map_keeps_rectangle_power(self):
        """No NaN holes -> unchanged behavior: corner power of the rectangle."""
        h = _Host()
        tq = np.array([10.0, 120.0])
        rpm = np.array([0.0, 6000.0])
        full = np.full((2, 2), 0.9)
        h._autofill_motor_params_from_map(1, tq, rpm, full)
        expected = 120.0 * 6000.0 * 2 * np.pi / 60.0 / 1000.0
        assert float(h.motor1_max_power.get()) == pytest.approx(expected, rel=1e-3)
        assert float(h.motor1_max_torque.get()) == pytest.approx(120.0)

    def test_holey_map_uses_populated_cells(self):
        """NaN above the torque-speed curve -> power = max |T|*w of real cells.
        Shaped like the U546 datasheet: 120 Nm flat to 200 RPM, ~2.5 kW after."""
        h = _Host()
        tq = np.array([30.0, 60.0, 120.0])
        rpm = np.array([100.0, 200.0, 400.0, 800.0])
        w = rpm * 2 * np.pi / 60.0
        peak_w = 120.0 * w[1]                      # 120 Nm at 200 RPM = 2513 W
        eff = np.where(tq[:, None] * w[None, :] <= peak_w + 1e-6, 0.9, np.nan)
        h._autofill_motor_params_from_map(1, tq, rpm, eff)
        # Field is written with 2-dp formatting, hence the absolute tolerance.
        assert float(h.motor1_max_power.get()) == pytest.approx(peak_w / 1000.0, abs=0.01)
        assert float(h.motor1_max_torque.get()) == pytest.approx(120.0)
        # And the resulting mask actually rejects the blank corner now.
        mask = h._motor_capability_mask(
            np.array([[120.0, 120.0]]), np.array([[100.0, 800.0]]), motor=1)
        assert bool(mask[0, 0]) and not bool(mask[0, 1])

    def test_manual_fields_survive_autofill(self):
        h = _Host()
        h.motor1_max_power.insert(0, "2.5")
        h.motor1_max_power_manual = True
        h._autofill_motor_params_from_map(
            1, np.array([10.0, 120.0]), np.array([0.0, 6000.0]),
            np.full((2, 2), 0.9))
        assert h.motor1_max_power.get() == "2.5"
