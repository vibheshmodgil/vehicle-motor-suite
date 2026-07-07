"""
Per-view engineering interpretation for the HTML report.

`view_interpretation_html(app, analysis, subtitle)` returns an HTML block
answering, for the specific plot just captured: what does this graph show,
why does it matter, what are the key observations (with the actual numbers
this session computed), and what design conclusions follow. It is called
from `_render_report_views`'s `snap()` AT CAPTURE TIME, so any state the
branch just set (the Parametric study type, the Range panel, the Mechanical
Design check, ...) is still active.

Ground rules:
  * Every number quoted here must come from the app's own inputs or from
    results it just computed (the `_last_*` stashes, the estimators, the
    loaded dataframes). Never invent a value; when data is missing, drop the
    sentence rather than guessing.
  * Everything is wrapped defensively -- a failed builder returns "" and the
    report simply carries the figure without an interpretation, exactly like
    before this module existed.
  * Returning "" for an analysis keeps the older `_report_observations_html`
    fallback behaviour for it (the generator only appends that block when
    the first view produced no interpretation).
"""

import numpy as np


# --------------------------------------------------------------------- #
#  HTML helpers                                                          #
# --------------------------------------------------------------------- #

def _box(what, why=None, bullets=None, implication=None):
    """Assemble one interpretation block. `what`/`why`/`implication` are
    sentences (or None); `bullets` a list of observation strings."""
    parts = ["<div class='interp'><p class='ihead'>Engineering interpretation</p>"]
    if what:
        parts.append(f"<p><strong>What this shows:</strong> {what}</p>")
    if why:
        parts.append(f"<p><strong>Why it matters:</strong> {why}</p>")
    if bullets:
        lis = "".join(f"<li>{b}</li>" for b in bullets if b)
        if lis:
            parts.append(f"<p><strong>Key observations:</strong></p><ul>{lis}</ul>")
    if implication:
        parts.append(f"<p><strong>Design implications:</strong> {implication}</p>")
    parts.append("</div>")
    return "".join(parts)


def _f(app, attr, default=None):
    """float(entry) or default."""
    try:
        return float(getattr(app, attr).get())
    except Exception:
        return default


# --------------------------------------------------------------------- #
#  Shared computations                                                   #
# --------------------------------------------------------------------- #

def _base_speed(app):
    """(base_rpm_motor, base_speed_kmh_vehicle) from the peak fields, or None."""
    t_pk = _f(app, "peak_torque")
    p_pk = _f(app, "peak_power")
    r = _f(app, "wheel_radius")
    gr = _f(app, "gear_ratio", 1.0)
    if not t_pk or not p_pk or not r or t_pk <= 0 or p_pk <= 0 or r <= 0:
        return None
    base_rpm = (p_pk * 1000.0 / t_pk) * 60.0 / (2.0 * np.pi)
    v_kmh = (base_rpm / max(abs(gr), 1e-9)) * 2.0 * np.pi * r / 60.0 * 3.6
    return base_rpm, v_kmh


def _road_load_split(app, cap, v_kmh):
    """(rolling_N, aero_N, total_N) on flat road at v_kmh, from cap['params']."""
    from .physics import g
    p = cap["params"]
    v = v_kmh / 3.6
    rolling = p["m_i"] * g * p["Crr"]
    aero = 0.5 * 1.225 * p["CdA"] * v ** 2
    return rolling, aero, rolling + aero


def _accel_target_rows(app, cap):
    """['0-40 km/h in x s', ...] for 40/60/80 + the user's target, each
    clipped to targets actually below the estimated top speed."""
    targets = [40.0, 60.0, 80.0]
    user_t = _f(app, "target_speed")
    if user_t and user_t not in targets:
        targets.append(user_t)
    t_max = _f(app, "max_time", 60.0) or 60.0
    rows = []
    for tgt in sorted(set(targets)):
        if cap["top_speed_kmh"] > 0 and tgt > cap["top_speed_kmh"]:
            rows.append(f"0–{tgt:.0f} km/h: not reachable "
                        f"(estimated top speed {cap['top_speed_kmh']:.1f} km/h).")
            continue
        t = app._estimate_acceleration_time(
            cap["speeds"], cap["force"], float(cap["params"]["m_i"]),
            cap["params"]["Crr"], cap["params"]["CdA"], tgt, t_max)
        if t is not None and np.isfinite(t):
            rows.append(f"0–{tgt:.0f} km/h in ≈ {t:.1f} s.")
        else:
            rows.append(f"0–{tgt:.0f} km/h: not reached within the {t_max:.0f} s window.")
    return rows


def _limiting_factor(app, cap):
    """Sentence naming what limits acceleration/top speed: torque region,
    power region, or the battery DC cap -- computed by comparing the
    battery-capped available force against the motor-only force."""
    bs = _base_speed(app)
    speeds = np.asarray(cap["speeds"], dtype=float)
    force = np.asarray(cap["force"], dtype=float)
    top = cap["top_speed_kmh"] if cap["top_speed_kmh"] > 0 else float(speeds[-1])
    in_range = speeds <= top
    if not np.any(in_range):
        return None
    parts = []
    if bs is not None:
        below = np.mean(speeds[in_range] <= bs[1]) * 100.0
        parts.append(
            f"Up to top speed the vehicle spends {below:.0f}% of its speed range in the "
            f"constant-torque region (below base speed {bs[1]:.1f} km/h) and "
            f"{100 - below:.0f}% in the constant-power region, so launch/gradeability "
            "are torque-limited while high-speed acceleration is power-limited.")
    # Battery binding? Rebuild the motor-only wheel force (no battery cap)
    # and see where the actual available force sits below it.
    try:
        t_pk = _f(app, "peak_torque")
        p_pk = _f(app, "peak_power")
        r = _f(app, "wheel_radius")
        gr = _f(app, "gear_ratio", 1.0)
        gear_eff = app.get_gear_efficiency_value()
        if t_pk and p_pk and r:
            rpm_motor = speeds / 3.6 / r * 60.0 / (2.0 * np.pi) * gr
            omega = np.maximum(rpm_motor * 2.0 * np.pi / 60.0, 1e-6)
            t_motor = np.minimum(t_pk, p_pk * 1000.0 / omega)
            force_nobatt = t_motor * gr * gear_eff / r
            binding = (force < force_nobatt - 1e-6) & in_range
            if np.any(binding):
                lo = float(speeds[binding].min())
                share = float(np.mean(binding[in_range])) * 100.0
                parts.append(
                    f"The battery DC limit is the binding constraint from about "
                    f"{lo:.0f} km/h upward ({share:.0f}% of the usable speed range): "
                    "there the motor could deliver more torque than the battery can "
                    "supply, so top-end performance is battery-limited, not motor-limited.")
            elif app.get_battery_dc_power_w() is not None:
                parts.append("The battery DC limit is set but never binds inside the "
                             "usable speed range — the motor itself is the constraint.")
    except Exception:
        pass
    return " ".join(parts) if parts else None


def _thermal_bullets(app, cap):
    """One bullet per thermal load point, judged against continuous and peak
    capability. The tool has no transient thermal model, so time-at-peak is
    stated as a datasheet check, never invented."""
    try:
        pts = app.compute_thermal_load_points()
    except Exception:
        return []
    if not pts:
        return []
    t_pk = _f(app, "peak_torque")
    ratio = _f(app, "peak_to_rated_torque_ratio", 2.0) or 2.0
    t_cont = t_pk / ratio if t_pk else None
    out = []
    for i, p in enumerate(pts, 1):
        head = (f"Load point {i}: {app.fmt_gradient(p['grad_pct'])} at "
                f"{p['v_kmh']:.0f} km/h for {p['duration_s']:g} s → "
                f"{p['motor_torque']:.1f} Nm @ {p['motor_rpm']:.0f} RPM motor "
                f"({p['wheel_torque']:.0f} Nm at the wheel).")
        if t_cont is None:
            out.append(head)
            continue
        if p["motor_torque"] <= t_cont:
            verdict = (f"Below the continuous rating ({t_cont:.1f} Nm): thermally "
                       "sustainable indefinitely at steady state.")
        elif t_pk and p["motor_torque"] <= t_pk:
            verdict = (f"Between continuous ({t_cont:.1f} Nm) and peak ({t_pk:.1f} Nm): "
                       "this relies on short-term overload. Verify the allowable "
                       f"time at this torque ({p['duration_s']:g} s demanded) against the "
                       "motor and controller thermal ratings — this tool has no "
                       "transient thermal model.")
        else:
            verdict = (f"EXCEEDS the peak torque rating ({t_pk:.1f} Nm) — the duty "
                       "point is not reachable; resize the motor or the gearing.")
        out.append(head + " " + verdict)
    return out


# --------------------------------------------------------------------- #
#  Powertrain Sizing / Acceleration                                      #
# --------------------------------------------------------------------- #

def _powertrain(app, subtitle):
    cap = app._report_vehicle_capability()
    if cap is None:
        return ""
    from .units import gradient_pct_to_deg
    bs = _base_speed(app)
    t_pk = _f(app, "peak_torque")
    p_pk = _f(app, "peak_power")
    gr = _f(app, "gear_ratio", 1.0)
    gear_eff = app.get_gear_efficiency_value()
    r = _f(app, "wheel_radius")
    ratio = _f(app, "peak_to_rated_torque_ratio", 2.0) or 2.0

    bullets = []
    implication = None

    if subtitle.startswith("Torque"):
        side = "wheel" if "Wheel" in subtitle else "motor shaft"
        what = (f"The motor's peak and continuous torque capability at the {side}, "
                "against the resistive torque demanded at each gradient. Where the "
                "capability curve crosses a gradient's resistive curve is the maximum "
                "sustainable speed on that gradient; no crossing above the demand "
                "means the gradient cannot be climbed at any speed.")
        why = ("This is the core sizing check: the vertical gap between capability "
               "and demand is the torque reserve available for acceleration, and the "
               "crossing points set top speed per gradient.")
    else:  # Force (Wheel)
        what = ("Tractive force available at the tyre contact patch versus road load "
                "(rolling resistance + aerodynamic drag + gradient force) across "
                "vehicle speed. The flat part of the capability curve is the "
                "constant-torque (traction-rich) region; the falling part is the "
                "constant-power hyperbola F = P/v.")
        why = ("Force–speed is the vehicle-level view of the same sizing: the area "
               "between available force and road load is what accelerates the mass "
               "(F_net = m·a), so a thin gap means sluggish response even if top "
               "speed is adequate.")

    # Sizing narrative numbers (all views share them; keep the heavy block
    # on the first Torque view, a shorter set elsewhere).
    if bs is not None and t_pk and p_pk:
        bullets.append(
            f"Peak torque {t_pk:.0f} Nm holds up to base speed "
            f"{bs[0]:.0f} RPM motor ≈ {bs[1]:.1f} km/h vehicle; beyond it torque falls "
            f"as P/ω at the {p_pk:.1f} kW peak-power limit. Continuous (rated) torque "
            f"is peak/{ratio:g} = {t_pk / ratio:.1f} Nm.")
    if gr and r:
        v_per_krpm = 1000.0 / max(abs(gr), 1e-9) * 2.0 * np.pi * r / 60.0 * 3.6
        bullets.append(
            f"Gearing: the {gr:g}:1 reduction multiplies motor torque by "
            f"{gr * gear_eff:.2f}x at the wheel (incl. gear efficiency {gear_eff:g}) "
            f"and maps 1000 motor RPM to {v_per_krpm:.1f} km/h — this single number "
            "is how the motor's torque-speed characteristic becomes the vehicle's "
            "force-speed characteristic.")
    if cap["top_speed_kmh"] > 0:
        rolling, aero, total = _road_load_split(app, cap, cap["top_speed_kmh"])
        bullets.append(
            f"Estimated flat-road top speed ≈ {cap['top_speed_kmh']:.1f} km/h, where "
            f"road load ({total:.0f} N) consumes all available force — split "
            f"{100 * rolling / total:.0f}% rolling ({rolling:.0f} N) / "
            f"{100 * aero / total:.0f}% aerodynamic ({aero:.0f} N). Above ~"
            f"{0.7 * cap['top_speed_kmh']:.0f} km/h drag dominates, so top speed "
            "responds to CdA far more than to mass.")
    bullets.append(
        f"Maximum startable gradient ≈ {cap['max_grad_pct']:.1f}% "
        f"({gradient_pct_to_deg(cap['max_grad_pct']):.1f}°), set by peak wheel torque "
        "at crawl speed (the constant-torque region).")
    if subtitle.startswith("Torque") and "Wheel" in subtitle:
        bullets += _accel_target_rows(app, cap)
        lim = _limiting_factor(app, cap)
        if lim:
            bullets.append(lim)
        thermal = _thermal_bullets(app, cap)
        if thermal:
            bullets.append("<em>Thermal duty check:</em>")
            bullets += thermal

    if cap["top_speed_kmh"] > 0 and cap["max_grad_pct"] > 0:
        implication = (
            "If top speed falls short, raise power or reduce CdA / gear ratio; if "
            "gradeability or launch falls short, raise peak torque or the gear ratio "
            "(they trade against top speed through the same reduction). Points that "
            "must be held for long durations should sit below the continuous curve, "
            "not just below the peak curve.")
    return _box(what, why, bullets, implication)


def _acceleration(app):
    cap = app._report_vehicle_capability()
    if cap is None:
        return ""
    what = ("Vehicle speed versus time under maximum available tractive force, "
            "integrated from F_net = F_available − F_roadload over the actual "
            "capability curve (battery cap included when set).")
    why = ("Acceleration feel is the most customer-visible sizing outcome; it also "
           "reveals which region of the motor map does the work.")
    bullets = _accel_target_rows(app, cap)
    lim = _limiting_factor(app, cap)
    if lim:
        bullets.append(lim)
    bs = _base_speed(app)
    implication = None
    if bs is not None:
        implication = (
            f"Time below {bs[1]:.0f} km/h is bought with torque (and gearing); time "
            "above it is bought with power. If the early phase is adequate but the "
            "top end lags, add power or reduce losses rather than torque.")
    return _box(what, why, bullets, implication)


# --------------------------------------------------------------------- #
#  Parametric Study                                                      #
# --------------------------------------------------------------------- #

def _parametric(app, subtitle):
    stash = getattr(app, "_last_parametric", None)
    if not stash or stash.get("graph_type") != subtitle:
        return ""
    what = (f"A sweep of the '{subtitle}' relationship, holding every other input at "
            "its current value; the crimson marker/star is the currently configured "
            "vehicle, so the curve reads as 'what happens if only this parameter moves'.")
    why = ("Parametric sweeps rank where design effort pays: a steep curve at the "
           "current point means high sensitivity (big return on improving that "
           "parameter), a flat one means the parameter is not worth chasing.")
    bullets = []
    implication = None

    if stash.get("kind") == "1d" and stash.get("y"):
        x = np.asarray(stash["x"], dtype=float)
        y = np.asarray(stash["y"], dtype=float)
        cur = float(stash["current_x"])
        finite = np.isfinite(y)
        param = stash.get("param", "parameter")
        bullets.append(
            f"Input {param}: swept {x.min():.4g} → {x.max():.4g}; current value "
            f"{cur:.4g}" + ("" if x.min() <= cur <= x.max() else " (outside the sweep)") + ".")
        if np.any(finite):
            y_cur = float(np.interp(cur, x, y)) if x.min() <= cur <= x.max() else None
            bullets.append(
                f"Output: ranges {np.nanmin(y):.4g} → {np.nanmax(y):.4g} over the sweep"
                + (f"; at the current {param} it is {y_cur:.4g}." if y_cur is not None else "."))
            # Trend + local sensitivity/elasticity around the current point.
            dy = np.diff(y[finite])
            if np.all(dy <= 1e-12):
                trend = "monotonically decreasing"
            elif np.all(dy >= -1e-12):
                trend = "monotonically increasing"
            else:
                trend = "non-monotonic"
            sens_txt = ""
            if y_cur is not None and cur > 0 and abs(y_cur) > 1e-12:
                h = (x.max() - x.min()) / max(len(x) - 1, 1)
                y_hi = float(np.interp(min(cur + h, x.max()), x, y))
                y_lo = float(np.interp(max(cur - h, x.min()), x, y))
                slope = (y_hi - y_lo) / max((min(cur + h, x.max()) - max(cur - h, x.min())), 1e-12)
                elasticity = slope * cur / y_cur
                sens_txt = (f" Local sensitivity ≈ {slope:.4g} per unit {param}; "
                            f"elasticity ≈ {elasticity:+.2f} (a 10% change in {param} "
                            f"moves the output about {10 * abs(elasticity):.1f}%).")
            bullets.append(f"The trend is {trend} over the swept range.{sens_txt}")
        implication = (
            f"Use the elasticity to prioritise: |elasticity| well below ~0.3 means "
            f"{param} is a weak lever for this output and effort is better spent on "
            "the other swept parameter or on the powertrain itself.")
    elif stash.get("kind") == "2d" and stash.get("map"):
        cda = np.asarray(stash["cda"], dtype=float)
        crr = np.asarray(stash["crr"], dtype=float)
        vmap = np.asarray(stash["map"], dtype=float)
        bullets.append(
            f"Inputs: CdA swept {cda.min():.3g} → {cda.max():.3g} m², "
            f"Crr swept {crr.min():.4g} → {crr.max():.4g}; current point "
            f"CdA = {stash.get('current_cda'):.4g}, Crr = {stash.get('current_crr'):.4g}.")
        if np.any(np.isfinite(vmap)):
            bullets.append(
                f"Output ({stash.get('cbar_label', 'value')}): "
                f"{np.nanmin(vmap):.4g} → {np.nanmax(vmap):.4g} over the grid.")
            cc, rr = stash.get("current_cda"), stash.get("current_crr")
            if cc is not None and rr is not None \
                    and cda.min() <= cc <= cda.max() and crr.min() <= rr <= crr.max():
                i = int(np.argmin(np.abs(crr - rr)))
                j = int(np.argmin(np.abs(cda - cc)))
                if np.isfinite(vmap[i, j]):
                    bullets.append(f"At the current vehicle the output is ≈ {vmap[i, j]:.4g}.")
        bullets.append(
            "The slope of the iso-lines gives the trade-off rate: where they run "
            "steeply against one axis, that parameter dominates locally; near-vertical "
            "lines mean CdA controls the outcome, near-horizontal lines mean Crr does.")
        implication = ("Aero effort (CdA) pays mostly at high cruise speeds, rolling "
                       "resistance (Crr) at low speeds and in stop-go duty — pick the "
                       "lever that matches where this vehicle actually operates.")
    return _box(what, why, bullets, implication)


# --------------------------------------------------------------------- #
#  Drive cycle                                                           #
# --------------------------------------------------------------------- #

def _cycle_stats(app):
    df_dc = getattr(app, "dataframe", None)
    if df_dc is None or "dc_time" not in df_dc or "dc_speed" not in df_dc:
        return None
    t = np.asarray(df_dc["dc_time"], dtype=float)
    v = np.asarray(df_dc["dc_speed"], dtype=float)
    ok = np.isfinite(t) & np.isfinite(v)
    t, v = t[ok], v[ok]
    if t.size < 3:
        return None
    order = np.argsort(t)
    t, v = t[order], v[order]
    v_mps = v / 3.6
    dur = float(t[-1] - t[0])
    dist_km = float(np.trapz(v_mps, t) / 1000.0)
    dt = np.diff(t)
    acc = np.diff(v_mps) / np.maximum(dt, 1e-9)
    moving = v > 0.5
    # Count contiguous accel / decel events (|a| > 0.1 m/s^2 runs).
    def _events(mask):
        return int(np.sum(np.diff(np.concatenate(([0], mask.astype(int)))) == 1))
    return dict(
        duration_s=dur, distance_km=dist_km,
        v_max=float(np.max(v)), v_avg=float(np.mean(v)),
        v_avg_moving=(float(np.mean(v[moving])) if np.any(moving) else 0.0),
        idle_pct=float(100.0 * np.mean(~moving)),
        n_accel=_events(acc > 0.1), n_decel=_events(acc < -0.1),
        a_max=float(np.max(acc)) if acc.size else 0.0,
        a_min=float(np.min(acc)) if acc.size else 0.0,
    )


def _cycle_chain_peaks(app):
    """Peak values along the vehicle-model chain: wheel/motor torque, motor
    speed, mechanical and battery power -- how the speed trace propagates
    through the model."""
    try:
        op = app._drive_cycle_operating_points()
    except Exception:
        return None
    if op is None:
        return None
    try:
        gr = _f(app, "gear_ratio", 1.0) or 1.0
        gear_eff = app.get_gear_efficiency_value()
        mt, mr, mp = op["motor_torque"], op["motor_rpm"], op["motor_power"]
        m_tq, m_rpm, m_mat, _ = app._resolve_range_efficiency_map(kind="motor")
        c_tq, c_rpm, c_mat, _ = app._resolve_range_efficiency_map(kind="controller")
        m_const = app._get_eff_constant(app.motor_eff_const, 0.90) \
            if getattr(app, "motor_eff_const", None) is not None else 0.90
        c_const = app._get_eff_constant(app.controller_eff_const, 0.95) \
            if getattr(app, "controller_eff_const", None) is not None else 0.95
        eta_m = app._interpolate_efficiency_or_constant(mt, mr, m_mat, m_tq, m_rpm, m_const)
        eta_c = app._interpolate_efficiency_or_constant(mt, mr, c_mat, c_tq, c_rpm, c_const)
        eta = np.clip(eta_m * eta_c, 1e-6, 1.0)
        batt = np.where(mp >= 0, mp / eta, mp * eta)
        return dict(
            max_wheel_tq=float(np.nanmax(mt * gr * gear_eff)),
            max_motor_tq=float(np.nanmax(mt)),
            max_motor_rpm=float(np.nanmax(mr)),
            max_mech_kw=float(np.nanmax(mp)) / 1000.0,
            max_batt_kw=float(np.nanmax(batt)) / 1000.0,
            op=op,
        )
    except Exception:
        return None


def _drive_cycle(app, subtitle):
    stats = _cycle_stats(app)
    if stats is None:
        return ""
    bullets = [
        f"Cycle: {stats['duration_s']:.0f} s, {stats['distance_km']:.2f} km; "
        f"v_max {stats['v_max']:.1f} km/h, v_avg {stats['v_avg']:.1f} km/h "
        f"({stats['v_avg_moving']:.1f} km/h while moving), idle {stats['idle_pct']:.0f}% of the time.",
        f"{stats['n_accel']} acceleration and {stats['n_decel']} deceleration events "
        f"(|a| > 0.1 m/s²); peak acceleration {stats['a_max']:.2f} m/s², "
        f"peak deceleration {abs(stats['a_min']):.2f} m/s².",
    ]
    chain = _cycle_chain_peaks(app)

    if subtitle == "Speed vs Time":
        what = ("The drive cycle's speed trace — the demand side of every downstream "
                "result (torque, power, efficiency, range all follow from it).")
        why = ("The model converts this trace point-by-point: v → wheel speed "
               "(ω = v/r) → motor speed (×GR); F = m·g·Crr·cosθ + ½ρ·CdA·v² + "
               "m·g·sinθ + m·a → wheel torque (×r) → motor torque (÷GR·η_gear) → "
               "mechanical power (T·ω) → battery power (÷η_motor·η_controller when "
               "motoring). Understanding the cycle is understanding the demand.")
        if chain:
            bullets.append(
                f"Propagated through the vehicle model, this cycle peaks at "
                f"{chain['max_wheel_tq']:.1f} Nm wheel torque = "
                f"{chain['max_motor_tq']:.1f} Nm motor torque, "
                f"{chain['max_motor_rpm']:.0f} RPM motor speed, "
                f"{chain['max_mech_kw']:.2f} kW mechanical and "
                f"≈ {chain['max_batt_kw']:.2f} kW battery power.")
        implication = ("A cycle with many accel/decel events rewards regen and low "
                       "inertia; a high-average-speed cycle rewards low CdA and a "
                       "motor that is efficient in its power region.")
        return _box(what, why, bullets, implication)

    # Scatter / heatmap views: operating-point density interpretation.
    what = ("Every time step of the cycle placed on the motor's torque-speed plane"
            + (" — the heatmap bins them so density (time or tractive energy) is "
               "visible at a glance." if "Heatmap" in subtitle else "."))
    why = ("Motor and controller losses are set by WHERE on this plane the vehicle "
           "actually operates, not by the ratings; the densest (and most "
           "energy-weighted) region is where efficiency optimisation pays.")
    if chain is not None:
        op = chain["op"]
        mt = np.asarray(op["motor_torque"], dtype=float)
        mr = np.asarray(op["motor_rpm"], dtype=float)
        mp = np.asarray(op["motor_power"], dtype=float)
        dt_hr = np.asarray(op["dt_hr"], dtype=float)
        motoring = mp > 0
        bs = _base_speed(app)
        if bs is not None and np.any(motoring):
            below = float(np.mean(mr[motoring] <= bs[0])) * 100.0
            bullets.append(
                f"{below:.0f}% of motoring time is spent below base speed "
                f"({bs[0]:.0f} RPM — the constant-torque region) and "
                f"{100 - below:.0f}% above it (constant-power region).")
        if np.any(motoring):
            e = mp[motoring] * dt_hr[motoring]
            if np.sum(e) > 1e-9:
                rpm_c = float(np.sum(mr[motoring] * e) / np.sum(e))
                tq_c = float(np.sum(np.abs(mt[motoring]) * e) / np.sum(e))
                rpm_t = float(np.median(mr[motoring]))
                tq_t = float(np.median(np.abs(mt[motoring])))
                bullets.append(
                    f"Time-weighted median operating point ≈ {tq_t:.1f} Nm @ "
                    f"{rpm_t:.0f} RPM; ENERGY-weighted centroid ≈ {tq_c:.1f} Nm @ "
                    f"{rpm_c:.0f} RPM — energy concentrates at higher load than time "
                    "does, so weight optimisation decisions by energy, not dwell time.")
                implication = (
                    f"Center the motor's high-efficiency island on ≈ {tq_c:.0f} Nm @ "
                    f"{rpm_c:.0f} RPM (the energy centroid). Efficiency gains far from "
                    "the dense region buy almost nothing on this cycle.")
                return _box(what, why, bullets, implication)
    return _box(what, why, bullets, None)


# --------------------------------------------------------------------- #
#  Drive Cycle Efficiency maps                                           #
# --------------------------------------------------------------------- #

def _eff_map_stats(app, matrix, tq_axis, rpm_axis):
    """Peak eta + location and capability-masked area shares by band."""
    if matrix is None or tq_axis is None or rpm_axis is None:
        return None
    m = np.asarray(matrix, dtype=float)
    tq = np.asarray(tq_axis, dtype=float)
    rpm = np.asarray(rpm_axis, dtype=float)
    S, T = np.meshgrid(rpm, tq)
    mask = app._motor_capability_mask(T, S, motor=1)
    vals = m.copy()
    if mask is not None:
        vals = np.where(mask, vals, np.nan)
    finite = np.isfinite(vals)
    if not np.any(finite):
        return None
    idx = np.unravel_index(np.nanargmax(vals), vals.shape)
    n = float(np.sum(finite))
    return dict(
        peak=float(np.nanmax(vals)) * 100.0,
        peak_tq=float(tq[idx[0]]), peak_rpm=float(rpm[idx[1]]),
        share_90=float(np.sum(vals[finite] >= 0.90)) / n * 100.0,
        share_80_90=float(np.sum((vals[finite] >= 0.80) & (vals[finite] < 0.90))) / n * 100.0,
        share_lt80=float(np.sum(vals[finite] < 0.80)) / n * 100.0,
    )


def _cycle_band_shares(app):
    """Time- and energy-weighted share of cycle operating points per
    combined-efficiency band, or None without a cycle."""
    chain = _cycle_chain_peaks(app)
    if chain is None:
        return None
    try:
        op = chain["op"]
        mt, mr, mp, dt_hr = (np.asarray(op[k], dtype=float)
                             for k in ("motor_torque", "motor_rpm", "motor_power", "dt_hr"))
        m_tq, m_rpm, m_mat, _ = app._resolve_range_efficiency_map(kind="motor")
        c_tq, c_rpm, c_mat, _ = app._resolve_range_efficiency_map(kind="controller")
        m_const = app._get_eff_constant(app.motor_eff_const, 0.90) \
            if getattr(app, "motor_eff_const", None) is not None else 0.90
        c_const = app._get_eff_constant(app.controller_eff_const, 0.95) \
            if getattr(app, "controller_eff_const", None) is not None else 0.95
        eta = np.clip(
            app._interpolate_efficiency_or_constant(mt, mr, m_mat, m_tq, m_rpm, m_const)
            * app._interpolate_efficiency_or_constant(mt, mr, c_mat, c_tq, c_rpm, c_const),
            1e-6, 1.0)
        motoring = mp > 0
        if not np.any(motoring):
            return None
        e = mp[motoring] * dt_hr[motoring]
        t = dt_hr[motoring]
        et = eta[motoring]
        def share(w, lo, hi):
            sel = (et >= lo) & (et < hi)
            return float(np.sum(w[sel]) / max(np.sum(w), 1e-12)) * 100.0
        return dict(
            t90=share(t, 0.90, 2), e90=share(e, 0.90, 2),
            t80=share(t, 0.80, 0.90), e80=share(e, 0.80, 0.90),
            tlo=share(t, 0.0, 0.80), elo=share(e, 0.0, 0.80),
        )
    except Exception:
        return None


def _dce(app, subtitle):
    if not subtitle:
        return ""   # placeholder capture (no maps loaded)
    ratio = _f(app, "peak_to_rated_torque_ratio", 2.0) or 2.0
    t_pk = _f(app, "peak_torque")
    is_motor = subtitle.startswith("Motor")
    is_ctrl = subtitle.startswith("Controller")
    is_regen = "Regen" in subtitle
    is_diff = "Difference" in subtitle
    mat = getattr(app, "eff2_map_matrix" if is_ctrl else "eff1_map_matrix", None)
    tq_ax = getattr(app, "eff2_map_torques" if is_ctrl else "eff1_map_torques", None)
    rpm_ax = getattr(app, "eff2_map_rpms" if is_ctrl else "eff1_map_rpms", None)

    if is_diff:
        what = ("Controller efficiency minus motor efficiency on a common grid — "
                "positive (one color family) where the controller outperforms the "
                "motor, negative where the motor does.")
        why = ("It localises the weaker component per operating region: system "
               "efficiency improvement effort should target whichever component "
               "drags the combined map down where the vehicle actually runs.")
        return _box(what, why, None, None)

    if is_regen:
        what = ("The motor map mirrored into the braking (negative-torque) "
                "half-plane — the efficiency applied when kinetic energy flows back "
                "to the battery.")
        why = ("No datasheet measures a separate regen map, so the tool assumes the "
               "motoring efficiency at |T| applies to braking too; this view makes "
               "that assumption visible instead of implicit.")
        m = getattr(app, "_last_dce_metrics", None)
        bullets = []
        if m and m.get("e_batt_in", 0) > 1e-9:
            bullets.append(
                f"On the loaded cycle, regen returns {m.get('e_regen', 0.0):.1f} Wh — "
                f"{100.0 * m.get('e_regen', 0.0) / m['e_batt_in']:.1f}% of the motoring "
                "battery energy.")
        return _box(what, why, bullets,
                    "If measured regen efficiency becomes available, compare it here; "
                    "the symmetric assumption is optimistic at very low speeds where "
                    "regen is typically cut off.")

    label = "controller" if is_ctrl else ("combined motor × controller" if "Combined" in subtitle else "motor")
    what = (f"The {label} efficiency over the torque-speed plane, clipped to the "
            "motor's reachable envelope (peak torque to base speed, then the power "
            "hyperbola). Blank regions are outside capability or missing data.")
    why = ("Every Wh the vehicle uses passes through this map; range and thermal "
           "load both follow from where the drive cycle sits on it.")
    bullets = []
    stats = None
    if "Combined" not in subtitle:
        stats = _eff_map_stats(app, mat, tq_ax, rpm_ax)
    if stats:
        bullets.append(
            f"Peak efficiency {stats['peak']:.1f}% at ≈ {stats['peak_tq']:.0f} Nm / "
            f"{stats['peak_rpm']:.0f} RPM. Of the reachable map area: "
            f"{stats['share_90']:.0f}% is ≥ 90% efficient, {stats['share_80_90']:.0f}% "
            f"is 80–90%, {stats['share_lt80']:.0f}% is below 80% (high-loss).")
        bullets.append(
            "High-loss regions sit where they always do: low-speed/high-torque "
            "(copper/conduction losses at near-stall) and high-speed/low-torque "
            "(iron and switching losses with little useful output).")
    if t_pk:
        bullets.append(
            f"The continuous operating region is the band below the rated torque "
            f"line ({t_pk / ratio:.1f} Nm); duty held above it is thermally "
            "time-limited even where the map shows good efficiency.")
    shares = _cycle_band_shares(app)
    if shares:
        bullets.append(
            f"Weighted by the loaded drive cycle: {shares['t90']:.0f}% of motoring "
            f"time ({shares['e90']:.0f}% of energy) runs at ≥ 90% combined efficiency, "
            f"{shares['t80']:.0f}% ({shares['e80']:.0f}%) at 80–90%, and "
            f"{shares['tlo']:.0f}% ({shares['elo']:.0f}%) below 80% — the last band "
            "is where consumption is being lost.")
        m = getattr(app, "_last_dce_metrics", None)
        if m:
            bullets.append(
                f"Cycle-level result: energy-weighted efficiency {m['energy_eff']:.1f}%, "
                f"simple average {m['avg_eff']:.1f}%.")
    implication = ("Match the sweet spot to the drive-cycle centroid (see the "
                   "operating-point density view): a gearing change shifts the whole "
                   "cycle horizontally across this map and is often the cheapest "
                   "efficiency gain available.")
    return _box(what, why, bullets, implication)


# --------------------------------------------------------------------- #
#  Range analysis                                                        #
# --------------------------------------------------------------------- #

def _range(app, subtitle):
    m = getattr(app, "_last_range_metrics", None)
    if not m:
        return ""
    bullets = []
    implication = None

    peak_kw = m.get("peak_battery_power_kw")
    if subtitle.startswith("Power"):
        what = ("Power at every stage of the chain — wheel, motor output, motor "
                "input, controller input, battery — over the cycle. The vertical "
                "gaps between traces are the per-stage losses at that instant.")
        why = ("Peak battery power sizes the pack's discharge capability and the "
               "controller current rating; the gap pattern shows which stage loses "
               "the most and when.")
        if peak_kw is not None:
            bullets.append(
                f"Peak battery power {peak_kw:.2f} kW at t = "
                f"{m.get('peak_battery_power_at_s', 0):.0f} s; the demand stays within "
                f"90% of that peak for {m.get('time_within_90pct_of_peak_s', 0):.0f} s "
                "in total (short spikes are fine; long dwells near peak stress the pack).")
            bullets.append(
                f"Average battery power while moving: "
                f"{m.get('avg_battery_power_moving_kw', 0):.2f} kW — the continuous "
                "requirement, versus the peak above "
                f"({peak_kw / max(m.get('avg_battery_power_moving_kw', 1e-9), 1e-9):.1f}x ratio).")
        implication = ("Size the battery's continuous discharge for the average-moving "
                       "power and its pulse rating for the peak; a high peak/average "
                       "ratio favours a power-optimised cell chemistry or a larger pack.")
    elif subtitle.startswith("Cumulative") or subtitle.startswith("Energy"):
        what = ("Cumulative energy per loss mechanism and per chain stage over the "
                "cycle — the running integral of the power panel.")
        why = ("The end values are the cycle's energy budget: they decide range "
               "directly (range = usable pack energy ÷ net Wh/km).")
        bullets.append(
            f"Total battery draw over the cycle: {m.get('total_battery_energy_wh', 0):.1f} Wh "
            f"over {m.get('trip_distance_km', 0):.2f} km = "
            f"{m.get('net_energy_loss_per_km', 0):.1f} Wh/km net.")
        acc_share = m.get("wheel_energy_share_accelerating_pct")
        if acc_share is not None:
            bullets.append(
                f"{acc_share:.0f}% of positive wheel energy is spent while "
                f"accelerating, {100 - acc_share:.0f}% while cruising/climbing — "
                + ("acceleration-dominated duty: mass and regen matter most."
                   if acc_share > 50 else
                   "cruise-dominated duty: CdA and rolling resistance matter most."))
        implication = ("Attack the largest end-value first; a 10% cut in the dominant "
                       "sink is worth more than eliminating a minor one entirely.")
    elif subtitle.startswith("Battery C-rate"):
        what = ("Battery load expressed as C-rate (current / capacity), the "
                "cell-level view of the power panel.")
        why = ("Cells are rated in C; sustained high C accelerates ageing and "
               "increases I²R loss, and the peak C must stay inside the cell's "
               "datasheet limit.")
        if m.get("peak_c_rate") is not None:
            bullets.append(f"Peak discharge ≈ {m['peak_c_rate']:.2f} C on this cycle.")
        implication = ("Keep sustained load under the cell's continuous C rating with "
                       "margin; if the peak approaches the pulse rating, add parallel "
                       "capacity or reduce the peak power demand.")
    elif "Loss" in subtitle or "Waterfall" in subtitle:
        what = ("The cycle's energy consumption split by mechanism, in Wh/km and as "
                "shares — where every kilometre's energy actually goes.")
        why = ("This ranking is the design agenda for range: it says which loss to "
               "engineer down first.")
        terms = {
            "aerodynamic": m.get("aerodynamic_loss_per_km", 0.0),
            "rolling": m.get("rolling_loss_per_km", 0.0),
            "grade": m.get("grade_loss_per_km", 0.0),
            "inertia": m.get("inertia_loss_motoring_per_km", 0.0),
            "transmission": m.get("transmission_loss_per_km", 0.0),
            "motor": m.get("motor_loss_per_km", 0.0),
            "controller": m.get("controller_loss_per_km", 0.0),
            "auxiliary": m.get("aux_loss_total_per_km", 0.0),
        }
        ranked = sorted(terms.items(), key=lambda kv: kv[1], reverse=True)
        gross = m.get("gross_loss_per_km", 0.0)
        if gross > 1e-9:
            bullets.append("Ranked sinks: " + ", ".join(
                f"{k} {v:.1f} Wh/km ({100 * v / gross:.0f}%)" for k, v in ranked if v > 0) + ".")
        regen = m.get("regen_energy_per_km", 0.0)
        if regen > 0 and gross > 1e-9:
            bullets.append(f"Regen recovers {regen:.1f} Wh/km "
                           f"({100 * regen / gross:.0f}% of gross).")
        if m.get("estimated_range_km") is not None:
            bullets.append(
                f"Resulting range: {m['estimated_range_km']:.1f} km from the usable "
                f"pack energy at {m.get('net_energy_loss_per_km', 0):.1f} Wh/km net.")
        implication = ("Range improvements compound: each Wh/km removed from the top "
                       "sink adds range AND reduces the pack size needed for the "
                       "same range target.")
    elif "Eff" in subtitle:
        what = ("The efficiency map actually used by the range model for this "
                "component, with the cycle's operating points overlaid.")
        why = ("If the point cloud misses the map's sweet spot, the consumption "
               "numbers above are being paid for it.")
        bullets.append(
            f"Cycle-average efficiencies (motoring): motor "
            f"{100 * m.get('motor_eff', 0):.1f}%, controller "
            f"{100 * m.get('controller_eff', 0):.1f}%, wheel-to-battery "
            f"{100 * m.get('drive_cycle_eff', 0):.1f}%.")
    elif subtitle.startswith("Drive"):
        what = ("The demand chain for this cycle: speed trace → wheel torque → "
                "motor torque → the torque-speed point cloud on the motor plane.")
        why = ("It documents how the speed profile propagates through the vehicle "
               "model into electrical demand — each panel is one conversion step "
               "(F = road load + m·a; T = F·r; ÷GR·η to the motor; ω from v/r·GR).")
    else:
        return ""
    return _box(what, why, bullets, implication)


# --------------------------------------------------------------------- #
#  Brief what/why for the remaining analyses                             #
# --------------------------------------------------------------------- #

def _engine(app):
    return _box(
        "Engine torque converted to tractive effort at the wheel through each "
        "gear, against the resistive curves — the IC-engine equivalent of the "
        "Powertrain Sizing view, with the gear-change envelope visible.",
        "It benchmarks the EV powertrain against an IC reference: an electric "
        "motor's flat low-speed torque typically replaces the first 2–3 gears.",
        None, None)


def _mtpa(app):
    return _box(
        "The PMSM's optimal d-q current trajectory: MTPA below base speed, "
        "field-weakening along the voltage limit above it, and MTPV where the "
        "optimum detaches from the current circle — with the resulting torque, "
        "power and current components versus speed.",
        "This is the control-level origin of the torque-speed envelope used "
        "everywhere else in the tool: base speed and the constant-power range "
        "follow directly from the machine's Ld/Lq/ψ and the voltage/current limits.",
        None,
        "A wider constant-power range needs more field-weakening capability "
        "(higher saliency or more current margin); check that the drive-cycle "
        "operating region stays inside the feasible envelope shown here.")


def _mech(app, subtitle):
    texts = {
        "Rotor Stress & Burst Speed": (
            "Centrifugal stress in the rotor versus radius/speed, against the "
            "material's allowable — the ultimate mechanical speed limit.",
            "The burst margin must cover the maximum overspeed (downhill, field "
            "loss), not just rated speed."),
        "Shaft Design (Static + Fatigue)": (
            "Static and fatigue (Goodman) safety factors for the shaft under the "
            "combined torque and bending loads, versus diameter.",
            "Fatigue, not static strength, sizes a motor shaft: the plotted SF "
            "must meet the handbook target at the chosen diameter."),
        "Press / Shrink Fit": (
            "Interface pressure of the rotor/shaft fit versus speed, including "
            "the centrifugal relief — with the loss-of-contact speed marked.",
            "Torque transmission fails when the fit pressure reaches zero; keep "
            "loss-of-contact comfortably above maximum operating speed."),
        "Bearing Life (L10)": (
            "L10 bearing life versus load for the selected bearing.",
            "Bearing life is usually the motor's service-life limit; check it at "
            "the real radial/axial loads, not nominal."),
        "Critical Speed & Balancing": (
            "First bending critical speed versus shaft diameter, with the "
            "operating speed and the ISO balance-grade context.",
            "Operating speed should clear the first critical with margin; "
            "balancing grade sets the vibration budget."),
    }
    what, why = texts.get(subtitle, (None, None))
    if what is None:
        return ""
    return _box(what, why, None,
                "The numeric summary below quotes the computed safety factors "
                "against the handbook targets — treat anything at or below target "
                "as a redesign flag, not a pass.")


def _bom(app, subtitle):
    try:
        obs = app._report_observations("Motor BOM (Cost & Weight)") or []
    except Exception:
        obs = []
    texts = {
        "Sankey - Cost": ("How the total cost flows down the assembly tree — ribbon "
                          "width is proportional to cost.",
                          "It shows structure: which assembly owns the money."),
        "Sankey - Weight": ("The same flow for mass.",
                            "Weight drives range and handling; its distribution "
                            "rarely matches the cost distribution."),
        "Pareto - Cost": ("Parts ranked by cost with the cumulative share line.",
                          "Cost reduction follows the Pareto rule — the first few "
                          "bars usually hold most of the opportunity."),
        "Pareto - Weight": ("Parts ranked by mass with the cumulative share line.",
                            "Same rule for mass reduction."),
        "Group Split - Cost": ("Cost grouped by the selected dimension (assembly / "
                               "category / active vs passive).",
                               "The active-vs-passive split separates electromagnetic "
                               "content (scales with performance) from structure."),
        "Compare A vs B - Cost": ("Group-level cost of BOM A vs BOM B with deltas.",
                                  "It quantifies an architecture decision (e.g. hub vs "
                                  "mid-mount) instead of debating it."),
    }
    what, why = texts.get(subtitle, (None, None))
    if what is None:
        return ""
    return _box(what, why, obs if subtitle == "Sankey - Cost" else None, None)


def _compare_std(app, subtitle):
    n = len(getattr(app, "selected_std_motors", []) or [])
    what = {
        "Torque": "Peak torque capability of the current motor against each saved "
                  "library motor, with per-motor gradient-crossing markers.",
        "Force": "The same comparison as tractive force at the wheel.",
        "Acceleration": "Simulated speed-time under each motor's capability.",
        "Efficiency Map": "Efficiency difference between the current motor's map and "
                          "the selected library motor's saved map.",
    }.get(subtitle)
    if what is None:
        return ""
    return _box(what,
                f"A like-for-like screen of {n} candidate motor(s) on THIS vehicle's "
                "mass, drag and gearing — the crossings show which candidates meet "
                "each gradient/top-speed target.",
                None,
                "Prefer the smallest motor that clears every target with margin; "
                "excess capability is cost and weight carried on every trip.")


# --------------------------------------------------------------------- #
#  Dispatcher                                                            #
# --------------------------------------------------------------------- #

def view_interpretation_html(app, analysis, subtitle):
    """HTML interpretation block for one captured report view ('' if none)."""
    try:
        if analysis == "Powertrain Sizing":
            return _powertrain(app, subtitle or "Torque - At Wheel")
        if analysis == "Acceleration":
            return _acceleration(app)
        if analysis == "Parametric Study":
            return _parametric(app, subtitle)
        if analysis == "Drive Cycle":
            return _drive_cycle(app, subtitle)
        if analysis == "Drive Cycle Efficiency":
            return _dce(app, subtitle)
        if analysis == "Range analysis":
            return _range(app, subtitle)
        if analysis == "Engine analysis":
            return _engine(app)
        if analysis == "MTPA / MTPV (PMSM)":
            return _mtpa(app)
        if analysis == "Mechanical Design (Motor)":
            return _mech(app, subtitle)
        if analysis == "Motor BOM (Cost & Weight)":
            return _bom(app, subtitle)
        if analysis == "Compare Standard Motor Data":
            return _compare_std(app, subtitle)
    except Exception:
        return ""
    return ""
