"""
Calculation EXTENSIONS.

These are *new, opt-in* physics helpers. They are intentionally written so that
at their default arguments they reproduce the original program's behaviour
exactly:

    * air_density_isa is only used when the user ticks "Altitude-corrected
      air density"; otherwise the code keeps the original hard-coded 1.225.
    * rolling_force reduces to the original constant-Crr force when the speed
      coefficient crr1 == 0 (the default).
    * trapz_energy_wh is only used when the user selects trapezoidal
      integration; the default remains the original cumulative-sum method.
    * apply_regen_cap is a no-op when cap_w is None (the default).

Nothing in physics.py was modified; the verbatim calculation core is untouched.
"""

import numpy as np

# Sea-level reference used by the original code everywhere except one typo.
RHO_AIR_DEFAULT = 1.225          # kg/m^3 at ISA sea level, 15 degC
R_SPECIFIC_AIR = 287.05          # J/(kg.K)


def air_density_isa(altitude_m=0.0, temp_c=15.0, pressure_kpa=None):
    """Air density from the International Standard Atmosphere.

    At altitude_m=0 and temp_c=15 this returns ~1.225 kg/m^3, matching the
    original constant. If pressure_kpa is given it overrides the altitude
    model. Density falls ~1%/100 m, so e.g. Bengaluru at ~900 m, 25 degC is
    closer to 1.10 kg/m^3 than 1.225.
    """
    if pressure_kpa is not None:
        p = float(pressure_kpa) * 1000.0
    else:
        T0, L, g0, M, Ru = 288.15, 0.0065, 9.80665, 0.0289644, 8.3144598
        h = max(float(altitude_m), 0.0)
        p = 101325.0 * (1.0 - L * h / T0) ** (g0 * M / (Ru * L))
    T = 273.15 + float(temp_c)
    return p / (R_SPECIFIC_AIR * T)


def rolling_force(mass_kg, crr0, speed_mps, g, theta=0.0, crr1=0.0):
    """Rolling-resistance force with optional velocity dependence.

    Crr(v) = crr0 + crr1 * v. With crr1 == 0 (default) this is identical to
    the original  m * g * Crr * cos(theta).
    """
    speed_mps = np.asarray(speed_mps, dtype=float)
    crr_v = crr0 + crr1 * speed_mps
    return mass_kg * g * crr_v * np.cos(theta) * np.ones_like(speed_mps)


def trapz_energy_wh(power_w, time_s):
    """Cumulative energy (Wh) by trapezoidal integration of power over time.

    More accurate than rectangular cumsum on acceleration phases. Returns a
    cumulative array the same length as power_w (first element 0).
    """
    power_w = np.asarray(power_w, dtype=float)
    time_s = np.asarray(time_s, dtype=float)
    out = np.zeros_like(power_w)
    if power_w.size < 2:
        return out
    seg = 0.5 * (power_w[1:] + power_w[:-1]) * np.diff(time_s)   # W*s per segment
    out[1:] = np.cumsum(seg) / 3600.0                            # -> Wh
    return out


def apply_regen_cap(regen_power_w, cap_w=None):
    """Clip regen (battery charging) power to a maximum acceptance, in W.

    cap_w=None (default) returns the input unchanged. Use this to model
    charge-acceptance / current limits that the original flat model ignored.
    """
    regen_power_w = np.asarray(regen_power_w, dtype=float)
    if cap_w is None:
        return regen_power_w
    return np.clip(regen_power_w, 0.0, float(cap_w))


def check_energy_invariants(metrics):
    """Sanity-check a dict of per-km energy terms; return a list of warnings.

    Cheap guard against future regressions. Empty list == all good.
    """
    warns = []
    for key in ("aerodynamic_loss_per_km", "rolling_loss_per_km",
                "grade_loss_per_km", "motor_loss_per_km",
                "controller_loss_per_km"):
        v = metrics.get(key)
        if v is not None and v < -1e-6:
            warns.append(f"{key} is negative ({v:.3f})")
    for key in ("motor_eff", "controller_eff", "drive_cycle_eff"):
        v = metrics.get(key)
        if v is not None and not (0.0 <= v <= 1.0 + 1e-6):
            warns.append(f"{key} outside (0,1]: {v:.3f}")
    return warns
