"""MTPA / MTPV analysis for PM synchronous machines (d-q model).

Implements the strategies described in knowledge_base/scenarios/MTPA_MTPV.pdf:

  Flux linkages:  psi_d = Ld*id + psi_PM,   psi_q = Lq*iq
  Torque:         Te = 1.5*p*(psi_d*iq - psi_q*id)
                     = 1.5*p*(psi_PM*iq + (Ld - Lq)*id*iq)   (constant params)
  Current limit:  id^2 + iq^2 <= Imax^2                    (circle)
  Voltage limit:  (Ld*id + psi_PM)^2 + (Lq*iq)^2 <= (Vmax/w_e)^2   (ellipse)
                  with w_e = p * w_mech and Vmax set by the PWM scheme
                  (SVPWM Vdc/sqrt(3), sine PWM Vdc/2, six-step 2*Vdc/pi).
                  Stator resistance is neglected (steady state), as in the PDF.

For each speed the solver finds the maximum-torque current vector (id<=0,
iq>=0 motoring quadrant) subject to both limits, by sampling the two active
boundaries (current circle and voltage ellipse) and taking the best feasible
point. The operating regions follow the PDF's classification:

  MTPA (constant torque)  - the unconstrained MTPA point is voltage-feasible
  FW   (constant power)   - optimum still on the current circle, slid along it
  MTPV (deep flux-weaken) - optimum on the shrinking voltage ellipse

Saturation maps: `solve_mtpa_mtpv()` optionally accepts Ld / Lq / psi_PM maps
over an (id, iq) grid. When any map is supplied the solver switches to a dense
grid search of the motoring quadrant (the ellipse can no longer be inverted in
closed form), interpolating each parameter at every candidate current vector;
missing maps fall back to the constant values. With no maps the original
boundary-sampling path runs unchanged (golden values locked by tests).

Base speed is reported analytically: w_e_base = Vmax / |psi_s(MTPA point)|,
not the last feasible sample of the speed grid (which biased it low by up to
one grid step).

`solve_mtpa_mtpv()` is a pure function (testable without the GUI);
`MtpaMtpvMixin` provides the input section and the plots.
"""

import numpy as np
import customtkinter as ctk
from tkinter import filedialog, messagebox

import pandas as pd
from matplotlib.ticker import MultipleLocator
from scipy.interpolate import RegularGridInterpolator

from .theme import COLORS, FONTS
from .validation import parse_float

# Region codes used in the solver output.
REGION_MTPA = 0
REGION_FW = 1
REGION_MTPV = 2
REGION_INFEASIBLE = 3

_REGION_META = {
    REGION_MTPA: ("MTPA", "#2563eb"),
    REGION_FW: ("Flux-Weakening", "#f59e0b"),
    REGION_MTPV: ("MTPV", "#10b981"),
    REGION_INFEASIBLE: ("Infeasible", "#9ca3af"),
}

# PWM scheme -> peak fundamental phase voltage as a fraction of Vdc.
VOLTAGE_LIMIT_SCHEMES = {
    "SVPWM (Vdc/√3)": 1.0 / np.sqrt(3.0),
    "Sine PWM (Vdc/2)": 0.5,
    "Six-step (2·Vdc/π)": 2.0 / np.pi,
}
DEFAULT_VOLTAGE_SCHEME = "SVPWM (Vdc/√3)"

CONNECTION_CHOICES = ["Star (Y)", "Delta (Δ)"]
CURRENT_QTY_CHOICES = ["Phase", "Line"]
CURRENT_MEAS_CHOICES = ["Peak", "RMS"]


def dc_link_to_vmax(vdc, scheme=DEFAULT_VOLTAGE_SCHEME):
    """Peak fundamental phase voltage available from a DC link for a scheme."""
    return float(vdc) * VOLTAGE_LIMIT_SCHEMES.get(scheme,
                                                  VOLTAGE_LIMIT_SCHEMES[DEFAULT_VOLTAGE_SCHEME])


def phase_peak_current(amps, connection="Star (Y)", quantity="Phase",
                       measure="Peak"):
    """Convert a user current spec to the peak *phase* current the d-q model needs.

    Star: line current == phase current. Delta: phase = line / sqrt(3).
    RMS values are scaled by sqrt(2) to the amplitude-invariant peak.
    """
    i = float(amps)
    if quantity == "Line" and connection.startswith("Delta"):
        i /= np.sqrt(3.0)
    if measure == "RMS":
        i *= np.sqrt(2.0)
    return i


def _dq_map_lookup(map_dict):
    """Build a clamped bilinear lookup f(id, iq) from {'id','iq','m'} arrays.

    Queries are clipped to the map's axis range (edge-hold, no extrapolation).
    If the map's id axis is all >= 0 it is treated as |id| so users can supply
    demagnetising current magnitudes without a sign convention.
    """
    id_ax = np.asarray(map_dict["id"], dtype=float)
    iq_ax = np.asarray(map_dict["iq"], dtype=float)
    mat = np.asarray(map_dict["m"], dtype=float)
    if id_ax.size < 2 or iq_ax.size < 2:
        raise ValueError("d-q parameter map needs at least a 2x2 grid.")
    use_abs_id = id_ax.min() >= 0.0
    rgi = RegularGridInterpolator((id_ax, iq_ax), mat, method="linear",
                                  bounds_error=False, fill_value=None)

    def lookup(id_a, iq_a):
        idv = np.asarray(id_a, dtype=float)
        iqv = np.asarray(iq_a, dtype=float)
        if use_abs_id:
            idv = np.abs(idv)
        idv = np.clip(idv, id_ax[0], id_ax[-1])
        iqv = np.clip(iqv, iq_ax[0], iq_ax[-1])
        idv, iqv = np.broadcast_arrays(idv, iqv)
        pts = np.column_stack([idv.ravel(), iqv.ravel()])
        return rgi(pts).reshape(idv.shape)

    return lookup


def _const_lookup(value):
    v = float(value)

    def lookup(id_a, iq_a):
        return np.full(np.broadcast(np.asarray(id_a), np.asarray(iq_a)).shape, v)

    return lookup


def solve_mtpa_mtpv(pole_pairs, ld_h, lq_h, psi_pm, i_max, v_max, rpm_max,
                    n_speed=240, n_ang=721,
                    ld_map=None, lq_map=None, psi_map=None, n_grid=201):
    """Sweep mechanical speed and return the full-load operating solution.

    Parameters (SI): ld_h/lq_h in henry, psi_pm in Wb, i_max in A (peak phase),
    v_max in V (peak phase, per the PWM scheme), rpm_max mechanical RPM.

    Optional saturation maps ld_map/lq_map/psi_map are dicts
    {'id': 1-D A, 'iq': 1-D A, 'm': 2-D values} in H / H / Wb. Any map that is
    None falls back to the matching constant. Supplying at least one map
    switches to the dense-grid solver; with none, the original constant-
    parameter path runs (identical numbers to before).

    Returns a dict of numpy arrays over the speed sweep plus summary scalars.
    """
    p = float(pole_pairs)
    Ld, Lq = float(ld_h), float(lq_h)
    psi = float(psi_pm)
    Imax, Vmax = float(i_max), float(v_max)
    if min(p, Ld, Lq, psi, Imax, Vmax, rpm_max) <= 0:
        raise ValueError("All MTPA/MTPV motor parameters must be positive.")
    kd = Ld - Lq
    mapped = any(m is not None for m in (ld_map, lq_map, psi_map))

    # ---- speed sweep (shared by both paths) ----
    rpm = np.linspace(rpm_max / n_speed, rpm_max, n_speed)
    w_m = rpm * 2.0 * np.pi / 60.0        # mech rad/s
    w_e = p * w_m                          # electrical rad/s
    vflux = Vmax / w_e                     # max allowed |flux| at each speed
    rows = np.arange(n_speed)
    phi = np.linspace(np.pi / 2.0, np.pi, n_ang)

    if mapped:
        ld_f = _dq_map_lookup(ld_map) if ld_map is not None else _const_lookup(Ld)
        lq_f = _dq_map_lookup(lq_map) if lq_map is not None else _const_lookup(Lq)
        psi_f = _dq_map_lookup(psi_map) if psi_map is not None else _const_lookup(psi)

        def flux_dq(id_a, iq_a):
            return (ld_f(id_a, iq_a) * id_a + psi_f(id_a, iq_a),
                    lq_f(id_a, iq_a) * iq_a)

        def torque_any(id_a, iq_a):
            pd_, pq_ = flux_dq(id_a, iq_a)
            return 1.5 * p * (pd_ * iq_a - pq_ * id_a)

        # Dense motoring-quadrant grid, restricted to the current circle,
        # plus the circle boundary itself at fine angular resolution (the
        # MTPA / FW optimum lies exactly on the boundary, which the interior
        # grid only approaches to within one grid step).
        id_g = np.linspace(-Imax, 0.0, n_grid)
        iq_g = np.linspace(0.0, Imax, n_grid)
        ID, IQ = np.meshgrid(id_g, iq_g, indexing="ij")
        inside = ID ** 2 + IQ ** 2 <= Imax ** 2 * (1.0 + 1e-9)
        idv = np.concatenate([ID[inside], Imax * np.cos(phi)])
        iqv = np.concatenate([IQ[inside], Imax * np.sin(phi)])
        pd_v, pq_v = flux_dq(idv, iqv)
        t_v = 1.5 * p * (pd_v * iqv - pq_v * idv)
        f_v = np.hypot(pd_v, pq_v)
        i_mag = np.hypot(idv, iqv)

        k_mtpa = int(np.argmax(t_v))       # unconstrained MTPA point at Imax
        t_mtpa_max = float(t_v[k_mtpa])
        flux_mtpa = float(f_v[k_mtpa])

        t_env = np.empty(n_speed)
        id_op = np.empty(n_speed)
        iq_op = np.empty(n_speed)
        on_circle = np.empty(n_speed, dtype=bool)
        for k in range(n_speed):
            feas = f_v <= vflux[k] + 1e-12
            if not np.any(feas):
                t_env[k] = -np.inf
                id_op[k] = iq_op[k] = np.nan
                on_circle[k] = False
                continue
            t_masked = np.where(feas, t_v, -np.inf)
            j = int(np.argmax(t_masked))
            t_env[k] = t_v[j]
            id_op[k] = idv[j]
            iq_op[k] = iqv[j]
            on_circle[k] = i_mag[j] >= Imax * 0.995
        mtpa_feasible = flux_mtpa <= vflux + 1e-12

        # ---- MTPA locus for the id-iq plane (0 .. Imax) ----
        mags = np.linspace(0.0, Imax, 80)
        id_m = mags[:, None] * np.cos(phi)[None, :]
        iq_m = mags[:, None] * np.sin(phi)[None, :]
        t_m = torque_any(id_m, iq_m)
        j = np.argmax(t_m, axis=1)
        mrows = np.arange(mags.size)
        locus_id = id_m[mrows, j]
        locus_iq = iq_m[mrows, j]

        # Characteristic current: fixed point of |id| = psi(id,0)/Ld(id,0).
        i_ch = psi / Ld
        for _ in range(6):
            i_ch = float(psi_f(-i_ch, 0.0) / np.maximum(ld_f(-i_ch, 0.0), 1e-12))
    else:
        def torque(id_a, iq_a):
            return 1.5 * p * (psi * iq_a + kd * id_a * iq_a)

        # ---- current-circle boundary (motoring quadrant: id<=0, iq>=0) ----
        id_c = Imax * np.cos(phi)
        iq_c = Imax * np.sin(phi)
        t_c = torque(id_c, iq_c)
        flux_c = np.hypot(Ld * id_c + psi, Lq * iq_c)

        i_mtpa = int(np.argmax(t_c))          # unconstrained MTPA point at Imax
        t_mtpa_max = float(t_c[i_mtpa])
        flux_mtpa = float(flux_c[i_mtpa])

        # Best point on the current circle that satisfies the voltage limit.
        feas_c = flux_c[None, :] <= vflux[:, None] + 1e-12
        t_grid_c = np.where(feas_c, t_c[None, :], -np.inf)
        idx_c = np.argmax(t_grid_c, axis=1)
        best_t_c = t_grid_c[rows, idx_c]

        # Best point on the voltage-ellipse boundary inside the current circle.
        theta = np.linspace(0.0, np.pi, n_ang)
        psi_d = vflux[:, None] * np.cos(theta)[None, :]
        psi_q = vflux[:, None] * np.sin(theta)[None, :]
        id_e = (psi_d - psi) / Ld
        iq_e = psi_q / Lq
        ok_e = (id_e <= 1e-9) & (iq_e >= 0.0) & (id_e ** 2 + iq_e ** 2 <= Imax ** 2 + 1e-9)
        t_grid_e = np.where(ok_e, torque(id_e, iq_e), -np.inf)
        idx_e = np.argmax(t_grid_e, axis=1)
        best_t_e = t_grid_e[rows, idx_e]

        use_ellipse = best_t_e > best_t_c
        t_env = np.where(use_ellipse, best_t_e, best_t_c)
        id_op = np.where(use_ellipse, id_e[rows, idx_e], id_c[idx_c])
        iq_op = np.where(use_ellipse, iq_e[rows, idx_e], iq_c[idx_c])
        mtpa_feasible = feas_c[:, i_mtpa]
        on_circle = np.hypot(id_op, iq_op) >= Imax * 0.995

        # ---- MTPA locus for the id-iq plane (0 .. Imax) ----
        mags = np.linspace(0.0, Imax, 80)
        id_m = mags[:, None] * np.cos(phi)[None, :]
        iq_m = mags[:, None] * np.sin(phi)[None, :]
        t_m = torque(id_m, iq_m)
        j = np.argmax(t_m, axis=1)
        mrows = np.arange(mags.size)
        locus_id = id_m[mrows, j]
        locus_iq = iq_m[mrows, j]

        i_ch = psi / Ld                       # characteristic current

    infeasible = ~np.isfinite(t_env)
    t_env = np.where(infeasible, 0.0, np.clip(t_env, 0.0, None))
    id_op = np.where(infeasible, np.nan, id_op)
    iq_op = np.where(infeasible, np.nan, iq_op)

    # Region classification is geometric, not "which sampling won": in FW the
    # optimum sits at the circle/ellipse intersection (|i| = Imax), so both
    # boundary searches find (nearly) the same point and comparing torques
    # would flip-flop. True MTPV is when the optimum detaches from the current
    # circle and moves inside it (|i| clearly below Imax).
    region = np.full(n_speed, REGION_FW, dtype=int)
    region[mtpa_feasible] = REGION_MTPA
    on_mtpv = (~on_circle) & (region != REGION_MTPA)
    region[on_mtpv] = REGION_MTPV
    region[infeasible] = REGION_INFEASIBLE

    power_kw = t_env * w_m / 1000.0

    # ---- summary scalars ----
    # Analytic base speed: the exact speed where the voltage limit reaches the
    # MTPA point, Vmax = w_e * |psi_s(MTPA)|. (The previous grid-sample report
    # quantised this down by up to rpm_max/n_speed.)
    if flux_mtpa > 0:
        base_rpm = float(Vmax / (p * flux_mtpa) * 60.0 / (2.0 * np.pi))
    else:
        base_rpm = None
    corner_kw = float(np.nanmax(power_kw)) if np.any(np.isfinite(power_kw)) else 0.0

    return {
        "rpm": rpm, "torque": t_env, "power_kw": power_kw,
        "id": id_op, "iq": iq_op, "region": region,
        "mtpa_locus_id": locus_id, "mtpa_locus_iq": locus_iq,
        "t_mtpa_max": t_mtpa_max, "base_rpm": base_rpm,
        "corner_kw": corner_kw, "i_ch": i_ch,
        "mtpv_reachable": bool(i_ch < Imax),
        "mapped": mapped,
        "params": {"p": p, "Ld": Ld, "Lq": Lq, "psi": psi,
                   "Imax": Imax, "Vmax": Vmax},
    }


class MtpaMtpvMixin:

    # ------------------------------------------------------------------ #
    #  Input section                                                     #
    # ------------------------------------------------------------------ #
    def _build_mtpa_mtpv_section(self, input_frame):
        self.sections['mtpa_mtpv'] = self.create_section(
            input_frame, "MTPA / MTPV Motor Model (d-q)", "#f1f5f9")
        frame = self.sections['mtpa_mtpv']

        ctk.CTkLabel(
            frame,
            text=("PMSM/IPM d-q model. Torque = 1.5·p·(ψ_PM·iq + (Ld−Lq)·id·iq)\n"
                  "Limits: current circle Imax, voltage ellipse Vmax/ω.\n"
                  "Stator resistance neglected (steady state)."),
            font=("Segoe UI", 10), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(6, 2))

        self.create_labeled_entry(frame, "Pole Pairs (p)", "4", "mtpa_pole_pairs")
        self.create_labeled_entry(frame, "Ld - d-axis inductance (mH)", "0.15", "mtpa_ld_mh")
        self.create_labeled_entry(frame, "Lq - q-axis inductance (mH)", "0.35", "mtpa_lq_mh")
        self.create_labeled_entry(frame, "PM Flux Linkage ψ_PM (Wb)", "0.015", "mtpa_psi_pm")

        # --- Current spec: value + how to interpret it (star/delta etc.) ---
        self.create_labeled_entry(frame, "Max Current (A)", "200", "mtpa_imax")

        row = self.create_control_row(frame, "Winding Connection")
        self.mtpa_conn_combo = ctk.CTkComboBox(
            row, values=CONNECTION_CHOICES, width=140,
            command=lambda _c: self.plot_graph())
        self.mtpa_conn_combo.set(CONNECTION_CHOICES[0])
        self.mtpa_conn_combo.pack(side="right")

        row = self.create_control_row(frame, "Current Given As")
        self.mtpa_current_qty_combo = ctk.CTkComboBox(
            row, values=CURRENT_QTY_CHOICES, width=140,
            command=lambda _c: self.plot_graph())
        self.mtpa_current_qty_combo.set(CURRENT_QTY_CHOICES[0])
        self.mtpa_current_qty_combo.pack(side="right")

        row = self.create_control_row(frame, "Current Value Is")
        self.mtpa_current_meas_combo = ctk.CTkComboBox(
            row, values=CURRENT_MEAS_CHOICES, width=140,
            command=lambda _c: self.plot_graph())
        self.mtpa_current_meas_combo.set(CURRENT_MEAS_CHOICES[0])
        self.mtpa_current_meas_combo.pack(side="right")

        self.create_labeled_entry(frame, "DC Link Voltage Vdc (V)", "72", "mtpa_vdc")

        row = self.create_control_row(frame, "Voltage Limit (PWM)")
        self.mtpa_vlimit_combo = ctk.CTkComboBox(
            row, values=list(VOLTAGE_LIMIT_SCHEMES.keys()), width=180,
            command=lambda _c: self.plot_graph())
        self.mtpa_vlimit_combo.set(DEFAULT_VOLTAGE_SCHEME)
        self.mtpa_vlimit_combo.pack(side="right")

        self.create_labeled_entry(frame, "Max Speed (RPM, mechanical)", "8000", "mtpa_max_rpm")

        # --- Optional saturation maps: Ld/Lq/psi over an (id, iq) grid ---
        ctk.CTkLabel(
            frame,
            text=("Saturation maps (optional). Excel grid: first column = id (A),\n"
                  "header row = iq (A). Ld/Lq cells in mH, ψ_PM cells in Wb.\n"
                  "Missing maps use the constant values above."),
            font=("Segoe UI", 10), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 2))

        self.mtpa_ld_map = None
        self.mtpa_lq_map = None
        self.mtpa_psi_map = None
        for which, label in (("ld", "Upload Ld Map (mH)"),
                             ("lq", "Upload Lq Map (mH)"),
                             ("psi", "Upload ψ_PM Map (Wb)")):
            map_row = ctk.CTkFrame(frame, fg_color="transparent")
            map_row.pack(fill="x", pady=(2, 2), padx=8)
            btn = ctk.CTkButton(
                map_row, text=label,
                command=lambda w=which: self.upload_mtpa_dq_map(w))
            btn.pack(side="left", padx=(0, 6), fill="x", expand=True)
            ind = ctk.CTkLabel(map_row, text="❌",
                               text_color=COLORS['warning'],
                               font=("Segoe UI", 18))
            ind.pack(side="left", padx=(0, 6))
            setattr(self, f"mtpa_{which}_indicator", ind)
            dele = ctk.CTkButton(
                map_row, text="Delete", fg_color=COLORS['warning'],
                text_color="white", width=60,
                command=lambda w=which: self.delete_mtpa_dq_map(w))
            dele.pack(side="left")
            dele.configure(state="disabled")
            setattr(self, f"mtpa_{which}_delete_button", dele)

        row = self.create_control_row(frame, "Select Plot")
        self.mtpa_plot_toggle = ctk.CTkComboBox(
            row,
            values=["All", "Torque-Speed", "Power-Speed",
                    "Current Trajectory (id-iq)", "id & iq vs Speed"],
            width=200,
            command=lambda _choice: self.plot_graph(),
        )
        self.mtpa_plot_toggle.set("All")
        self.mtpa_plot_toggle.pack(side="right")

        self.mtpa_results_label = ctk.CTkLabel(
            frame, text="Results appear here after plotting.",
            justify="left", font=("Segoe UI", 12),
            text_color=COLORS['primary'], anchor="w",
        )
        self.mtpa_results_label.pack(fill="x", padx=16, pady=(4, 8))

    # ------------------------------------------------------------------ #
    #  Saturation-map upload / delete                                    #
    # ------------------------------------------------------------------ #
    _MTPA_MAP_TITLES = {"ld": "Ld", "lq": "Lq", "psi": "ψ_PM"}

    def _read_dq_map_excel(self, file_path):
        """Read an (id x iq) parameter grid: first column = id axis (A),
        header row = iq axis (A), cells = parameter value. Same layout as the
        efficiency maps, but values are kept as-is (no % normalisation)."""
        df_map = pd.read_excel(file_path, index_col=0)
        df_map = df_map.dropna(how='all').dropna(axis=1, how='all')
        iq_raw = pd.to_numeric(pd.Index(df_map.columns), errors='coerce').to_numpy(dtype=float)
        id_raw = pd.to_numeric(pd.Index(df_map.index), errors='coerce').to_numpy(dtype=float)
        vals = df_map.apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)

        col_mask = np.isfinite(iq_raw)
        row_mask = np.isfinite(id_raw)
        iq_ax = iq_raw[col_mask]
        id_ax = id_raw[row_mask]
        mat = vals[np.ix_(row_mask, col_mask)]
        if id_ax.size < 2 or iq_ax.size < 2:
            raise ValueError("Map needs numeric id rows and iq column headers (>= 2x2).")

        id_order = np.argsort(id_ax)
        iq_order = np.argsort(iq_ax)
        id_ax = id_ax[id_order]
        iq_ax = iq_ax[iq_order]
        mat = mat[np.ix_(id_order, iq_order)]

        finite = np.isfinite(mat)
        if not np.any(finite):
            raise ValueError("Map contains no numeric values.")
        mat = np.where(finite, mat, float(np.nanmedian(mat[finite])))
        return {"id": id_ax, "iq": iq_ax, "m": mat}

    def upload_mtpa_dq_map(self, which):
        title = self._MTPA_MAP_TITLES.get(which, which)
        file_path = filedialog.askopenfilename(
            title=f"Select {title} Map Excel (id rows x iq columns)",
            filetypes=[("Excel Files", "*.xlsx;*.xls")])
        if not file_path:
            return
        try:
            map_dict = self._read_dq_map_excel(file_path)
        except Exception as exc:
            messagebox.showerror(f"{title} Map Error", str(exc))
            return
        setattr(self, f"mtpa_{which}_map", map_dict)
        if hasattr(self, "_set_indicator"):
            self._set_indicator(f"mtpa_{which}_indicator", True,
                                [f"mtpa_{which}_delete_button"])
        if hasattr(self, "update_data_checklist"):
            self.update_data_checklist()
        self.set_status(f"{title} map loaded "
                        f"({map_dict['m'].shape[0]}x{map_dict['m'].shape[1]} grid).", "ok")
        if getattr(self, "plot_mode", None) == "MTPA / MTPV (PMSM)":
            self.plot_graph()

    def delete_mtpa_dq_map(self, which):
        setattr(self, f"mtpa_{which}_map", None)
        if hasattr(self, "_set_indicator"):
            self._set_indicator(f"mtpa_{which}_indicator", False,
                                [f"mtpa_{which}_delete_button"])
        if hasattr(self, "update_data_checklist"):
            self.update_data_checklist()
        if getattr(self, "plot_mode", None) == "MTPA / MTPV (PMSM)":
            self.plot_graph()

    @staticmethod
    def _mtpa_map_to_si(map_dict, kind):
        """Uploaded Ld/Lq maps are in mH (auto-detects values already in H);
        psi maps are already in Wb."""
        if map_dict is None:
            return None
        out = {"id": np.asarray(map_dict["id"], dtype=float),
               "iq": np.asarray(map_dict["iq"], dtype=float),
               "m": np.asarray(map_dict["m"], dtype=float)}
        if kind in ("ld", "lq"):
            # < 0.01 "mH" (10 nH) is not a real machine; those values are H.
            if np.nanmax(np.abs(out["m"])) >= 0.01:
                out["m"] = out["m"] / 1000.0
        return out

    # ------------------------------------------------------------------ #
    #  Input reading                                                     #
    # ------------------------------------------------------------------ #
    def _read_mtpa_inputs(self):
        errors = []
        p = parse_float(self.mtpa_pole_pairs, "Pole Pairs", minimum=1, errors=errors)
        ld = parse_float(self.mtpa_ld_mh, "Ld", minimum=1e-6, errors=errors)
        lq = parse_float(self.mtpa_lq_mh, "Lq", minimum=1e-6, errors=errors)
        psi = parse_float(self.mtpa_psi_pm, "PM Flux Linkage", minimum=1e-9, errors=errors)
        imax = parse_float(self.mtpa_imax, "Max Current", minimum=1e-6, errors=errors)
        vdc = parse_float(self.mtpa_vdc, "DC Link Voltage", minimum=1e-6, errors=errors)
        rpm_max = parse_float(self.mtpa_max_rpm, "Max Speed", minimum=1, errors=errors)
        if errors:
            self.set_status("Fix inputs: " + "; ".join(errors), "error")
            return None

        conn = self.mtpa_conn_combo.get() if hasattr(self, "mtpa_conn_combo") else CONNECTION_CHOICES[0]
        qty = self.mtpa_current_qty_combo.get() if hasattr(self, "mtpa_current_qty_combo") else "Phase"
        meas = self.mtpa_current_meas_combo.get() if hasattr(self, "mtpa_current_meas_combo") else "Peak"
        scheme = self.mtpa_vlimit_combo.get() if hasattr(self, "mtpa_vlimit_combo") else DEFAULT_VOLTAGE_SCHEME
        return {
            "pole_pairs": p,
            "ld_h": ld / 1000.0,          # mH -> H
            "lq_h": lq / 1000.0,
            "psi_pm": psi,
            "i_max": phase_peak_current(imax, conn, qty, meas),
            "v_max": dc_link_to_vmax(vdc, scheme),
            "rpm_max": rpm_max,
            "ld_map": self._mtpa_map_to_si(getattr(self, "mtpa_ld_map", None), "ld"),
            "lq_map": self._mtpa_map_to_si(getattr(self, "mtpa_lq_map", None), "lq"),
            "psi_map": self._mtpa_map_to_si(getattr(self, "mtpa_psi_map", None), "psi"),
        }

    # ------------------------------------------------------------------ #
    #  Plotting                                                          #
    # ------------------------------------------------------------------ #
    def plot_mtpa_mtpv(self):
        inputs = self._read_mtpa_inputs()
        if inputs is None:
            self.show_placeholder_message("Invalid inputs — fields marked in red")
            return
        try:
            sol = solve_mtpa_mtpv(**inputs)
        except Exception as exc:
            self.set_status(f"MTPA/MTPV solve failed: {exc}", "error")
            self.show_placeholder_message(str(exc))
            return

        view = self.mtpa_plot_toggle.get() if hasattr(self, "mtpa_plot_toggle") else "All"
        self.figure.clf()
        self.figure.patch.set_facecolor(COLORS["plot_bg"])

        if view == "All":
            axes = self.figure.subplots(2, 2)
            self._mtpa_panel_torque_speed(axes[0, 0], sol)
            self._mtpa_panel_power_speed(axes[0, 1], sol)
            self._mtpa_panel_idiq_plane(axes[1, 0], sol)
            self._mtpa_panel_currents_vs_speed(axes[1, 1], sol)
            for ax in axes.ravel():
                self._mtpa_apply_gs(ax)
            self.figure.tight_layout()
            self.ax = axes[0, 0]
        else:
            self.ax = self.figure.add_subplot(111)
            if view == "Torque-Speed":
                self._mtpa_panel_torque_speed(self.ax, sol)
            elif view == "Power-Speed":
                self._mtpa_panel_power_speed(self.ax, sol)
            elif view == "Current Trajectory (id-iq)":
                self._mtpa_panel_idiq_plane(self.ax, sol)
            else:
                self._mtpa_panel_currents_vs_speed(self.ax, sol)
            self._mtpa_apply_gs(self.ax)
            self.figure.tight_layout()

        self._mtpa_update_results(sol)
        self.canvas.draw()
        self.set_status("MTPA/MTPV: done.", "ok")

    def _mtpa_apply_gs(self, ax):
        """Graph Settings (grid / legend / font sizes) for one MTPA panel.

        apply_graph_style() only touches self.ax; the "All" view has four
        axes, so the MTPA plots restyle each panel themselves. Defaults
        reproduce the original hard-coded look."""
        try:
            grid_ls = self.gs_linestyle("grid_style", "--")
            grid_alpha = self.gs_float("grid_alpha", 0.5)
            ax.grid(False)
            if self.gs_bool("grid_x", True):
                ax.grid(True, axis="x", linestyle=grid_ls, alpha=grid_alpha)
            if self.gs_bool("grid_y", True):
                ax.grid(True, axis="y", linestyle=grid_ls, alpha=grid_alpha)
            for step, axis in ((self.gs_float("grid_x_step", 0.0), ax.xaxis),
                               (self.gs_float("grid_y_step", 0.0), ax.yaxis)):
                if step and step > 0:
                    lo, hi = (ax.get_xlim() if axis is ax.xaxis else ax.get_ylim())
                    if 0 < (hi - lo) / step <= 1000:
                        axis.set_major_locator(MultipleLocator(step))
        except Exception:
            pass
        try:
            if ax.get_title():
                ax.title.set_fontsize(self.gs_int("title_size", 13))
            ax.xaxis.label.set_size(self.gs_int("label_size", 11))
            ax.yaxis.label.set_size(self.gs_int("label_size", 11))
        except Exception:
            pass
        try:
            leg = ax.get_legend()
            if not self.gs_bool("show_legend", True):
                if leg is not None:
                    leg.remove()
            elif leg is not None:
                loc = self.gs_str("legend_loc", "Auto")
                if loc and loc != "Auto":
                    handles = getattr(leg, "legend_handles", None) or leg.legendHandles
                    labels = [t.get_text() for t in leg.get_texts()]
                    fs = leg.get_texts()[0].get_fontsize() if leg.get_texts() else 8
                    ax.legend(handles=handles, labels=labels, loc=loc, fontsize=fs)
        except Exception:
            pass

    def _mtpa_shade_regions(self, ax, sol):
        """Color the background by operating region (MTPA / FW / MTPV)."""
        if self.gs_bool("region_shade", True):
            alpha = self.gs_float("region_alpha", 0.10)
            rpm, region = sol["rpm"], sol["region"]
            start = 0
            for i in range(1, len(rpm) + 1):
                if i == len(rpm) or region[i] != region[start]:
                    name, color = _REGION_META[int(region[start])]
                    ax.axvspan(rpm[start], rpm[i - 1], color=color, alpha=alpha)
                    start = i
        if sol["base_rpm"] is not None:
            ax.axvline(sol["base_rpm"], color="black", linestyle=":", linewidth=1.2)

    def _mtpa_region_legend_handles(self, sol):
        import matplotlib.patches as mpatches
        present = sorted(set(int(r) for r in sol["region"]))
        return [mpatches.Patch(color=_REGION_META[r][1], alpha=0.35,
                               label=_REGION_META[r][0]) for r in present]

    def _mtpa_panel_torque_speed(self, ax, sol):
        ax.plot(sol["rpm"], sol["torque"],
                color=self.gs_color("env_color", "black"),
                linestyle=self.gs_linestyle("env_style", "-"),
                linewidth=self.gs_float("env_width", 2.2),
                label="Max torque envelope")
        self._mtpa_shade_regions(ax, sol)
        if sol["base_rpm"] is not None:
            ax.annotate(f"base {sol['base_rpm']:.0f} RPM",
                        xy=(sol["base_rpm"], float(np.max(sol["torque"])) * 0.55),
                        fontsize=9, rotation=90, va="center", ha="right")
        ax.set_xlabel("Speed (RPM, mech)")
        ax.set_ylabel("Torque (Nm)")
        ax.set_title("Torque-Speed Envelope", fontsize=13, weight="bold")
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(handles=([h for h in ax.get_legend_handles_labels()[0]]
                           + self._mtpa_region_legend_handles(sol)),
                  fontsize=8, loc="upper right")

    def _mtpa_panel_power_speed(self, ax, sol):
        ax.plot(sol["rpm"], sol["power_kw"],
                color=self.gs_color("power_color", COLORS["primary"]),
                linestyle=self.gs_linestyle("power_style", "-"),
                linewidth=self.gs_float("power_width", 2.2))
        self._mtpa_shade_regions(ax, sol)
        ax.set_xlabel("Speed (RPM, mech)")
        ax.set_ylabel("Mechanical Power (kW)")
        ax.set_title("Power-Speed", fontsize=13, weight="bold")
        ax.grid(True, linestyle="--", alpha=0.5)

    def _mtpa_panel_idiq_plane(self, ax, sol):
        prm = sol["params"]
        Imax, Ld, Lq, psi, p = (prm["Imax"], prm["Ld"], prm["Lq"],
                                prm["psi"], prm["p"])
        # Current-limit circle (motoring quadrant).
        ang = np.linspace(np.pi / 2, np.pi, 200)
        ax.plot(Imax * np.cos(ang), Imax * np.sin(ang), "--", color="gray",
                linewidth=1.4, label=f"Current limit {Imax:.0f} A")
        # MTPA locus.
        ax.plot(sol["mtpa_locus_id"], sol["mtpa_locus_iq"], color="#2563eb",
                linewidth=2.2, label="MTPA locus")
        # Voltage ellipses at sample speeds (constant-parameter shapes; with
        # saturation maps loaded these are the unsaturated approximation).
        samples = [r for r in (sol["base_rpm"],
                               (sol["base_rpm"] or 0) * 2,
                               sol["rpm"][-1]) if r]
        theta = np.linspace(0, np.pi, 200)
        for r in dict.fromkeys(samples):          # unique, keep order
            w_e = p * r * 2 * np.pi / 60.0
            vf = prm["Vmax"] / w_e
            ide = (vf * np.cos(theta) - psi) / Ld
            iqe = vf * np.sin(theta) / Lq
            keep = (ide >= -Imax * 1.4) & (iqe <= Imax * 1.4)
            ax.plot(ide[keep], iqe[keep], ":", linewidth=1.2,
                    label=f"V-ellipse @ {r:.0f} RPM")
        # Full-load trajectory over speed, colored by region.
        msize = self.gs_int("traj_size", 4)
        for code, (name, color) in _REGION_META.items():
            m = sol["region"] == code
            if np.any(m):
                ax.plot(sol["id"][m], sol["iq"][m], ".", color=color,
                        markersize=msize, label=f"Trajectory: {name}")
        # Characteristic current.
        ax.axvline(-sol["i_ch"], color="purple", linestyle="-.", linewidth=1.0,
                   label=f"-I_ch = -ψ/Ld = {-sol['i_ch']:.0f} A")
        ax.set_xlabel("id (A)")
        ax.set_ylabel("iq (A)")
        ax.set_title("Current Plane (id-iq)", fontsize=13, weight="bold")
        ax.set_xlim(-Imax * 1.25, Imax * 0.15)
        ax.set_ylim(0, Imax * 1.15)
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(fontsize=7, loc="upper left")

    def _mtpa_panel_currents_vs_speed(self, ax, sol):
        ax.plot(sol["rpm"], sol["id"],
                color=self.gs_color("id_color", "#dc2626"),
                linestyle=self.gs_linestyle("id_style", "-"),
                linewidth=self.gs_float("id_width", 2.0), label="id (A)")
        ax.plot(sol["rpm"], sol["iq"],
                color=self.gs_color("iq_color", "#2563eb"),
                linestyle=self.gs_linestyle("iq_style", "-"),
                linewidth=self.gs_float("iq_width", 2.0), label="iq (A)")
        i_s = np.hypot(sol["id"], sol["iq"])
        ax.plot(sol["rpm"], i_s,
                color=self.gs_color("is_color", "gray"),
                linestyle=self.gs_linestyle("is_style", "--"),
                linewidth=self.gs_float("is_width", 1.5),
                label="|i_s| (A)")
        self._mtpa_shade_regions(ax, sol)
        ax.set_xlabel("Speed (RPM, mech)")
        ax.set_ylabel("Current (A)")
        ax.set_title("Full-Load id / iq vs Speed", fontsize=13, weight="bold")
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(fontsize=8, loc="best")

    def _mtpa_update_results(self, sol):
        prm = sol["params"]
        t_at_max = float(sol["torque"][-1])
        maps_on = [self._MTPA_MAP_TITLES[w] for w in ("ld", "lq", "psi")
                   if getattr(self, f"mtpa_{w}_map", None) is not None]
        parts = [
            f"Peak torque (MTPA): {sol['t_mtpa_max']:.2f} Nm",
            (f"Base speed: {sol['base_rpm']:.0f} RPM"
             if sol["base_rpm"] is not None else
             "Base speed: not reached in sweep (voltage-limited from start)"),
            f"Corner / max power: {sol['corner_kw']:.2f} kW",
            f"Characteristic current I_ch = ψ/Ld: {sol['i_ch']:.1f} A "
            f"({'<' if sol['mtpv_reachable'] else '>='} Imax -> MTPV "
            f"{'reachable' if sol['mtpv_reachable'] else 'not reachable'})",
            f"Torque at max speed: {t_at_max:.2f} Nm",
            f"Solver limits: Imax {prm['Imax']:.1f} A (peak phase), "
            f"Vmax {prm['Vmax']:.1f} V (peak phase)",
            ("Saturation maps: " + ", ".join(maps_on)) if maps_on
            else "Saturation maps: none (constant Ld/Lq/ψ)",
        ]
        try:
            self.mtpa_results_label.configure(text="\n".join(parts))
        except Exception:
            pass
