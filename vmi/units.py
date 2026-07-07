"""Shared unit-conversion helpers.

These formulas appear inline throughout the plot modules (km/h <-> m/s,
wheel speed <-> RPM, motor RPM via gear ratio, RPM <-> rad/s). Centralizing
them gives one tested implementation; the numbers are identical to the
inline versions. Adopt gradually -- new code should use these, existing
verbatim plot code may keep its inline math.
"""

import numpy as np

TWO_PI = 2.0 * np.pi


def kmh_to_mps(v_kmh):
    """km/h -> m/s."""
    return np.asarray(v_kmh, dtype=float) / 3.6


def mps_to_kmh(v_mps):
    """m/s -> km/h."""
    return np.asarray(v_mps, dtype=float) * 3.6


def mps_to_wheel_rpm(v_mps, wheel_radius_m):
    """Vehicle speed (m/s) -> wheel RPM."""
    return np.asarray(v_mps, dtype=float) / wheel_radius_m * 60.0 / TWO_PI


def kmh_to_wheel_rpm(v_kmh, wheel_radius_m):
    """Vehicle speed (km/h) -> wheel RPM."""
    return mps_to_wheel_rpm(kmh_to_mps(v_kmh), wheel_radius_m)


def wheel_rpm_to_kmh(rpm, wheel_radius_m):
    """Wheel RPM -> vehicle speed (km/h)."""
    return np.asarray(rpm, dtype=float) * TWO_PI * wheel_radius_m / 60.0 * 3.6


def wheel_rpm_to_motor_rpm(wheel_rpm, gear_ratio):
    """Wheel RPM -> motor RPM through the reduction."""
    return np.asarray(wheel_rpm, dtype=float) * gear_ratio


def rpm_to_rad_s(rpm):
    """RPM -> angular velocity (rad/s)."""
    return np.asarray(rpm, dtype=float) * TWO_PI / 60.0


def rad_s_to_rpm(omega):
    """Angular velocity (rad/s) -> RPM."""
    return np.asarray(omega, dtype=float) * 60.0 / TWO_PI


def gradient_deg_to_pct(angle_deg):
    """Incline angle (degrees) -> slope percentage (rise/run x 100).

    pct = tan(theta) * 100. The app's physics works in percent everywhere
    (theta = arctan(pct/100)), so a degree input converted here round-trips
    to exactly the entered angle.
    """
    return float(np.tan(np.radians(float(angle_deg))) * 100.0)


def gradient_pct_to_deg(pct):
    """Slope percentage -> incline angle in degrees (inverse of the above)."""
    return float(np.degrees(np.arctan(float(pct) / 100.0)))
