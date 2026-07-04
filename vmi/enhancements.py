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

        # Wrap canvas.draw / draw_idle so dark mode applies to *every* plot.
        if hasattr(self, "canvas") and not getattr(self, "_draw_wrapped", False):
            for name in ("draw", "draw_idle"):
                orig = getattr(self.canvas, name, None)
                if orig is None:
                    continue

                def make(orig_fn):
                    def wrapped(*a, **k):
                        if getattr(self, "_dark_mode", False):
                            try:
                                theme.apply_dark_to_figure(self.figure)
                            except Exception:
                                pass
                        return orig_fn(*a, **k)
                    return wrapped
                setattr(self.canvas, name, make(orig))
            self._draw_wrapped = True

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
        rows = [
            ("Reference Mass (kg)", g("m_ref")),
            ("Rear Load Ratio", g("rear_load_ratio")),
            ("Ambient Temperature (°C)", g("ambient_temp")),
            ("Ambient Pressure (kPa)", g("ambient_pressure")),
            ("Crr", crr if crr else "(auto-estimated from mass)"),
            ("CdA (m²)", f"{cd_a} m²" if cd_a else "(auto-estimated from mass)"),
            ("Gear Ratio", g("gear_ratio")),
            ("Wheel Radius (m)", g("wheel_radius")),
            ("Peak Torque (Nm)", g("peak_torque")),
            ("Peak Power (kW)", g("peak_power")),
            ("Continuous Power (kW)", g("continuous_power")),
            ("Peak : Rated Torque Ratio", g("peak_to_rated_torque_ratio")),
            ("Gradients (%)", g("gradients")),
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
        return ""

    def _render_report_views(self, analysis):
        """Render every distinct plot this analysis contributes to the
        report. Returns a list of (subtitle, b64_png) tuples -- subtitle is
        "" for a single-view analysis, in which case the section just uses
        the analysis name with no suffix.

        Explicit per-analysis branches on purpose (not a generic loop): each
        analysis's "views" are controlled by completely different widgets
        (Output/Plotting Part combos, the heatmap switch, the map buttons,
        the Compare radio buttons, ...), so a one-size-fits-all abstraction
        would just hide what's actually being toggled. If you add a new
        analysis mode with more than one meaningful view, add a branch here;
        everything else falls through to the single generic `update_plot()`
        capture at the bottom.
        """
        self.plot_type.set(analysis)
        self.plot_mode = analysis
        self.show_sections_for_analysis(analysis)
        out = []

        def snap(subtitle=""):
            self.canvas.draw()
            self.update_idletasks()
            png = io.BytesIO()
            self.figure.savefig(png, format="png", dpi=150, bbox_inches="tight",
                                facecolor=self.figure.get_facecolor())
            png.seek(0)
            out.append((subtitle, base64.b64encode(png.read()).decode("ascii")))

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
            # each map view has to be called directly.
            have1 = getattr(self, "efficiency_data_1", None) is not None
            have2 = getattr(self, "efficiency_data_2", None) is not None
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

        else:
            # Acceleration, Parametric Study, Engine analysis: no sub-views.
            # Range analysis and MTPA/MTPV already default to an "All" /
            # multi-panel dashboard covering everything in one figure.
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
        toc = ["<li><a href='#inputs'>Vehicle &amp; Motor Inputs</a></li>"]
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
                    for i, (subtitle, b64) in enumerate(views):
                        anchor = f"section-{len(sections)}"
                        title = f"{name} — {subtitle}" if subtitle else name
                        section_summary = summary_html
                        if i == 0 and analysis_inputs_html:
                            section_summary = analysis_inputs_html + section_summary
                        sections.append(_REPORT_SECTION_TEMPLATE.format(
                            anchor=anchor, name=title, b64=b64, summary=section_summary))
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
            html = _MULTI_REPORT_TEMPLATE.format(
                stamp=stamp, inputs=inputs_html, toc="\n".join(toc), sections="\n".join(sections),
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
 footer{{text-align:center;color:#94a3b8;font-size:12px;padding:18px}}
</style></head><body>
<header><h1>Vehicle &harr; Motor Integration Report</h1><p>Generated {stamp}</p></header>
<main>
 <div class="card" id="inputs"><h2>Vehicle &amp; Motor Inputs</h2>{inputs}</div>
 <nav class="toc"><h2>Contents</h2><ul>{toc}</ul></nav>
 {sections}
</main>
<footer>Produced by the Vehicle-Motor Integration Suite</footer>
</body></html>"""
