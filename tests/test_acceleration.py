"""Golden-value tests for the acceleration simulation.

The bug these lock: the Acceleration view integrated over the speed array
built from the TORQUE plot's x-axis limit (auto-filled to 0..80 km/h) and
broke out of the loop the moment the speed reached the end of that array.
Any vehicle able to exceed the x-limit therefore stopped mid-run, long before
"Max Simulation Time", and reported that x-limit as its "Final" speed -- a
plot-axis number presented as a physical top speed.

Reported with CdA 0.5, Crr 0.022, 90/90 tyre: below roughly 5 kW the run
completed normally, above it the curve stopped in the middle of the plot.

Two contracts:
  * the simulation always covers the full requested duration and converges on
    the real top speed (independently cross-checked against the net-force
    zero crossing);
  * cases the old code DID complete keep their numbers -- the switch from a
    nearest-neighbour force lookup to linear interpolation must not move the
    calibrated 0-60 times.
"""

import numpy as np
import pytest

from vmi.calc_ext import simulate_acceleration, top_speed_from_net_force
from vmi.physics import g
from vmi.torque_force import TorqueForceMixin


# --- Reported vehicle -------------------------------------------------------
MASS = 180.0
CRR = 0.022
CDA = 0.5
WHEEL_R = 0.266          # 90/90 tyre
PARAMS = {"m_i": MASS, "Crr": CRR, "CdA": CDA, "gradient": 0}


class _Host(TorqueForceMixin):
    """Minimal host exposing only what the accel helpers touch (the real host
    is the full TorqueSpeedApp; the mixin never needs a widget tree here)."""

    def __init__(self, max_time=60.0, gear_eff=1.0, inertial_mass=None):
        self._max_time = max_time
        self._gear_eff = gear_eff
        self._inertial_mass = inertial_mass

    class _Entry:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    @property
    def max_time(self):
        return _Host._Entry(str(self._max_time))

    def get_gear_efficiency_value(self):
        return self._gear_eff

    def cap_torque_to_battery(self, torque, rpm):
        return torque

    def get_effective_inertial_mass(self, mass_kg, wheel_radius_m=None):
        return float(self._inertial_mass if self._inertial_mass else mass_kg)


def _run(peak_torque, peak_power_kw, host=None, params=None, start=80.0,
         gear_ratio=1.0, wheel_radius=WHEEL_R):
    host = host or _Host()
    params = params or PARAMS
    fn = host._accel_wheel_force_fn(wheel_radius, gear_ratio, host._gear_eff,
                                    peak_torque=peak_torque,
                                    peak_power_kw=peak_power_kw)
    return host._accel_simulate(fn, params, wheel_radius, start_max_kmh=start)


class TestTopSpeedFromNetForce:
    def test_crossing_is_interpolated(self):
        speeds = np.array([0.0, 10.0, 20.0, 30.0])
        net = np.array([100.0, 50.0, -50.0, -200.0])
        assert top_speed_from_net_force(speeds, net) == pytest.approx(15.0)

    def test_cannot_launch_returns_zero(self):
        speeds = np.array([0.0, 10.0, 20.0])
        assert top_speed_from_net_force(speeds, np.array([-5.0, -9.0, -20.0])) == 0.0

    def test_no_crossing_returns_none(self):
        """Grid too short to reach the top speed -> caller must grow it."""
        speeds = np.array([0.0, 10.0, 20.0])
        assert top_speed_from_net_force(speeds, np.array([100.0, 90.0, 80.0])) is None

    def test_first_crossing_wins_on_non_monotonic_curve(self):
        # A later feasible region is unreachable -- the vehicle already stopped
        # accelerating at the first crossing.
        speeds = np.array([0.0, 10.0, 20.0, 30.0, 40.0])
        net = np.array([100.0, -100.0, 50.0, 50.0, -50.0])
        assert top_speed_from_net_force(speeds, net) == pytest.approx(5.0)


class TestSimulateAcceleration:
    def test_runs_the_full_requested_duration(self):
        speeds = np.linspace(0.0, 200.0, 2000)
        net = 800.0 - 0.5 * 1.225 * CDA * (speeds / 3.6) ** 2 - MASS * g * CRR
        t, v, info = simulate_acceleration(speeds, net, MASS, 60.0, dt_s=0.01)
        assert t[-1] == pytest.approx(60.0)
        assert t[0] == 0.0 and v[0] == 0.0
        assert len(t) == len(v)

    def test_converges_on_the_net_force_zero_crossing(self):
        speeds = np.linspace(0.0, 300.0, 3000)
        net = 800.0 - 0.5 * 1.225 * CDA * (speeds / 3.6) ** 2 - MASS * g * CRR
        top = top_speed_from_net_force(speeds, net)
        t, v, info = simulate_acceleration(speeds, net, MASS, 300.0, dt_s=0.01)
        assert info["top_speed_kmh"] == pytest.approx(top)
        assert v[-1] == pytest.approx(top, abs=0.05)
        assert info["settled_at_s"] is not None

    def test_speed_never_decreases_and_never_goes_negative(self):
        speeds = np.linspace(0.0, 200.0, 2000)
        net = 600.0 - 0.5 * 1.225 * CDA * (speeds / 3.6) ** 2 - MASS * g * CRR
        _, v, _ = simulate_acceleration(speeds, net, MASS, 120.0, dt_s=0.01)
        assert v.min() >= 0.0
        assert np.all(np.diff(v) >= -1e-9)

    def test_cannot_launch_is_flagged_not_silently_flat(self):
        speeds = np.linspace(0.0, 100.0, 500)
        net = np.full_like(speeds, -50.0)
        t, v, info = simulate_acceleration(speeds, net, MASS, 10.0, dt_s=0.01)
        assert info["launched"] is False
        assert info["top_speed_kmh"] == 0.0
        assert np.all(v == 0.0)
        assert t[-1] == pytest.approx(10.0)

    def test_still_accelerating_has_no_settle_time(self):
        speeds = np.linspace(0.0, 400.0, 2000)
        net = np.full_like(speeds, 500.0)        # never crosses zero
        _, _, info = simulate_acceleration(speeds, net, MASS, 5.0, dt_s=0.01)
        assert info["settled_at_s"] is None
        assert info["top_speed_kmh"] is None


class TestGridCoversRealTopSpeed:
    """The regression proper: the reported CdA/Crr/tyre combination."""

    @pytest.mark.parametrize("peak_torque,peak_power_kw,expected_top", [
        (180, 6.0, 91.4),
        (200, 8.0, 101.7),
        (250, 12.0, 117.8),
        (400, 20.0, 141.2),
    ])
    def test_high_power_no_longer_truncates_at_the_80_kmh_x_limit(
            self, peak_torque, peak_power_kw, expected_top):
        t, v, info = _run(peak_torque, peak_power_kw)
        # Full duration, not a mid-plot stop at the x-axis limit.
        assert t[-1] == pytest.approx(60.0)
        # ... and it goes well past the 80 km/h the old code stopped at.
        assert v[-1] > 80.0
        assert info["top_speed_kmh"] == pytest.approx(expected_top, abs=0.1)
        assert v[-1] == pytest.approx(info["top_speed_kmh"], abs=0.1)

    def test_grid_grows_until_the_force_actually_crosses_zero(self):
        host = _Host()
        fn = host._accel_wheel_force_fn(WHEEL_R, 1.0, 1.0,
                                        peak_torque=400, peak_power_kw=20.0)
        speeds, net = host._accel_net_force_grid(fn, PARAMS, start_max_kmh=80.0)
        assert speeds[-1] > 80.0          # the 80 km/h hint was not a ceiling
        assert net[-1] < 0                # top speed is bracketed
        assert top_speed_from_net_force(speeds, net) is not None


class TestCompletedCasesKeepTheirNumbers:
    """Cases the OLD code finished must be numerically unchanged -- these are
    calibrated results, and only the truncation was a bug."""

    @pytest.mark.parametrize("peak_torque,peak_power_kw,old_final,old_t60", [
        (180, 2.4, 63.78696, 23.315),
        (180, 3.0, 69.87241, 14.405),
        (180, 4.0, 78.29085, 9.518),
    ])
    def test_low_power_results_unchanged(self, peak_torque, peak_power_kw,
                                         old_final, old_t60):
        t, v, _ = _run(peak_torque, peak_power_kw)
        assert t[-1] == pytest.approx(60.0)
        # Nearest-neighbour -> linear force lookup moves this by ~1e-4 km/h.
        assert v[-1] == pytest.approx(old_final, abs=1e-3)
        reached = np.where(v >= 60.0)[0]
        assert t[reached[0]] == pytest.approx(old_t60, abs=1e-3)


class TestGradientAndInertia:
    def test_steep_gradient_cannot_launch(self):
        params = dict(PARAMS, gradient=40)
        _, v, info = _run(180, 2.4, params=params)
        assert info["launched"] is False
        assert np.all(v == 0.0)

    def test_gradient_slows_acceleration(self):
        flat = _run(250, 12.0)[2]["top_speed_kmh"]
        hill = _run(250, 12.0, params=dict(PARAMS, gradient=7))[2]["top_speed_kmh"]
        assert hill < flat

    def test_wheel_inertia_only_slows_the_ma_term(self):
        """Top speed is a steady-state result -- inertia must not change it."""
        plain = _run(250, 12.0)
        heavy = _run(250, 12.0, host=_Host(inertial_mass=MASS + 20.0))
        assert heavy[2]["top_speed_kmh"] == pytest.approx(
            plain[2]["top_speed_kmh"], abs=1e-6)
        # ... but it must take longer to get there.
        idx_p = np.where(plain[1] >= 60.0)[0][0]
        idx_h = np.where(heavy[1] >= 60.0)[0][0]
        assert heavy[0][idx_h] > plain[0][idx_p]


class TestUploadedMotorCurve:
    def test_uploaded_curve_is_held_flat_outside_its_range(self):
        host = _Host()
        rpm = np.array([0.0, 1000.0, 2000.0])
        torque = np.array([180.0, 180.0, 90.0])
        fn = host._accel_wheel_force_fn(WHEEL_R, 1.0, 1.0,
                                        motor_curve=(rpm, torque))
        # Well past the curve's last point -> last torque held (as the torque
        # views do), so the force is 90 * 1 * 1 / r.
        far = np.array([500.0])
        assert fn(far)[0] == pytest.approx(90.0 / WHEEL_R)

    def test_unsorted_curve_is_sorted_before_interpolation(self):
        host = _Host()
        rpm = np.array([2000.0, 0.0, 1000.0])
        torque = np.array([90.0, 180.0, 180.0])
        fn = host._accel_wheel_force_fn(WHEEL_R, 1.0, 1.0,
                                        motor_curve=(rpm, torque))
        sorted_fn = host._accel_wheel_force_fn(
            WHEEL_R, 1.0, 1.0,
            motor_curve=(np.array([0.0, 1000.0, 2000.0]),
                         np.array([180.0, 180.0, 90.0])))
        probe = np.linspace(0.0, 120.0, 50)
        np.testing.assert_allclose(fn(probe), sorted_fn(probe))
