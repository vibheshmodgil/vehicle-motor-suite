"""MTPA / MTPV analysis for PM synchronous machines (d-q model).

Implements the strategies described in knowledge_base/scenarios/MTPA_MTPV.pdf:

  Flux linkages:  psi_d = Ld*id + psi_PM,   psi_q = Lq*iq
  Torque:         Te = 1.5*p*(psi_PM*iq + (Ld - Lq)*id*iq)
  Current limit:  id^2 + iq^2 <= Imax^2                    (circle)
  Voltage limit:  (Ld*id + psi_PM)^2 + (Lq*iq)^2 <= (Vmax/w_e)^2   (ellipse)
                  with Vmax = Vdc/sqrt(3) (SVPWM) and w_e = p * w_mech.
                  Stator resistance is neglected (steady state), as in the PDF.

For each speed the solver finds the maximum-torque current vector (id<=0,
iq>=0 motoring quadrant) subject to both limits, by sampling the two active
boundaries (current circle and voltage ellipse) and taking the best feasible
point. The operating regions follow the PDF's classification:

  MTPA (constant torque)  - the unconstrained MTPA point is voltage-feasible
  FW   (constant power)   - optimum still on the current circle, slid along it
  MTPV (deep flux-weaken) - optimum on the shrinking voltage ellipse

`solve_mtpa_mtpv()` is a pure function (testable without the GUI);
`MtpaMtpvMixin` provides the input section and the plots.
"""

import numpy as np
import customtkinter as ctk

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


def solve_mtpa_mtpv(pole_pairs, ld_h, lq_h, psi_pm, i_max, v_max, rpm_max,
                    n_speed=240, n_ang=721):
    """Sweep mechanical speed and return the full-load operating solution.

    Parameters (SI): ld_h/lq_h in henry, psi_pm in Wb, i_max in A (peak),
    v_max in V (peak phase, already Vdc/sqrt(3)), rpm_max mechanical RPM.

    Returns a dict of numpy arrays over the speed sweep plus summary scalars.
    """
    p = float(pole_pairs)
    Ld, Lq = float(ld_h), float(lq_h)
    psi = float(psi_pm)
    Imax, Vmax = float(i_max), float(v_max)
    if min(p, Ld, Lq, psi, Imax, Vmax, rpm_max) <= 0:
        raise ValueError("All MTPA/MTPV motor parameters must be positive.")
    kd = Ld - Lq

    def torque(id_a, iq_a):
        return 1.5 * p * (psi * iq_a + kd * id_a * iq_a)

    # ---- current-circle boundary (motoring quadrant: id<=0, iq>=0) ----
    phi = np.linspace(np.pi / 2.0, np.pi, n_ang)
    id_c = Imax * np.cos(phi)
    iq_c = Imax * np.sin(phi)
    t_c = torque(id_c, iq_c)
    flux_c = np.hypot(Ld * id_c + psi, Lq * iq_c)

    i_mtpa = int(np.argmax(t_c))          # unconstrained MTPA point at Imax
    t_mtpa_max = float(t_c[i_mtpa])

    # ---- speed sweep ----
    rpm = np.linspace(rpm_max / n_speed, rpm_max, n_speed)
    w_m = rpm * 2.0 * np.pi / 60.0        # mech rad/s
    w_e = p * w_m                          # electrical rad/s
    vflux = Vmax / w_e                     # max allowed |flux| at each speed

    # Best point on the current circle that satisfies the voltage limit.
    feas_c = flux_c[None, :] <= vflux[:, None] + 1e-12
    t_grid_c = np.where(feas_c, t_c[None, :], -np.inf)
    idx_c = np.argmax(t_grid_c, axis=1)
    rows = np.arange(n_speed)
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

    infeasible = ~np.isfinite(t_env)
    t_env = np.where(infeasible, 0.0, np.clip(t_env, 0.0, None))
    id_op = np.where(infeasible, np.nan, id_op)
    iq_op = np.where(infeasible, np.nan, iq_op)

    # Region classification is geometric, not "which sampling won": in FW the
    # optimum sits at the circle/ellipse intersection (|i| = Imax), so both
    # boundary searches find (nearly) the same point and comparing torques
    # would flip-flop. True MTPV is when the optimum detaches from the current
    # circle and moves inside it (|i| clearly below Imax).
    i_mag_op = np.hypot(id_op, iq_op)
    region = np.full(n_speed, REGION_FW, dtype=int)
    region[feas_c[:, i_mtpa]] = REGION_MTPA
    on_mtpv = (i_mag_op < Imax * 0.995) & (region != REGION_MTPA)
    region[on_mtpv] = REGION_MTPV
    region[infeasible] = REGION_INFEASIBLE

    power_kw = t_env * w_m / 1000.0

    # ---- MTPA locus for the id-iq plane (0 .. Imax) ----
    mags = np.linspace(0.0, Imax, 80)
    id_m = mags[:, None] * np.cos(phi)[None, :]
    iq_m = mags[:, None] * np.sin(phi)[None, :]
    t_m = torque(id_m, iq_m)
    j = np.argmax(t_m, axis=1)
    mrows = np.arange(mags.size)
    locus_id = id_m[mrows, j]
    locus_iq = iq_m[mrows, j]

    # ---- summary scalars ----
    mtpa_mask = region == REGION_MTPA
    base_rpm = float(rpm[mtpa_mask][-1]) if np.any(mtpa_mask) else None
    corner_kw = float(np.nanmax(power_kw)) if np.any(np.isfinite(power_kw)) else 0.0
    i_ch = psi / Ld                       # characteristic current

    return {
        "rpm": rpm, "torque": t_env, "power_kw": power_kw,
        "id": id_op, "iq": iq_op, "region": region,
        "mtpa_locus_id": locus_id, "mtpa_locus_iq": locus_iq,
        "t_mtpa_max": t_mtpa_max, "base_rpm": base_rpm,
        "corner_kw": corner_kw, "i_ch": i_ch,
        "mtpv_reachable": bool(i_ch < Imax),
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
                  "Limits: current circle Imax, voltage ellipse Vmax/ω (Vmax = Vdc/√3).\n"
                  "Stator resistance neglected (steady state)."),
            font=("Segoe UI", 10), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(6, 2))

        self.create_labeled_entry(frame, "Pole Pairs (p)", "4", "mtpa_pole_pairs")
        self.create_labeled_entry(frame, "Ld - d-axis inductance (mH)", "0.15", "mtpa_ld_mh")
        self.create_labeled_entry(frame, "Lq - q-axis inductance (mH)", "0.35", "mtpa_lq_mh")
        self.create_labeled_entry(frame, "PM Flux Linkage ψ_PM (Wb)", "0.015", "mtpa_psi_pm")
        self.create_labeled_entry(frame, "Max Phase Current Imax (A, peak)", "200", "mtpa_imax")
        self.create_labeled_entry(frame, "DC Link Voltage Vdc (V)", "72", "mtpa_vdc")
        self.create_labeled_entry(frame, "Max Speed (RPM, mechanical)", "8000", "mtpa_max_rpm")

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
    #  Input reading                                                     #
    # ------------------------------------------------------------------ #
    def _read_mtpa_inputs(self):
        errors = []
        p = parse_float(self.mtpa_pole_pairs, "Pole Pairs", minimum=1, errors=errors)
        ld = parse_float(self.mtpa_ld_mh, "Ld", minimum=1e-6, errors=errors)
        lq = parse_float(self.mtpa_lq_mh, "Lq", minimum=1e-6, errors=errors)
        psi = parse_float(self.mtpa_psi_pm, "PM Flux Linkage", minimum=1e-9, errors=errors)
        imax = parse_float(self.mtpa_imax, "Max Phase Current", minimum=1e-6, errors=errors)
        vdc = parse_float(self.mtpa_vdc, "DC Link Voltage", minimum=1e-6, errors=errors)
        rpm_max = parse_float(self.mtpa_max_rpm, "Max Speed", minimum=1, errors=errors)
        if errors:
            self.set_status("Fix inputs: " + "; ".join(errors), "error")
            return None
        return {
            "pole_pairs": p,
            "ld_h": ld / 1000.0,          # mH -> H
            "lq_h": lq / 1000.0,
            "psi_pm": psi,
            "i_max": imax,
            "v_max": vdc / np.sqrt(3.0),  # SVPWM peak phase voltage
            "rpm_max": rpm_max,
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
            self.figure.tight_layout()

        self._mtpa_update_results(sol)
        self.canvas.draw()
        self.set_status("MTPA/MTPV: done.", "ok")

    def _mtpa_shade_regions(self, ax, sol):
        """Color the background by operating region (MTPA / FW / MTPV)."""
        rpm, region = sol["rpm"], sol["region"]
        start = 0
        for i in range(1, len(rpm) + 1):
            if i == len(rpm) or region[i] != region[start]:
                name, color = _REGION_META[int(region[start])]
                ax.axvspan(rpm[start], rpm[i - 1], color=color, alpha=0.10)
                start = i
        if sol["base_rpm"] is not None:
            ax.axvline(sol["base_rpm"], color="black", linestyle=":", linewidth=1.2)

    def _mtpa_region_legend_handles(self, sol):
        import matplotlib.patches as mpatches
        present = sorted(set(int(r) for r in sol["region"]))
        return [mpatches.Patch(color=_REGION_META[r][1], alpha=0.35,
                               label=_REGION_META[r][0]) for r in present]

    def _mtpa_panel_torque_speed(self, ax, sol):
        ax.plot(sol["rpm"], sol["torque"], color="black", linewidth=2.2,
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
        ax.plot(sol["rpm"], sol["power_kw"], color=COLORS["primary"], linewidth=2.2)
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
        # Voltage ellipses at sample speeds.
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
        for code, (name, color) in _REGION_META.items():
            m = sol["region"] == code
            if np.any(m):
                ax.plot(sol["id"][m], sol["iq"][m], ".", color=color,
                        markersize=4, label=f"Trajectory: {name}")
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
        ax.plot(sol["rpm"], sol["id"], color="#dc2626", linewidth=2.0, label="id (A)")
        ax.plot(sol["rpm"], sol["iq"], color="#2563eb", linewidth=2.0, label="iq (A)")
        i_s = np.hypot(sol["id"], sol["iq"])
        ax.plot(sol["rpm"], i_s, color="gray", linestyle="--", linewidth=1.5,
                label="|i_s| (A)")
        self._mtpa_shade_regions(ax, sol)
        ax.set_xlabel("Speed (RPM, mech)")
        ax.set_ylabel("Current (A)")
        ax.set_title("Full-Load id / iq vs Speed", fontsize=13, weight="bold")
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(fontsize=8, loc="best")

    def _mtpa_update_results(self, sol):
        t_at_max = float(sol["torque"][-1])
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
        ]
        try:
            self.mtpa_results_label.configure(text="\n".join(parts))
        except Exception:
            pass
