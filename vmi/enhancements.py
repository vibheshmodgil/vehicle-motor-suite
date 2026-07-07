"""
EnhancementsMixin -- all the *new* cross-cutting UI features, kept in one place
so the original/auto-generated mixins stay clean:

  * a top action toolbar + a bottom status bar
  * export current figure (PNG / SVG / PDF) and current data (CSV / XLSX)
  * a one-click standalone HTML report (figure embedded + summary + metrics)
  * save / load all inputs as a JSON "scenario"
  * a Light/Dark plot-theme toggle (post-processes the figure, so it works for
    every plot type without touching the individual plot methods)
  * a cursor read-out wired into the status bar
  * Enter-to-plot and a busy-cursor "safe plot" wrapper that surfaces errors in
    the status bar instead of only raising
  * a loss-breakdown waterfall view for the Range analysis

None of this changes any calculation or any existing input field.
"""

import base64
import io
import json
import os
import webbrowser
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import customtkinter as ctk

from . import theme
from .theme import COLORS
from .formatting import fmt, fmt_wh, fmt_km, fmt_pct


class EnhancementsMixin:

    # ------------------------------------------------------------------ #
    #  Toolbar + status bar                                              #
    # ------------------------------------------------------------------ #
    def build_toolbar(self):
        """A slim action bar of quick buttons under the header."""
        bar = ctk.CTkFrame(self, fg_color=COLORS["header_bg_soft"], corner_radius=0,
                           height=46, border_width=0)
        bar.pack(side="top", fill="x")

        def btn(text, cmd):
            b = ctk.CTkButton(bar, text=text, command=cmd, width=10, height=30,
                              corner_radius=8, font=(COLORS_FONT, 12, "bold"))
            b.pack(side="left", padx=(8, 0), pady=8)
            return b

        btn("Plot  \u23ce", self._safe_plot)
        btn("Export figure", self.export_current_figure)
        btn("Export data", self.export_current_data)
        btn("Report", self.generate_report)
        ctk.CTkLabel(bar, text="\u2502", text_color=COLORS["border"]).pack(side="left", padx=4)
        btn("Save scenario", self.save_scenario)
        btn("Load scenario", self.load_scenario)
        ctk.CTkLabel(bar, text="│", text_color=COLORS["border"]).pack(side="left", padx=4)
        btn("\U0001F4AC Assistant", self.toggle_assistant_panel)

        # Theme toggle pinned to the right.
        self.theme_switch = ctk.CTkSwitch(
            bar, text="Dark plots", command=self.toggle_theme,
            progress_color=COLORS["primary"], font=(COLORS_FONT, 12),
        )
        self.theme_switch.pack(side="right", padx=12, pady=8)

    def build_status_bar(self):
        """A bottom status strip used for messages, errors and the cursor
        read-out."""
        self.status_bar = ctk.CTkFrame(self, fg_color=COLORS["card"], corner_radius=0,
                                       height=28, border_width=1,
                                       border_color=COLORS["border"])
        self.status_bar.pack(side="bottom", fill="x")
        self.status_label = ctk.CTkLabel(
            self.status_bar, text="Ready.", anchor="w",
            font=(COLORS_FONT, 12), text_color=COLORS["text_muted"],
        )
        self.status_label.pack(side="left", fill="x", expand=True, padx=12)
        self.cursor_label = ctk.CTkLabel(
            self.status_bar, text="", anchor="e",
            font=(COLORS_FONT, 11), text_color=COLORS["text_muted"],
        )
        self.cursor_label.pack(side="right", padx=12)

    def set_status(self, message, kind="info"):
        colors = {"info": COLORS["text_muted"], "ok": COLORS["success"],
                  "warn": COLORS["warning"], "error": COLORS["danger"]}
        if hasattr(self, "status_label"):
            try:
                self.status_label.configure(text=message,
                                            text_color=colors.get(kind, COLORS["text_muted"]))
                self.status_label.update_idletasks()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Wiring done once, after the canvas exists                         #
    # ------------------------------------------------------------------ #
    def enh_setup(self):
        self._dark_mode = False
        self._debug = getattr(self, "_debug", False)

        # Wrap canvas.draw / draw_idle so dark mode applies to *every* plot,
        # and so the figure layout is re-fitted on every draw. The layout
        # refit is what keeps axis labels / tick numbers visible when the
        # window is resized or the side panels are dragged wider: without it
        # a shrinking canvas clips the outermost labels behind the panels.
        if hasattr(self, "canvas") and not getattr(self, "_draw_wrapped", False):
            for name in ("draw", "draw_idle"):
                orig = getattr(self.canvas, name, None)
                if orig is None:
                    continue

                def make(orig_fn):
                    def wrapped(*a, **k):
                        try:
                            import warnings
                            with warnings.catch_warnings():
                                # tight_layout warns (but still lays out the
                                # compatible axes) when a figure contains
                                # manually placed axes; that's fine here.
                                warnings.simplefilter("ignore")
                                self.figure.tight_layout()
                        except Exception:
                            pass
                        if getattr(self, "_dark_mode", False):
                            try:
                                theme.apply_dark_to_figure(self.figure)
                            except Exception:
                                pass
                        return orig_fn(*a, **k)
                    return wrapped
                setattr(self.canvas, name, make(orig))
            self._draw_wrapped = True
            # Re-fit the layout whenever the canvas itself changes size (the
            # user resizes the window or drags a paned divider), then redraw.
            try:
                self.canvas.mpl_connect(
                    "resize_event", lambda _e: self.canvas.draw_idle())
            except Exception:
                pass

            # Cursor read-out.
            try:
                self.canvas.mpl_connect("motion_notify_event", self._on_plot_hover)
            except Exception:
                pass

        # Enter anywhere re-runs the current plot.
        try:
            self.bind("<Return>", lambda e: self._safe_plot())
            self.bind("<KP_Enter>", lambda e: self._safe_plot())
        except Exception:
            pass

    def _on_plot_hover(self, event):
        if event.inaxes and event.xdata is not None and event.ydata is not None:
            self.cursor_label.configure(text=f"x={event.xdata:.2f}   y={event.ydata:.2f}")
        else:
            self.cursor_label.configure(text="")

    # ------------------------------------------------------------------ #
    #  Safe plot (busy cursor + error surfacing)                         #
    # ------------------------------------------------------------------ #
    def _mark_core_fields(self):
        """Red-border any malformed core numeric inputs (visual feedback only;
        does not block plotting). Returns a list of problem messages."""
        from .validation import parse_float
        errors = []
        checks = [
            ("m_ref", "Reference mass", {"minimum": 0}),
            ("wheel_radius", "Wheel radius", {"minimum": 0}),
            ("gear_ratio", "Gear ratio", {}),
            ("rear_load_ratio", "Rear load ratio", {}),
            ("ambient_temp", "Ambient temp", {}),
            ("ambient_pressure", "Ambient pressure", {}),
        ]
        for attr, label, kw in checks:
            w = getattr(self, attr, None)
            if w is not None:
                parse_float(w, label, errors=errors, **kw)
        # crr / cd_a are optional (blank allowed).
        for attr, label in (("crr", "Crr"), ("cd_a", "CdA")):
            w = getattr(self, attr, None)
            if w is not None:
                parse_float(w, label, allow_blank=True, errors=errors)
        return errors

    def _safe_plot(self):
        """Re-run the current analysis with a busy cursor, catching errors and
        reporting them in the status bar rather than crashing the UI thread."""
        mode = getattr(self, "plot_mode", None) or self.plot_type.get()
        try:
            problems = self._mark_core_fields()
        except Exception:
            problems = []
        if problems:
            self.set_status("Check inputs: " + "; ".join(problems[:3]), "warn")
        else:
            self.set_status(f"Computing: {mode} \u2026", "info")
        try:
            self.configure(cursor="watch")
            self.update_idletasks()
        except Exception:
            pass
        try:
            if self.plot_type.get() == "Compare Standard Motor Data":
                self.update_compare_std_plot()
            else:
                self.plot_graph()
            if not problems:
                self.set_status(f"{mode}: done.", "ok")
        except Exception as exc:
            self.set_status(f"{mode} failed: {exc}", "error")
            messagebox.showerror("Plot Error", str(exc))
        finally:
            try:
                self.configure(cursor="")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Theme toggle                                                      #
    # ------------------------------------------------------------------ #
    def toggle_theme(self):
        self._dark_mode = bool(self.theme_switch.get()) if hasattr(self, "theme_switch") else (not getattr(self, "_dark_mode", False))
        # NOTE: this toggle styles the *plot only* (post-processing the figure),
        # it deliberately does NOT call ctk.set_appearance_mode(): switching the
        # whole-app appearance left tk frames/canvas stranded as black patches
        # when toggling back to light.
        try:
            if self._dark_mode:
                theme.apply_dark_to_figure(self.figure)
            else:
                theme.apply_matplotlib_theme()      # restore light rc defaults
                theme.apply_light_to_figure(self.figure)  # repaint existing figure
        except Exception:
            pass
        # Re-render the current view so the change is visible immediately.
        try:
            self._safe_plot()
        except Exception:
            try:
                self.canvas.draw()
            except Exception:
                pass
        self.set_status(f"Plot theme: {'dark' if self._dark_mode else 'light'}.", "info")

    # ------------------------------------------------------------------ #
    #  Exports                                                           #
    # ------------------------------------------------------------------ #
    def export_current_figure(self):
        path = filedialog.asksaveasfilename(
            title="Export figure",
            defaultextension=".png",
            initialfile="vmi_figure.png",
            filetypes=[("PNG image", "*.png"), ("SVG vector", "*.svg"),
                       ("PDF document", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.figure.savefig(path, dpi=200, bbox_inches="tight",
                                facecolor=self.figure.get_facecolor())
            self.set_status(f"Figure exported: {os.path.basename(path)}", "ok")
        except Exception as exc:
            self.set_status(f"Figure export failed: {exc}", "error")
            messagebox.showerror("Export error", str(exc))

    def _current_dataframe(self):
        """Pick the most relevant table for the current analysis."""
        mode = getattr(self, "plot_mode", "")
        # Range analysis -> a one-row metrics table.
        if mode == "Range analysis" and getattr(self, "_last_range_metrics", None):
            import pandas as pd
            return pd.DataFrame([self._last_range_metrics]), "range_metrics"
        for attr, label in (("torque_speed_df", "torque_speed"),
                            ("dataframe", "drive_cycle")):
            d = getattr(self, attr, None)
            if d is not None and hasattr(d, "to_csv"):
                return d, label
        return None, None

    def export_current_data(self):
        d, label = self._current_dataframe()
        if d is None:
            self.set_status("No tabular data to export for this view.", "warn")
            messagebox.showwarning("No data",
                                   "There is no table associated with the current view. "
                                   "Plot a torque-speed or drive-cycle analysis first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export data",
            defaultextension=".csv",
            initialfile=f"vmi_{label}.csv",
            filetypes=[("CSV", "*.csv"), ("Excel", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".xlsx"):
                d.to_excel(path, index=False)
            else:
                d.to_csv(path, index=False)
            self.set_status(f"Data exported: {os.path.basename(path)}", "ok")
        except Exception as exc:
            self.set_status(f"Data export failed: {exc}", "error")
            messagebox.showerror("Export error", str(exc))

    # ------------------------------------------------------------------ #
    #  Multi-analysis HTML report                                        #
    # ------------------------------------------------------------------ #
    # Which result-label widget(s) hold the numeric summary for each
    # analysis. Analyses with no entry (or an empty/blank label) fall back to
    # "(no numeric summary for this view)" in the report -- the figure is
    # still included either way.
    _REPORT_LABEL_ATTRS = {
        "Powertrain Sizing": ["params_label"],
        "Acceleration": ["params_label"],
        "Parametric Study": ["params_label"],
        "Drive Cycle": ["params_label"],
        "Engine analysis": ["engine_results_label"],
        "Drive Cycle Efficiency": ["drive_cycle_efficiency_label"],
        "Range analysis": ["range_results_label", "drive_cycle_efficiency_label"],
        "MTPA / MTPV (PMSM)": ["mtpa_results_label"],
        "Mechanical Design (Motor)": ["mech_results_label"],
        "Motor BOM (Cost & Weight)": ["bom_results_label"],
    }

    def generate_report(self):
        """Ask which analyses to include, then build one HTML report holding
        an image + numeric summary per selected analysis -- not just whatever
        is on screen right now."""
        analyses = list(getattr(self, "analysis_sections", {}).keys()) or [self.plot_type.get()]
        self._open_report_picker(analyses)

    def _open_report_picker(self, analyses):
        popup = tk.Toplevel(self)
        popup.title("Generate Report")
        popup.configure(bg=COLORS['background'])
        popup.grab_set()

        ctk.CTkLabel(
            popup, text="Include these analyses in the report:",
            font=(COLORS_FONT, 13, "bold"),
        ).pack(pady=(14, 6), padx=16, anchor="w")
        ctk.CTkLabel(
            popup,
            text="Each selected analysis is re-plotted (using its currently\n"
                 "loaded data) and added as its own image + summary section.",
            font=(COLORS_FONT, 11), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(padx=16, anchor="w", pady=(0, 8))

        check_frame = ctk.CTkFrame(popup, fg_color="transparent")
        check_frame.pack(fill="both", expand=True, padx=16)
        current = getattr(self, "plot_mode", None) or self.plot_type.get()
        check_vars = {}
        for name in analyses:
            var = tk.BooleanVar(value=(name == current))
            ctk.CTkCheckBox(check_frame, text=name, variable=var).pack(anchor="w", pady=3)
            check_vars[name] = var

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkButton(btn_row, text="Select All", width=100,
                     command=lambda: [v.set(True) for v in check_vars.values()]).pack(side="left")
        ctk.CTkButton(btn_row, text="Clear", width=100,
                     command=lambda: [v.set(False) for v in check_vars.values()]).pack(side="left", padx=(6, 0))

        def on_generate():
            selected = [name for name in analyses if check_vars[name].get()]
            popup.destroy()
            if not selected:
                self.set_status("Report: no analyses selected.", "warn")
                return
            self._generate_multi_analysis_report(selected)

        ctk.CTkButton(popup, text="Generate Report", command=on_generate,
                     fg_color=COLORS["primary"]).pack(padx=16, pady=(6, 14), fill="x")
        popup.update_idletasks()
        h = min(560, 210 + 32 * len(analyses))
        popup.geometry(f"380x{h}")

    def _report_summary_html(self, name):
        """Text/table summary for one analysis's report section, drawn from
        whatever result label(s)/metrics that analysis actually has."""
        blocks = []
        for attr in self._REPORT_LABEL_ATTRS.get(name, []):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                text = widget.cget("text")
            except Exception:
                text = ""
            if text:
                blocks.append(f"<p>{text.replace(chr(10), '<br>')}</p>")

        if name == "Range analysis":
            metrics = getattr(self, "_last_range_metrics", None)
            if metrics:
                rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
                blocks.append(f"<table><tbody>{rows}</tbody></table>")

        if name == "Compare Standard Motor Data":
            motors = getattr(self, "selected_std_motors", None) or []
            if motors:
                rows = "".join(
                    f"<tr><td>{m.get('name', '')}</td><td>{m.get('gear_ratio', '')}</td>"
                    f"<td>{m.get('wheel_radius', '')}</td></tr>" for m in motors)
                blocks.append(
                    "<table><thead><tr><th>Motor</th><th>Gear Ratio</th>"
                    f"<th>Wheel Radius (m)</th></tr></thead><tbody>{rows}</tbody></table>")

        return "\n".join(blocks) if blocks else "<p><em>(no numeric summary for this view)</em></p>"

    def _report_widget_text(self, attr_name):
        w = getattr(self, attr_name, None)
        if w is None:
            return ""
        try:
            return str(w.get())
        except Exception:
            return ""

    def _report_gear_ratio(self):
        try:
            return float(self.gear_ratio.get())
        except Exception:
            return 1.0

    def _report_core_inputs_html(self):
        """Table of the shared vehicle/motor inputs almost every analysis is
        built on -- shown ONCE at the top of the report (they don't change
        between views/analyses, so repeating them per section would just be
        noise). Read defensively: a blank/invalid field just shows blank
        rather than aborting the whole report."""
        g = self._report_widget_text
        crr = g("crr")
        cd_a = g("cd_a")
        grad_unit = ("degrees (°)"
                     if hasattr(self, "gradient_unit_is_degrees")
                     and self.gradient_unit_is_degrees() else "percent (%)")
        have_m_map = getattr(self, "eff1_map_matrix", None) is not None
        have_c_map = getattr(self, "eff2_map_matrix", None) is not None
        batt_v, batt_i = g("batt_voltage"), g("batt_current_limit")
        batt_smooth_on = False
        try:
            batt_smooth_on = bool(self.battery_eff_smoothing_switch.get())
        except Exception:
            pass
        if not batt_v or not batt_i:
            batt_mode = "No battery DC limit (fields blank)"
        elif have_m_map or have_c_map:
            batt_mode = ("Map-aware: shaft cap = Vdc·Idc·η_motor(T,ω)·η_controller(T,ω), "
                         "solved per operating point"
                         + (" (map smoothed for this lookup)" if batt_smooth_on else ""))
        else:
            batt_mode = (f"Constant chain: shaft cap = Vdc·Idc·η with "
                         f"η = {g('batt_to_shaft_eff') or '1.0'} (no efficiency maps loaded)")
        thermal_on = False
        try:
            thermal_on = bool(self.thermal_overlay_switch.get())
        except Exception:
            pass
        rows = [
            ("Reference Mass (kg)", g("m_ref")),
            ("Rear Load Ratio", g("rear_load_ratio")),
            ("Ambient Temperature (°C)", g("ambient_temp")),
            ("Ambient Pressure (kPa)", g("ambient_pressure")),
            ("Crr", crr if crr else "(auto-estimated from mass)"),
            ("CdA (m²)", f"{cd_a} m²" if cd_a else "(auto-estimated from mass)"),
            ("Crr Speed Coefficient Crr1 (per m/s)", g("crr_speed_coeff") or "0 (constant Crr)"),
            ("Gear Ratio", g("gear_ratio")),
            ("Gear Efficiency", g("gear_efficiency")),
            ("Wheel Radius (m)", g("wheel_radius")),
            ("Peak Torque (Nm)", g("peak_torque")),
            ("Peak Power (kW)", g("peak_power")),
            ("Continuous Power (kW)", g("continuous_power")),
            ("Peak : Rated Torque Ratio", g("peak_to_rated_torque_ratio")),
            ("Wheel Inertia (kg·m², total)", g("wheel_inertia") or "0 (ignored)"),
            ("Battery Voltage (V)", g("batt_voltage") or "(no DC limit)"),
            ("Battery DC Current Limit (A)", g("batt_current_limit") or "(no DC limit)"),
            ("Battery Limit Evaluation", batt_mode),
            (f"Gradients ({grad_unit})", g("gradients")),
            ("Thermal Load Points (grad, speed, s)",
             (g("thermal_points") or "(none)") if thermal_on else "(overlay off)"),
            ("Motor Efficiency Map", "Uploaded" if have_m_map else "Not loaded (constant used)"),
            ("Controller Efficiency Map", "Uploaded" if have_c_map else "Not loaded (constant used)"),
            ("Speed Unit", g("speed_unit_combo")),
            ("Output (Torque / Force)", g("output_combo")),
            ("Motor Curve Source",
             "Uploaded Excel curve" if getattr(self, "motor_dataframe", None) is not None
             else "Theoretical (Peak Torque / Peak Power fields)"),
        ]
        trs = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
        return f"<table><tbody>{trs}</tbody></table>"

    def _report_analysis_inputs_html(self, analysis):
        """Extra inputs specific to one analysis's own model, for analyses
        that don't share the vehicle/motor inputs above at all. Returns ""
        for everything else (the shared table already covers them)."""
        g = self._report_widget_text
        if analysis == "MTPA / MTPV (PMSM)":
            rows = [
                ("Pole Pairs (p)", g("mtpa_pole_pairs")),
                ("Ld (mH)", g("mtpa_ld_mh")),
                ("Lq (mH)", g("mtpa_lq_mh")),
                ("PM Flux Linkage ψ_PM (Wb)", g("mtpa_psi_pm")),
                ("Max Current (A)", g("mtpa_imax")),
                ("Winding Connection", g("mtpa_conn_combo")),
                ("Current Given As", g("mtpa_current_qty_combo")),
                ("Current Value Is", g("mtpa_current_meas_combo")),
                ("DC Link Voltage (V)", g("mtpa_vdc")),
                ("Voltage Limit (PWM)", g("mtpa_vlimit_combo")),
                ("Max Speed (RPM)", g("mtpa_max_rpm")),
            ]
            trs = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
            return f"<p><strong>Analysis inputs</strong></p><table><tbody>{trs}</tbody></table>"
        if analysis == "Mechanical Design (Motor)":
            # Rows come straight from the active Design Check's widgets, so
            # this stays in sync with whatever check/inputs are selected.
            try:
                rows = self._mech_report_input_rows()
            except Exception:
                return ""
            trs = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
            return f"<p><strong>Analysis inputs</strong></p><table><tbody>{trs}</tbody></table>"
        return ""

    # ---- assumptions + engineering observations ------------------------- #
    def _report_assumptions_html(self):
        """The model assumptions this run actually used, generated from the
        current toggles/inputs rather than boilerplate, so the report states
        what the numbers really assume."""
        g = self._report_widget_text
        items = [
            "Quasi-steady longitudinal vehicle model: "
            "F = m·g·Crr·cosθ + ½·ρ·CdA·v² + m·g·sinθ + m·a. No wind, tyre "
            "slip, cornering losses, or road-surface variation are modeled.",
        ]
        crr_src = ("entered manually" if getattr(self, "crr_manual", False)
                   else "estimated from the calibrated reference-mass lookup")
        cda_src = ("entered manually" if getattr(self, "cda_manual", False)
                   else "estimated from the calibrated reference-mass lookup "
                        "(temperature/pressure corrected)")
        items.append(f"Crr is {crr_src}; CdA is {cda_src}.")
        alt_on = False
        try:
            alt_on = bool(self.alt_density_toggle.get())
        except Exception:
            pass
        if alt_on:
            items.append(
                f"Air density: ISA model at altitude {g('altitude_m') or '0'} m "
                "and the ambient-temperature input (applies to Range / Drive "
                "Cycle Efficiency; capability plots keep ρ = 1.225 kg/m³).")
        else:
            items.append("Air density fixed at ρ = 1.225 kg/m³ (sea level, 15 °C).")
        crr1 = g("crr_speed_coeff")
        try:
            crr1_active = bool(crr1) and abs(float(crr1)) > 0
        except Exception:
            crr1_active = False
        if crr1_active:
            items.append(f"Velocity-dependent rolling resistance: Crr(v) = Crr + {crr1}·v (v in m/s).")
        else:
            items.append("Rolling-resistance coefficient is constant with speed (Crr1 = 0).")
        grad_deg = (hasattr(self, "gradient_unit_is_degrees")
                    and self.gradient_unit_is_degrees())
        items.append(
            "Gradients are entered in "
            + ("degrees and converted internally to slope percent (tan θ · 100)"
               if grad_deg else "slope percent (rise/run × 100)")
            + "; the physics uses θ = arctan(grade%/100).")
        items.append(
            f"Single fixed reduction: gear ratio {g('gear_ratio') or '1'}:1 at "
            f"transmission efficiency {g('gear_efficiency') or '1'}.")
        wheel_j = g("wheel_inertia")
        try:
            j_active = bool(wheel_j) and float(wheel_j) > 0
        except Exception:
            j_active = False
        if j_active:
            items.append(
                f"Wheel rotational inertia J = {wheel_j} kg·m² adds J/r² of "
                "translational-equivalent mass to the inertial (m·a) terms only; "
                "steady-state results are unaffected.")
        else:
            items.append("Rotating-mass inertia is neglected (Wheel Inertia J = 0).")
        have_m_map = getattr(self, "eff1_map_matrix", None) is not None
        have_c_map = getattr(self, "eff2_map_matrix", None) is not None
        items.append(
            "Motor efficiency: " + ("uploaded map" if have_m_map else
                                    f"constant {g('motor_eff_const') or '0.90'}")
            + "; controller efficiency: "
            + ("uploaded map" if have_c_map else f"constant {g('controller_eff_const') or '0.95'}")
            + ". Braking/regen re-uses the motoring efficiency at |T| "
              "(no separate regen map is measured on datasheets).")
        batt_v, batt_i = g("batt_voltage"), g("batt_current_limit")
        if batt_v and batt_i:
            if have_m_map or have_c_map:
                batt_smooth_on = False
                try:
                    batt_smooth_on = bool(self.battery_eff_smoothing_switch.get())
                except Exception:
                    pass
                items.append(
                    f"Battery DC limit {batt_v} V × {batt_i} A evaluated AFTER the "
                    "efficiency maps: shaft torque satisfies |T|·ω ≤ "
                    "Vdc·Idc·η_motor(T,ω)·η_controller(T,ω) at every point of every "
                    "capability curve. Demand curves (drive cycle, range) are not clipped."
                    + (" The map's blank (NaN) coverage gaps are filled and blended "
                       "for this lookup only (Smooth Map for Battery Limit is ON); "
                       "measured cells keep their exact values, so real features such "
                       "as the base-speed corner stay sharp while the coverage edge "
                       "no longer produces an abrupt efficiency cliff in the capped curve."
                       if batt_smooth_on else
                       " Smooth Map for Battery Limit is OFF: a NaN-holed map can show "
                       "a visible step in the capped curve right at its coverage edge — "
                       "turn the toggle on if that looks wrong."))
            else:
                items.append(
                    f"Battery DC limit {batt_v} V × {batt_i} A with constant "
                    f"battery-to-shaft efficiency {g('batt_to_shaft_eff') or '1.0'} "
                    "(no efficiency maps loaded). Demand curves are not clipped.")
        else:
            items.append("No battery DC power limit applied (fields left blank).")
        trap = False
        try:
            trap = str(self.integration_method.get()).lower().startswith("trap")
        except Exception:
            pass
        items.append("Drive-cycle energy integrated "
                     + ("trapezoidally over the time vector."
                        if trap else "by rectangular cumulative sum (original method)."))
        cap = g("regen_cap_w")
        items.append(f"Regenerative braking capped at {cap} W battery acceptance."
                     if cap else "Regenerative braking acceptance is uncapped.")
        items.append(f"Auxiliary electrical load of {g('aux_loss') or '25'} W "
                     "is drawn during motoring only.")
        try:
            s, p = g("cells_series"), g("cells_parallel")
            v, ah, dod = g("cell_voltage"), g("cell_capacity"), g("dod")
            if s and p:
                items.append(
                    f"Battery pack {s}s{p}p, cell {v} V × {ah} Ah; usable energy "
                    f"= pack energy × DoD {dod}%.")
        except Exception:
            pass
        items.append(
            f"Continuous (rated) torque taken as peak torque / "
            f"{g('peak_to_rated_torque_ratio') or '2'} (thermal steady-state assumed).")
        lis = "".join(f"<li>{i}</li>" for i in items)
        return f"<ul>{lis}</ul>"

    def _report_vehicle_capability(self):
        """Flat-road top speed, max startable gradient and 0-target time from
        the CURRENT inputs, using the same estimators the Parametric Study
        uses (so the observations agree with the plots). None when the
        vehicle/motor inputs don't parse."""
        try:
            from .physics import calculate_crr_cd_a
            crr_txt = self.crr.get().strip()
            cda_txt = self.cd_a.get().strip()
            params = calculate_crr_cd_a(
                float(self.m_ref.get()), float(self.rear_load_ratio.get()),
                float(self.ambient_temp.get()), float(self.ambient_pressure.get()),
                crr=float(crr_txt) if (getattr(self, "crr_manual", False) and crr_txt) else None,
                cd_a=float(cda_txt) if (getattr(self, "cda_manual", False) and cda_txt) else None,
            )
            wheel_radius = float(self.wheel_radius.get())
            gear_ratio = float(self.gear_ratio.get())
            peak_torque = float(self.peak_torque.get())
            peak_power = float(self.peak_power.get())
            speeds = np.linspace(0.1, 160.0, 2000)
            force = self._compute_available_wheel_force(
                speeds, wheel_radius, peak_torque, peak_power, gear_ratio)
            mass = float(params["m_i"])
            top = self._estimate_top_speed(speeds, force, mass, params["Crr"], params["CdA"])
            grad = self._estimate_max_gradability(
                speeds, force, mass, params["Crr"], params["CdA"], 60.0, 0.5)
            try:
                target = float(self.target_speed.get())
                t_max = float(self.max_time.get())
            except Exception:
                target, t_max = 60.0, 60.0
            accel_t = self._estimate_acceleration_time(
                speeds, force, mass, params["Crr"], params["CdA"], target, t_max)
            return dict(top_speed_kmh=float(top), max_grad_pct=float(grad),
                        accel_target_kmh=target, accel_time_s=accel_t,
                        speeds=speeds, force=force, params=params,
                        wheel_radius=wheel_radius, gear_ratio=gear_ratio)
        except Exception:
            return None

    _CAPABILITY_ANALYSES = ("Powertrain Sizing", "Acceleration",
                            "Parametric Study", "Compare Standard Motor Data")

    def _report_observations(self, analysis):
        """Engineering observations for one analysis, as plain sentences
        derived from the currently loaded data. Only states what the model
        actually computed; anything unavailable is simply omitted."""
        obs = []
        if analysis in self._CAPABILITY_ANALYSES:
            cap = self._report_vehicle_capability()
            if cap:
                from .units import gradient_pct_to_deg
                if cap["top_speed_kmh"] > 0:
                    obs.append(
                        f"Estimated flat-road top speed ≈ {cap['top_speed_kmh']:.1f} km/h "
                        "(peak capability curve vs. total resistive force).")
                obs.append(
                    f"Maximum startable gradient ≈ {cap['max_grad_pct']:.1f}% "
                    f"({gradient_pct_to_deg(cap['max_grad_pct']):.1f}°) with the peak curve.")
                t = cap["accel_time_s"]
                if t is not None and np.isfinite(t):
                    obs.append(
                        f"0–{cap['accel_target_kmh']:.0f} km/h in ≈ {t:.1f} s "
                        "(flat road, peak torque available).")
                else:
                    obs.append(
                        f"The target speed of {cap['accel_target_kmh']:.0f} km/h is not "
                        "reached within the simulation window on flat road.")
                p_dc = None
                try:
                    p_dc = self.get_battery_dc_power_w()
                except Exception:
                    pass
                if p_dc is not None:
                    mode = ("evaluated through the motor × controller efficiency maps"
                            if (getattr(self, "eff1_map_matrix", None) is not None
                                or getattr(self, "eff2_map_matrix", None) is not None)
                            else "with the constant battery-to-shaft efficiency")
                    obs.append(
                        f"Battery DC limit active: {p_dc / 1000.0:.2f} kW DC, {mode}; "
                        "high-speed capability may be battery-limited rather than "
                        "motor-limited.")
                # Thermal duty points vs. available capability.
                try:
                    pts = self.compute_thermal_load_points()
                except Exception:
                    pts = []
                for i, p in enumerate(pts, 1):
                    avail_wheel_force = float(np.interp(
                        p["v_kmh"], cap["speeds"], cap["force"]))
                    avail_wheel_tq = avail_wheel_force * cap["wheel_radius"]
                    if avail_wheel_tq > 1e-9:
                        util = 100.0 * p["wheel_torque"] / avail_wheel_tq
                        verdict = ("EXCEEDS peak capability" if util > 100.0
                                   else f"uses {util:.0f}% of peak capability")
                        obs.append(
                            f"Thermal load point {i} "
                            f"({self.fmt_gradient(p['grad_pct'])} @ {p['v_kmh']:.0f} km/h "
                            f"for {p['duration_s']:g} s) demands {p['motor_torque']:.1f} Nm "
                            f"at {p['motor_rpm']:.0f} RPM — {verdict}.")

        elif analysis == "Drive Cycle":
            df_dc = getattr(self, "dataframe", None)
            if df_dc is not None and "dc_time" in df_dc and "dc_speed" in df_dc:
                try:
                    t = np.asarray(df_dc["dc_time"], dtype=float)
                    v = np.asarray(df_dc["dc_speed"], dtype=float)
                    ok = np.isfinite(t) & np.isfinite(v)
                    t, v = t[ok], v[ok]
                    if t.size > 2:
                        dur = float(t[-1] - t[0])
                        v_mps = v / 3.6
                        dist_km = float(np.trapz(v_mps, t) / 1000.0)
                        acc = np.diff(v_mps) / np.maximum(np.diff(t), 1e-9)
                        obs.append(
                            f"Cycle: {dur:.0f} s, {dist_km:.2f} km, "
                            f"v_max {np.max(v):.1f} km/h, v_avg {np.mean(v):.1f} km/h, "
                            f"idle share {100.0 * np.mean(v < 0.5):.0f}%.")
                        obs.append(
                            f"Peak acceleration {np.max(acc):.2f} m/s², "
                            f"peak deceleration {abs(np.min(acc)):.2f} m/s² — the "
                            "torque-speed scatter/heatmap shows where these demands "
                            "sit on the motor map.")
                except Exception:
                    pass

        elif analysis == "Drive Cycle Efficiency":
            m = getattr(self, "_last_dce_metrics", None)
            if m:
                try:
                    obs.append(
                        f"Energy-weighted drive-cycle efficiency (motor × controller) "
                        f"= {m['energy_eff']:.1f}%; unweighted average over motoring "
                        f"points = {m['avg_eff']:.1f}%. A large gap between the two "
                        "means much of the energy is spent away from the map's sweet spot.")
                    if m.get("e_batt_in", 0) > 1e-9:
                        regen_share = 100.0 * m.get("e_regen", 0.0) / m["e_batt_in"]
                        obs.append(
                            f"Regen returns {m.get('e_regen', 0.0):.1f} Wh "
                            f"({regen_share:.1f}% of the motoring battery energy); "
                            f"net efficiency including regen = {m.get('net_eff', 0.0):.1f}%.")
                except Exception:
                    pass

        elif analysis == "Range analysis":
            m = getattr(self, "_last_range_metrics", None)
            if m:
                try:
                    loss_terms = {
                        "aerodynamic": m.get("aerodynamic_loss_per_km", 0.0),
                        "rolling": m.get("rolling_loss_per_km", 0.0),
                        "grade": m.get("grade_loss_per_km", 0.0),
                        "inertia": m.get("inertia_loss_motoring_per_km", 0.0),
                        "transmission": m.get("transmission_loss_per_km", 0.0),
                        "motor": m.get("motor_loss_per_km", 0.0),
                        "controller": m.get("controller_loss_per_km", 0.0),
                        "auxiliary": m.get("aux_loss_total_per_km", 0.0),
                    }
                    ranked = sorted(loss_terms.items(), key=lambda kv: kv[1], reverse=True)
                    gross = m.get("gross_loss_per_km", 0.0)
                    if gross > 1e-9:
                        top2 = ", ".join(
                            f"{k} ({v:.1f} Wh/km, {100.0 * v / gross:.0f}%)"
                            for k, v in ranked[:2])
                        obs.append(f"Dominant energy sinks per km: {top2}.")
                    if m.get("estimated_range_km") is not None:
                        obs.append(
                            f"Estimated range {m['estimated_range_km']:.1f} km at a net "
                            f"consumption of {m.get('net_energy_loss_per_km', 0.0):.1f} Wh/km "
                            "(net battery draw including the auxiliary load, minus "
                            "accepted regen).")
                    regen = m.get("regen_energy_per_km", 0.0)
                    if gross > 1e-9 and regen > 0:
                        obs.append(
                            f"Regen recovers {regen:.1f} Wh/km "
                            f"({100.0 * regen / gross:.0f}% of gross losses).")
                    obs.append(
                        f"Cycle-average efficiencies (motoring): motor "
                        f"{100.0 * m.get('motor_eff', 0.0):.1f}%, controller "
                        f"{100.0 * m.get('controller_eff', 0.0):.1f}%, wheel-to-battery "
                        f"{100.0 * m.get('drive_cycle_eff', 0.0):.1f}%.")
                except Exception:
                    pass

        elif analysis == "Motor BOM (Cost & Weight)":
            tree = getattr(self, "bom_tree", None)
            if tree is not None:
                try:
                    from .bom import node_value
                    total_cost = node_value(tree, "cost")
                    total_weight = node_value(tree, "weight")
                    obs.append(f"Total BOM cost ₹{total_cost:,.0f}; "
                               f"total weight {total_weight / 1000.0:.2f} kg.")
                    kids = [(c.get("name", "?"), node_value(c, "cost") * float(tree.get("qty", 1)))
                            for c in tree.get("children", [])]
                    if kids and total_cost > 1e-9:
                        name, val = max(kids, key=lambda kv: kv[1])
                        obs.append(
                            f"Largest cost contributor: {name} "
                            f"(₹{val:,.0f}, {100.0 * val / max(total_cost, 1e-9):.0f}% of total) "
                            "— see the Pareto view for the full ranking.")
                except Exception:
                    pass

        return obs

    def _report_observations_html(self, analysis):
        try:
            obs = self._report_observations(analysis) or []
        except Exception:
            return ""
        if not obs:
            return ""
        lis = "".join(f"<li>{o}</li>" for o in obs)
        return ("<div class='obs'><p><strong>Engineering observations</strong></p>"
                f"<ul>{lis}</ul></div>")

    def _render_report_views(self, analysis):
        """Render every distinct plot this analysis contributes to the
        report -- the full view hierarchy the UI itself offers (every
        Parametric study type, every Range panel, every Mechanical Design
        check, every efficiency map, ...), not just whichever view happens
        to be selected. Returns a list of (subtitle, b64_png,
        summary_override) tuples; subtitle is "" for a single-view analysis
        and summary_override is None unless the view needs its OWN summary
        (Mechanical Design: the results label + inputs change per check).

        Explicit per-analysis branches on purpose (not a generic loop): each
        analysis's "views" are controlled by completely different widgets
        (Output/Plotting Part combos, the heatmap switch, the map buttons,
        the Compare radio buttons, the Parametric/Range/Design-Check combos,
        ...), so a one-size-fits-all abstraction would just hide what's
        actually being toggled. If you add a new analysis mode with more
        than one meaningful view, add a branch here; everything else falls
        through to the single generic `update_plot()` capture at the bottom.
        """
        self.plot_type.set(analysis)
        self.plot_mode = analysis
        self.show_sections_for_analysis(analysis)
        out = []

        def snap(subtitle="", summary_override=None):
            self.canvas.draw()
            self.update_idletasks()
            png = io.BytesIO()
            self.figure.savefig(png, format="png", dpi=150, bbox_inches="tight",
                                facecolor=self.figure.get_facecolor())
            png.seek(0)
            # The engineering interpretation is computed AT CAPTURE TIME so
            # view-specific state (the Parametric study type, Range panel,
            # Design Check, ...) is still active. A failed builder returns ""
            # and the view simply carries no interpretation.
            try:
                from .report_insights import view_interpretation_html
                interp = view_interpretation_html(self, analysis, subtitle)
            except Exception:
                interp = ""
            out.append((subtitle, base64.b64encode(png.read()).decode("ascii"),
                        summary_override, interp))

        if analysis == "Powertrain Sizing":
            # Torque always gets its own view; Force is wheel-only (locked)
            # so it never needs a motor-side duplicate. The motor-side Torque
            # view only adds information when the gearbox actually changes
            # the numbers, i.e. gear ratio != 1.
            self.output_combo.set("Torque")
            self.plot_part_combo.set("At Wheel")
            self.update_plot()
            snap("Torque - At Wheel")
            if abs(self._report_gear_ratio() - 1.0) > 1e-9:
                self.plot_part_combo.set("At Motor")
                self.update_plot()
                snap("Torque - At Motor")
            self.output_combo.set("Force")
            self.update_plot()
            snap("Force (Wheel)")

        elif analysis == "Drive Cycle":
            if getattr(self, "dataframe", None) is None:
                self.show_placeholder_message("Insert Data or click the plot button")
                snap()
            else:
                self.plot_drive_cycle()
                snap("Speed vs Time")
                if hasattr(self, "heatmap_var"):
                    self.heatmap_var.set(False)
                self.plot_torque_speed_drive_cycle(show_popup=False)
                snap("Torque-Speed Scatter")
                if hasattr(self, "heatmap_var"):
                    self.heatmap_var.set(True)
                    self.plot_torque_speed_drive_cycle(show_popup=False)
                    snap("Torque-Speed Heatmap")
                    self.heatmap_var.set(False)

        elif analysis == "Drive Cycle Efficiency":
            # This analysis is entirely button-driven in the UI (dispatch's
            # plot_graph has no branch for it), so update_plot() alone would
            # just land on the "Insert Data or Update Plot" placeholder --
            # each map view has to be called directly. For the report the
            # extrapolate-to-envelope smoothing is forced ON (dense regrid +
            # gap fill -> publication-quality contours instead of blocky
            # quads); the user's own Graph Settings value is restored after.
            have1 = getattr(self, "efficiency_data_1", None) is not None
            have2 = getattr(self, "efficiency_data_2", None) is not None
            _gs_key = ("Drive Cycle Efficiency", "extrapolate_gaps")
            _had_gs = _gs_key in getattr(self, "_gs_values", {})
            _prev_gs = self._gs_values.get(_gs_key) if _had_gs else None
            self._gs_values[_gs_key] = True
            try:
                if have1:
                    self.plot_efficiency_map_motor1()
                    snap("Motor Efficiency Map")
                    self.plot_efficiency_map_regen()
                    snap("Regen (Braking) Efficiency Map")
                if have2:
                    self.plot_efficiency_map_motor2()
                    snap("Controller Efficiency Map")
                if have1 and have2:
                    self.plot_efficiency_map_combined()
                    snap("Combined Efficiency Map (Motor × Controller)")
                    self.plot_efficiency_difference_map()
                    snap("Efficiency Difference Map (Controller − Motor)")
            finally:
                if _had_gs:
                    self._gs_values[_gs_key] = _prev_gs
                else:
                    self._gs_values.pop(_gs_key, None)
            if not out:
                self.show_placeholder_message(
                    "Upload the Motor and/or Controller efficiency maps to see this analysis.")
                snap()

        elif analysis == "Compare Standard Motor Data":
            if not getattr(self, "selected_std_motors", None):
                self.show_placeholder_message("Choose at least one standard motor to compare.")
                snap()
            else:
                self.compare_std_plot_var.set("torque")
                self.update_compare_std_plot()
                snap("Torque")
                self.compare_std_plot_var.set("force")
                self.update_compare_std_plot()
                snap("Force")
                self.compare_std_plot_var.set("acceleration")
                self.update_compare_std_plot()
                snap("Acceleration")
                have_current_map = getattr(self, "eff1_map_torques", None) is not None
                have_saved_map = any(e.get("eff_map") for e in self.selected_std_motors)
                if have_current_map and have_saved_map:
                    self.compare_std_plot_var.set("efficiency")
                    self.update_compare_std_plot()
                    snap("Efficiency Map")

        elif analysis == "Parametric Study":
            # One section per study type -- the full hierarchy the combo
            # offers, not just whichever sweep was last selected.
            combo = getattr(self, "parametric_graph_combo", None)
            if combo is None:
                self.update_plot()
                snap()
            else:
                prev = combo.get()
                try:
                    for graph_type in list(combo.cget("values")):
                        combo.set(graph_type)
                        self.update_plot()
                        snap(graph_type)
                finally:
                    combo.set(prev)

        elif analysis == "Range analysis":
            toggle = getattr(self, "range_plot_toggle", None)
            if toggle is None or getattr(self, "dataframe", None) is None:
                self.update_plot()
                snap()
            else:
                # Every individual panel; "All" is skipped because it's just
                # the first four of these tiled smaller in one figure.
                subtitles = {
                    "Power": "Power vs Time",
                    "Energy": "Cumulative Energy vs Time",
                    "C-rate": "Battery C-rate vs Time",
                    "Loss": "Component Energy Loss",
                    "Waterfall": "Loss Waterfall (Wh/km)",
                    "Drive": "Drive Cycle Panels",
                    "M Eff": "Motor Efficiency Map",
                    "C Eff": "Controller Efficiency Map",
                }
                prev = toggle.get()
                # Publication-quality map panels for the report: force the
                # Range extrapolate/smooth setting on, restore afterwards.
                _gs_key = ("Range analysis", "extrapolate_gaps")
                _had_gs = _gs_key in getattr(self, "_gs_values", {})
                _prev_gs = self._gs_values.get(_gs_key) if _had_gs else None
                self._gs_values[_gs_key] = True
                try:
                    for view in list(toggle.cget("values")):
                        if view == "All":
                            continue
                        toggle.set(view)
                        self.update_plot()
                        snap(subtitles.get(view, view))
                finally:
                    toggle.set(prev)
                    if _had_gs:
                        self._gs_values[_gs_key] = _prev_gs
                    else:
                        self._gs_values.pop(_gs_key, None)

        elif analysis == "Mechanical Design (Motor)":
            combo = getattr(self, "mech_check_combo", None)
            if combo is None:
                self.update_plot()
                snap()
            else:
                prev = combo.get()
                try:
                    for check in list(combo.cget("values")):
                        combo.set(check)
                        self.update_plot()
                        # The results label AND the active-check inputs change
                        # per check, so each view carries its own summary
                        # (the generic per-analysis one would show only the
                        # last check's numbers under every view).
                        override = (self._report_analysis_inputs_html(analysis)
                                    + self._report_summary_html(analysis))
                        snap(check, summary_override=override)
                finally:
                    combo.set(prev)

        elif analysis == "Motor BOM (Cost & Weight)":
            if getattr(self, "bom_tree", None) is None:
                self.update_plot()
                snap()
            else:
                # Where the cost goes, where the weight goes, the sorted
                # drivers of both, and the group split. View/metric combos
                # are snapshotted and restored like the other report widgets.
                prev = (self.bom_view_combo.get(), self.bom_metric_combo.get())
                views = [
                    ("Sankey Diagram", "Cost (₹)", "Sankey - Cost"),
                    ("Sankey Diagram", "Weight (g)", "Sankey - Weight"),
                    ("Pareto (Max → Min)", "Cost (₹)", "Pareto - Cost"),
                    ("Pareto (Max → Min)", "Weight (g)", "Pareto - Weight"),
                    ("Group Split", "Cost (₹)", "Group Split - Cost"),
                ]
                if getattr(self, "bom_tree_b", None) is not None:
                    views.append(("Compare A vs B", "Cost (₹)",
                                  "Compare A vs B - Cost"))
                try:
                    for view, met, subtitle in views:
                        self.bom_view_combo.set(view)
                        self.bom_metric_combo.set(met)
                        self.update_plot()
                        snap(subtitle)
                finally:
                    self.bom_view_combo.set(prev[0])
                    self.bom_metric_combo.set(prev[1])

        else:
            # Acceleration, Engine analysis: no sub-views. MTPA/MTPV already
            # defaults to its "All" multi-panel dashboard covering every
            # quantity in one figure.
            self.update_plot()
            snap()

        return out

    def generate_report(self):
        """Ask which analyses to include, then build one HTML report holding
        every plot view + numeric summary for each -- not just whatever
        single view is on screen right now."""
        analyses = list(getattr(self, "analysis_sections", {}).keys()) or [self.plot_type.get()]
        self._open_report_picker(analyses)

    def _open_report_picker(self, analyses):
        popup = tk.Toplevel(self)
        popup.title("Generate Report")
        popup.configure(bg=COLORS['background'])
        popup.grab_set()

        ctk.CTkLabel(
            popup, text="Include these analyses in the report:",
            font=(COLORS_FONT, 13, "bold"),
        ).pack(pady=(14, 6), padx=16, anchor="w")
        ctk.CTkLabel(
            popup,
            text="Every distinct plot for each selected analysis is included\n"
                 "(e.g. Powertrain Sizing gets Torque AND Force, both wheel-\n"
                 "and motor-side when the gear ratio isn't 1:1), using whatever\n"
                 "data is already loaded. Vehicle/motor inputs are listed once\n"
                 "at the top of the report.",
            font=(COLORS_FONT, 11), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(padx=16, anchor="w", pady=(0, 8))

        check_frame = ctk.CTkFrame(popup, fg_color="transparent")
        check_frame.pack(fill="both", expand=True, padx=16)
        current = getattr(self, "plot_mode", None) or self.plot_type.get()
        check_vars = {}
        for name in analyses:
            var = tk.BooleanVar(value=(name == current))
            ctk.CTkCheckBox(check_frame, text=name, variable=var).pack(anchor="w", pady=3)
            check_vars[name] = var

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkButton(btn_row, text="Select All", width=100,
                     command=lambda: [v.set(True) for v in check_vars.values()]).pack(side="left")
        ctk.CTkButton(btn_row, text="Clear", width=100,
                     command=lambda: [v.set(False) for v in check_vars.values()]).pack(side="left", padx=(6, 0))

        def on_generate():
            selected = [name for name in analyses if check_vars[name].get()]
            popup.destroy()
            if not selected:
                self.set_status("Report: no analyses selected.", "warn")
                return
            self._generate_multi_analysis_report(selected)

        ctk.CTkButton(popup, text="Generate Report", command=on_generate,
                     fg_color=COLORS["primary"]).pack(padx=16, pady=(6, 14), fill="x")
        popup.update_idletasks()
        h = min(600, 260 + 32 * len(analyses))
        popup.geometry(f"420x{h}")

    def _generate_multi_analysis_report(self, analyses):
        path = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".html",
            initialfile="vmi_report.html",
            filetypes=[("HTML report", "*.html"), ("All files", "*.*")],
        )
        if not path:
            return

        # Snapshot everything the report is about to change so it can be put
        # back exactly as the user left it once generation finishes.
        original_mode = self.plot_type.get()
        original_output = self._report_widget_text("output_combo")
        original_plot_part = self._report_widget_text("plot_part_combo")
        original_heatmap = bool(self.heatmap_var.get()) if hasattr(self, "heatmap_var") else None
        original_compare_plot = self._report_widget_text("compare_std_plot_var") or None

        sections = []
        toc = ["<li><a href='#inputs'>Parameters (Vehicle &amp; Motor Inputs)</a></li>",
               "<li><a href='#assumptions'>Model Assumptions</a></li>"]
        try:
            self.configure(cursor="watch")
            self.update_idletasks()
        except Exception:
            pass
        try:
            n_views_total = 0
            for name in analyses:
                try:
                    views = self._render_report_views(name)
                    analysis_inputs_html = self._report_analysis_inputs_html(name)
                    summary_html = self._report_summary_html(name)
                    observations_html = self._report_observations_html(name)
                    first_has_interp = bool(views and len(views[0]) > 3 and views[0][3])
                    for i, (subtitle, b64, override, interp) in enumerate(views):
                        anchor = f"section-{len(sections)}"
                        title = f"{name} — {subtitle}" if subtitle else name
                        if override is not None:
                            # View-specific summary (already includes its own
                            # inputs where relevant, e.g. Mechanical Design).
                            section_summary = override
                        else:
                            section_summary = summary_html
                            if i == 0 and analysis_inputs_html:
                                section_summary = analysis_inputs_html + section_summary
                        # The per-view interpretation supersedes the old
                        # analysis-level observations box; keep the latter only
                        # for analyses whose views carry no interpretation.
                        if i == 0 and observations_html and not first_has_interp:
                            section_summary += observations_html
                        sections.append(_REPORT_SECTION_TEMPLATE.format(
                            anchor=anchor, name=title, b64=b64,
                            interp=(interp or ""), summary=section_summary))
                        toc.append(f"<li><a href='#{anchor}'>{title}</a></li>")
                        n_views_total += 1
                except Exception as exc:
                    anchor = f"section-{len(sections)}"
                    sections.append(
                        f"<div class='card' id='{anchor}'><h2>{name}</h2>"
                        f"<p style='color:#b91c1c'>Could not render this analysis: {exc}</p></div>")
                    toc.append(f"<li><a href='#{anchor}'>{name}</a></li>")
        finally:
            try:
                self.plot_type.set(original_mode)
                if original_output:
                    self.output_combo.set(original_output)
                if original_plot_part:
                    self.plot_part_combo.set(original_plot_part)
                if original_heatmap is not None:
                    self.heatmap_var.set(original_heatmap)
                if original_compare_plot:
                    self.compare_std_plot_var.set(original_compare_plot)
                self.update_plot()
            except Exception:
                pass
            try:
                self.configure(cursor="")
            except Exception:
                pass

        try:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            inputs_html = self._report_core_inputs_html()
            assumptions_html = self._report_assumptions_html()
            html = _MULTI_REPORT_TEMPLATE.format(
                stamp=stamp, inputs=inputs_html, assumptions=assumptions_html,
                toc="\n".join(toc), sections="\n".join(sections),
                primary=COLORS["primary"], dark=COLORS["header_bg"],
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            self.set_status(
                f"Report saved: {os.path.basename(path)} "
                f"({len(analyses)} analyses, {n_views_total} plots)", "ok")
            try:
                webbrowser.open("file://" + os.path.abspath(path))
            except Exception:
                pass
        except Exception as exc:
            self.set_status(f"Report failed: {exc}", "error")
            messagebox.showerror("Report error", str(exc))

    # ------------------------------------------------------------------ #
    #  Scenario save / load (all entry + combobox + switch values)       #
    # ------------------------------------------------------------------ #
    def _scenario_widgets(self):
        """Yield (name, widget, kind) for every savable input widget."""
        for name, w in vars(self).items():
            if isinstance(w, ctk.CTkEntry):
                yield name, w, "entry"
            elif isinstance(w, ctk.CTkComboBox):
                yield name, w, "combo"
            elif isinstance(w, ctk.CTkSegmentedButton):
                yield name, w, "segment"
            elif isinstance(w, (ctk.CTkSwitch, ctk.CTkCheckBox)):
                yield name, w, "toggle"

    # ---- loaded-data persistence (DataFrames / maps + their tick marks) ---- #
    @staticmethod
    def _to_py_scalar(x):
        return x.item() if hasattr(x, "item") else x

    def _enc_obj(self, v):
        """JSON-safe encoding for the data objects a scenario can carry."""
        import numpy as np
        import pandas as pd
        if v is None:
            return None
        if isinstance(v, pd.DataFrame):
            safe = v.where(pd.notnull(v), None)
            return {
                "__k": "df",
                "index": [self._to_py_scalar(i) for i in v.index],
                "columns": [self._to_py_scalar(c) for c in v.columns],
                "data": safe.values.tolist(),
            }
        if isinstance(v, np.ndarray):
            return {"__k": "nd", "data": np.asarray(v, dtype=float).tolist()}
        if isinstance(v, (np.floating, np.integer)):
            return self._to_py_scalar(v)
        if isinstance(v, dict):
            return {"__k": "dict", "items": [[k, self._enc_obj(val)] for k, val in v.items()]}
        if isinstance(v, (list, tuple)):
            return [self._enc_obj(x) for x in v]
        # Final fallback: keep JSON-native scalars; drop anything else (e.g. the
        # cached scipy spline stored in engine_efficiency_curves, which the
        # engine code rebuilds on demand from the raw rpm/eff arrays).
        if isinstance(v, (str, int, float, bool)):
            return v
        return None

    def _dec_obj(self, v):
        import numpy as np
        import pandas as pd
        if isinstance(v, dict):
            k = v.get("__k")
            if k == "df":
                return pd.DataFrame(v["data"], index=v["index"], columns=v["columns"])
            if k == "nd":
                return np.asarray(v["data"], dtype=float)
            if k == "dict":
                return {key: self._dec_obj(val) for key, val in v["items"]}
            return v
        if isinstance(v, list):
            return [self._dec_obj(x) for x in v]
        return v

    @staticmethod
    def _is_present(v):
        import pandas as pd
        if v is None:
            return False
        if isinstance(v, pd.DataFrame):
            return not v.empty
        if isinstance(v, dict):
            return len(v) > 0
        return True

    def _dataset_slots(self):
        """Every loadable dataset: which attributes hold it, and the indicator /
        buttons that show it's loaded."""
        return [
            dict(name="drive_cycle", attrs=["dataframe"], primary="dataframe",
                 indicator="drive_cycle_indicator",
                 buttons=["drive_cycle_delete_button", "plot_drive_cycle_button", "plot_torque_speed_button"]),
            dict(name="motor_data", attrs=["motor_dataframe", "motor_curve_source"], primary="motor_dataframe",
                 indicator="motor_data_indicator", buttons=["motor_data_delete_button"]),
            dict(name="engine_data", attrs=["engine_dataframe"], primary="engine_dataframe",
                 indicator="engine_data_indicator", buttons=["engine_data_delete_button"]),
            dict(name="engine_eff", attrs=["engine_efficiency_curves"], primary="engine_efficiency_curves",
                 indicator="engine_eff_indicator", buttons=["engine_eff_delete_button"]),
            dict(name="eff1", attrs=["efficiency_data_1", "eff1_map_torques", "eff1_map_rpms", "eff1_map_matrix"],
                 primary="efficiency_data_1", indicator="eff1_indicator", buttons=["eff1_delete_button"]),
            dict(name="eff2", attrs=["efficiency_data_2", "eff2_map_torques", "eff2_map_rpms", "eff2_map_matrix"],
                 primary="efficiency_data_2", indicator="eff2_indicator", buttons=["eff2_delete_button"]),
            dict(name="range_motor",
                 attrs=["range_motor_efficiency_map", "range_motor_eff_map_torques", "range_motor_eff_map_rpms"],
                 primary="range_motor_efficiency_map", indicator="range_motor_eff_indicator",
                 buttons=["range_motor_eff_delete_button"]),
            dict(name="range_controller",
                 attrs=["range_controller_efficiency_map", "range_controller_eff_map_torques", "range_controller_eff_map_rpms"],
                 primary="range_controller_efficiency_map", indicator="range_controller_eff_indicator",
                 buttons=["range_controller_eff_delete_button"]),
            # MTPA/MTPV saturation maps: {'id','iq','m'} dicts of ndarrays.
            dict(name="mtpa_ld_map", attrs=["mtpa_ld_map"], primary="mtpa_ld_map",
                 indicator="mtpa_ld_indicator", buttons=["mtpa_ld_delete_button"]),
            dict(name="mtpa_lq_map", attrs=["mtpa_lq_map"], primary="mtpa_lq_map",
                 indicator="mtpa_lq_indicator", buttons=["mtpa_lq_delete_button"]),
            dict(name="mtpa_psi_map", attrs=["mtpa_psi_map"], primary="mtpa_psi_map",
                 indicator="mtpa_psi_indicator", buttons=["mtpa_psi_delete_button"]),
            # Motor BOM: nested plain-dict trees (JSON-safe as-is). A is the
            # editable baseline; B is the optional compare variant.
            dict(name="bom_tree", attrs=["bom_tree"], primary="bom_tree",
                 indicator="bom_indicator",
                 buttons=["bom_export_button", "bom_delete_button"]),
            dict(name="bom_tree_b", attrs=["bom_tree_b"], primary="bom_tree_b",
                 indicator="bom_b_indicator",
                 buttons=["bom_b_delete_button"]),
        ]

    def _set_indicator(self, indicator_name, present, button_names=()):
        ind = getattr(self, indicator_name, None)
        if ind is not None:
            try:
                ind.configure(text="✅" if present else "❌",
                              text_color=COLORS["success"] if present else COLORS["warning"])
            except Exception:
                pass
        for b in button_names:
            w = getattr(self, b, None)
            if w is not None:
                try:
                    w.configure(state="normal" if present else "disabled")
                except Exception:
                    pass

    def _collect_scenario_data(self):
        """Snapshot every scenario widget plus loaded datasets into a dict.
        Shared by Save Scenario and the session autosave."""
        data = {}
        for name, w, kind in self._scenario_widgets():
            try:
                if kind == "entry":
                    data[name] = w.get()
                elif kind in ("combo", "segment"):
                    data[name] = w.get()
                elif kind == "toggle":
                    data[name] = bool(w.get())
            except Exception:
                pass

        # Also persist any loaded datasets so they come back next time.
        datasets = {}
        for slot in self._dataset_slots():
            primary_val = getattr(self, slot["primary"], None)
            if not self._is_present(primary_val):
                continue
            blob = {}
            for attr in slot["attrs"]:
                blob[attr] = self._enc_obj(getattr(self, attr, None))
            datasets[slot["name"]] = blob
        if datasets:
            data["__datasets__"] = datasets
        return data

    def save_scenario(self):
        data = self._collect_scenario_data()

        path = filedialog.asksaveasfilename(
            title="Save scenario",
            defaultextension=".json",
            initialfile="vmi_scenario.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.set_status(f"Scenario saved ({len(data)} fields).", "ok")
        except Exception as exc:
            self.set_status(f"Save failed: {exc}", "error")
            messagebox.showerror("Save error", str(exc))

    def load_scenario(self):
        path = filedialog.askopenfilename(
            title="Load scenario",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self.set_status(f"Load failed: {exc}", "error")
            messagebox.showerror("Load error", str(exc))
            return

        applied, restored = self._apply_scenario_data(data)
        self.set_status(
            f"Scenario loaded ({applied} fields, {restored} datasets).", "ok")

    def _apply_scenario_data(self, data):
        """Apply a scenario dict to the UI (fields + datasets + derived state).
        Shared by Load Scenario and the session restore. Returns
        (fields_applied, datasets_restored)."""
        applied = 0
        for name, w, kind in self._scenario_widgets():
            if name not in data:
                continue
            val = data[name]
            try:
                if kind == "entry":
                    w.delete(0, "end")
                    w.insert(0, str(val))
                elif kind in ("combo", "segment"):
                    w.set(val)
                elif kind == "toggle":
                    (w.select if val else w.deselect)()
                applied += 1
            except Exception:
                pass

        # Restore any saved datasets and tick their indicators automatically.
        datasets = data.get("__datasets__", {}) or {}
        restored = 0
        for slot in self._dataset_slots():
            blob = datasets.get(slot["name"])
            if blob is None:
                continue
            for attr in slot["attrs"]:
                if attr in blob:
                    try:
                        setattr(self, attr, self._dec_obj(blob[attr]))
                    except Exception:
                        pass
            present = self._is_present(getattr(self, slot["primary"], None))
            self._set_indicator(slot["indicator"], present, slot["buttons"])
            if present:
                restored += 1

        # Refresh derived UI that depends on the restored data.
        try:
            if self._is_present(getattr(self, "dataframe", None)) and hasattr(self, "update_drive_cycle_properties"):
                self.update_drive_cycle_properties()
        except Exception:
            pass
        # Lock/unlock manual motor inputs to match the restored motor curve.
        try:
            if hasattr(self, "set_motor_params_enabled"):
                self.set_motor_params_enabled(
                    not self._is_present(getattr(self, "motor_dataframe", None))
                )
        except Exception:
            pass
        if hasattr(self, "_sync_shared_efficiency_ticks"):
            try:
                self._sync_shared_efficiency_ticks()
            except Exception:
                pass

        # Re-plot the current view so everything reflects the loaded scenario.
        try:
            self.update_plot()
        except Exception:
            pass

        return applied, restored

    # ------------------------------------------------------------------ #
    #  Session autosave / restore                                         #
    # ------------------------------------------------------------------ #
    SESSION_FILE = "vmi_last_session.json"

    def autosave_session(self):
        """Silently snapshot the whole UI state to SESSION_FILE (called on
        window close, so nothing is lost between runs)."""
        try:
            data = self._collect_scenario_data()
            with open(self.SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def restore_last_session(self):
        """Restore SESSION_FILE if present. Returns True if something was
        applied. Defensive per-field, so a stale file can't break startup."""
        try:
            with open(self.SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        try:
            applied, restored = self._apply_scenario_data(data)
        except Exception:
            return False
        self.set_status(
            f"Restored last session ({applied} fields, {restored} datasets).", "ok")
        return True

    def _on_app_close(self):
        self.autosave_session()
        try:
            self.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Loss-breakdown waterfall (Range analysis view)                    #
    # ------------------------------------------------------------------ #
    def _draw_loss_waterfall(self, ax, metrics):
        """Waterfall from the energy sinks up to the net battery draw (Wh/km)."""
        terms = [
            ("Aero", metrics.get("aerodynamic_loss_per_km", 0.0)),
            ("Rolling", metrics.get("rolling_loss_per_km", 0.0)),
            ("Grade", metrics.get("grade_loss_per_km", 0.0)),
            ("Inertia", metrics.get("inertia_loss_motoring_per_km", 0.0)),
            ("Trans.", metrics.get("transmission_loss_per_km", 0.0)),
            ("Motor", metrics.get("motor_loss_per_km", 0.0)),
            ("Controller", metrics.get("controller_loss_per_km", 0.0)),
            ("Aux", metrics.get("aux_loss_total_per_km", 0.0)),
        ]
        regen = metrics.get("regen_energy_per_km", 0.0)
        net = metrics.get("net_energy_loss_per_km", sum(v for _, v in terms) - regen)

        labels = [t[0] for t in terms] + ["Regen", "Net"]
        running = 0.0
        for i, (lab, val) in enumerate(terms):
            ax.bar(i, val, bottom=running, color=COLORS["primary"], edgecolor="white")
            ax.text(i, running + val + 0.2, f"{val:.1f}", ha="center", va="bottom", fontsize=9)
            running += val
        # Regen pulls the total back down.
        ax.bar(len(terms), -regen, bottom=running, color=COLORS["success"], edgecolor="white")
        ax.text(len(terms), running - regen - 0.6, f"-{regen:.1f}", ha="center", va="top", fontsize=9)
        # Net result bar from zero.
        ax.bar(len(terms) + 1, net, color=COLORS["warning"], edgecolor="white")
        ax.text(len(terms) + 1, net + 0.2, f"{net:.1f}", ha="center", va="bottom", fontsize=9, weight="bold")

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=10)
        ax.set_ylabel("Energy (Wh/km)", fontsize=12, weight="bold")
        ax.set_title("Energy Loss Breakdown (per km)", fontsize=15, weight="bold")
        ax.grid(True, axis="y", linestyle="--", alpha=0.5)


# Font family pulled once (avoids importing FONTS name clashes in the methods).
COLORS_FONT = "Segoe UI"

_REPORT_SECTION_TEMPLATE = """<div class="card" id="{anchor}">
 <h2>{name}</h2>
 <img src="data:image/png;base64,{b64}" alt="{name} figure">
 {interp}
 <div class="summary">{summary}</div>
</div>"""

_MULTI_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Vehicle - Motor Integration Report</title>
<style>
 body{{font-family:'Segoe UI',Arial,sans-serif;margin:0;background:#eef1f7;color:#0f172a}}
 header{{background:{dark};color:#fff;padding:22px 32px}}
 header h1{{margin:0;font-size:22px}} header p{{margin:4px 0 0;color:#a5b4fc;font-size:13px}}
 main{{max-width:980px;margin:24px auto;padding:0 24px}}
 nav.toc{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px 20px;margin-bottom:20px}}
 nav.toc h2{{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:{primary}}}
 nav.toc ul{{margin:0;padding-left:20px}} nav.toc a{{color:{primary};text-decoration:none}}
 nav.toc a:hover{{text-decoration:underline}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:20px;margin-bottom:20px;scroll-margin-top:16px}}
 .card h2{{margin:0 0 12px;font-size:16px;color:{primary}}}
 img{{max-width:100%;border-radius:10px;border:1px solid #e2e8f0}}
 table{{width:100%;border-collapse:collapse;font-size:14px;margin-top:10px}}
 td,th{{padding:7px 10px;border-bottom:1px solid #eef1f7;text-align:left}} td:first-child{{color:#475569}}
 .summary{{font-size:14px;line-height:1.6;margin-top:12px}}
 .obs{{margin-top:12px;padding:12px 16px;background:#f8fafc;border-left:3px solid {primary};border-radius:0 10px 10px 0}}
 .obs ul{{margin:6px 0 0;padding-left:20px}} .obs li{{margin:3px 0}}
 .interp{{margin-top:12px;padding:12px 16px;background:#fbfcfe;border-left:3px solid #64748b;border-radius:0 10px 10px 0;font-size:14px;line-height:1.6}}
 .interp .ihead{{margin:0 0 6px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#64748b}}
 .interp p{{margin:6px 0}} .interp ul{{margin:4px 0 6px;padding-left:20px}} .interp li{{margin:3px 0}}
 .assume ul{{margin:6px 0 0;padding-left:20px}} .assume li{{margin:4px 0;line-height:1.5}}
 footer{{text-align:center;color:#94a3b8;font-size:12px;padding:18px}}
</style></head><body>
<header><h1>Vehicle &harr; Motor Integration Report</h1><p>Generated {stamp}</p></header>
<main>
 <div class="card" id="inputs"><h2>1 &middot; Parameters — Vehicle &amp; Motor Inputs</h2>{inputs}</div>
 <div class="card assume" id="assumptions"><h2>2 &middot; Model Assumptions</h2>{assumptions}</div>
 <nav class="toc"><h2>Contents</h2><ul>{toc}</ul></nav>
 {sections}
</main>
<footer>Produced by the Vehicle-Motor Integration Suite</footer>
</body></html>"""
