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
    * battery_power_cap_w returns None (no cap) when the battery voltage or
      DC current limit field is blank/invalid, and cap_torque_to_power is a
      strict no-op when cap_w is None.
    * effective_mass returns the plain mass unchanged when the wheel inertia
      is 0 (the default) or the radius is missing.

Nothing in physics.py was modified; the verbatim calculation core is untouched.
"""

import numpy as np
from scipy.ndimage import gaussian_filter

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


def battery_power_cap_w(voltage_v=None, current_a=None, eta=1.0):
    """Mechanical shaft-power cap implied by a battery DC current limit, in W.

    cap = Vdc * Idc * eta, where eta is the battery-to-shaft efficiency
    (controller x motor chain). Returns None (meaning "no cap") when either
    the voltage or the current limit is missing or non-positive, so blank
    battery fields reproduce the original unrestricted behaviour exactly.
    """
    try:
        v = float(voltage_v)
        i = float(current_a)
    except (TypeError, ValueError):
        return None
    if v <= 0 or i <= 0:
        return None
    try:
        e = float(eta)
    except (TypeError, ValueError):
        e = 1.0
    if not 0.0 < e <= 1.0:
        e = 1.0
    return v * i * e


def cap_torque_to_power(torque_nm, omega_rad_s, cap_w=None):
    """Clip a torque curve so |T| * omega never exceeds cap_w.

    Sign-preserving, so a regen (negative) torque is clipped symmetrically.
    cap_w=None (default) returns the input unchanged -- a strict no-op.
    """
    torque_nm = np.asarray(torque_nm, dtype=float)
    if cap_w is None:
        return torque_nm
    omega = np.maximum(np.abs(np.asarray(omega_rad_s, dtype=float)), 1e-9)
    limit = float(cap_w) / omega
    return np.clip(torque_nm, -limit, limit)


def cap_torque_to_power_via_eff(torque_nm, omega_rad_s, p_dc_w=None, eta_fn=None, iters=8):
    """Clip a torque curve so shaft power never exceeds the battery DC power
    TIMES the drivetrain efficiency at the (capped) operating point:

        |T| * omega <= p_dc_w * eta_fn(T, omega)

    This evaluates the battery limit AFTER the motor/controller efficiency
    maps rather than through a fixed battery-to-shaft chain efficiency.
    Because eta depends on the torque itself, each point is solved by
    fixed-point iteration T <- clip(T, +/- p_dc*eta(T, w)/w): the efficiency
    is re-read at the clipped point until it settles. Sign-preserving, so
    regen torque is clipped symmetrically (eta_fn is called with the signed
    torque; the app's map lookup uses |T|).

    p_dc_w=None or eta_fn=None -> identity, a strict no-op, so blank battery
    fields / missing maps reproduce the original behaviour exactly.
    """
    torque_nm = np.asarray(torque_nm, dtype=float)
    if p_dc_w is None or eta_fn is None:
        return torque_nm
    omega = np.maximum(np.abs(np.asarray(omega_rad_s, dtype=float)), 1e-9)
    p_dc = float(p_dc_w)
    capped = np.array(torque_nm, dtype=float, copy=True)
    for _ in range(int(iters)):
        eta = np.clip(np.asarray(eta_fn(capped, omega), dtype=float), 0.01, 1.0)
        limit = p_dc * eta / omega
        new = np.clip(torque_nm, -limit, limit)
        if np.allclose(new, capped, rtol=1e-6, atol=1e-9):
            capped = new
            break
        capped = new
    return capped


def smooth_efficiency_matrix(matrix, sigma=1.0):
    """Fill a map's NaN coverage gaps for the BATTERY-LIMIT lookup only
    (cap_torque_to_power_via_eff and the capability mask/envelope that share
    it), WITHOUT altering a single measured cell.

    Why this exists: a datasheet map's blank cells (above the torque-speed
    envelope) are correctly kept as NaN in the map itself (see
    efficiency._normalize_efficiency_map_data). But RegularGridInterpolator's
    bilinear formula propagates NaN through an ENTIRE grid cell the moment
    any one of its four corners is NaN, and the app then substitutes a flat
    constant (default_eff) for those NaN results -- creating a hard step
    between real map data and that constant exactly at the map's coverage
    edge, which is usually the very region the battery limit binds hardest.

    Contract (tightened 2026-07 after the first version blurred the whole
    matrix and visibly rounded off the REAL base-speed corner where the
    peak-torque plateau meets the power hyperbola):
      * every measured (finite) cell keeps its exact value -- genuine sharp
        features of the motor's behaviour are preserved;
      * only the synthetic cells (NaN filled by nearest-neighbor purely so
        bilinear interpolation has four finite corners) are Gaussian-blended,
        so the extension beyond the map's coverage is smooth rather than
        blocky;
      * a fully populated map, or an all-NaN one, is returned unchanged.
    """
    from scipy.interpolate import NearestNDInterpolator
    vals = np.asarray(matrix, dtype=float)
    valid = np.isfinite(vals)
    if not np.any(valid) or np.all(valid):
        return vals
    idx = np.indices(vals.shape)
    interp = NearestNDInterpolator(
        np.column_stack([idx[0][valid], idx[1][valid]]), vals[valid])
    filled = vals.copy()
    filled[~valid] = interp(np.column_stack([idx[0][~valid], idx[1][~valid]]))
    if sigma > 0:
        blurred = gaussian_filter(filled, sigma=sigma, mode="nearest")
        filled[~valid] = blurred[~valid]   # blend ONLY the synthetic cells
    return filled


def effective_mass(mass_kg, wheel_inertia_kgm2=0.0, wheel_radius_m=None):
    """Translational-equivalent vehicle mass including wheel rotational inertia.

    m_eff = m + J_total / r^2. Only the INERTIAL (m*a) terms use this --
    rolling/gradient forces keep the actual mass, since spinning wheels don't
    add weight. J <= 0 (default) or a missing/invalid radius returns the
    plain mass unchanged.
    """
    try:
        j = float(wheel_inertia_kgm2)
        r = float(wheel_radius_m)
    except (TypeError, ValueError):
        return float(mass_kg)
    if j <= 0 or r <= 0:
        return float(mass_kg)
    return float(mass_kg) + j / (r * r)


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
