"""Auto-generated module (method bodies copied verbatim from the original app)."""
import json
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import numpy as np
import pandas as pd
import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from PIL import Image
from scipy.interpolate import RegularGridInterpolator, UnivariateSpline
from scipy.ndimage import gaussian_filter

from .theme import COLORS, FONTS
from .physics import calculate_crr_cd_a, df, g
from .calc_ext import (battery_power_cap_w, cap_torque_to_power,
                       cap_torque_to_power_via_eff, effective_mass,
                       smooth_efficiency_matrix)
from .applog import logger



class HelpersMixin:

    # ----------------------------------------------------------------- #
    #  Modernized UI helpers (visual only -- no inputs/logic changed)   #
    # ----------------------------------------------------------------- #
    def create_section(self, parent, title, bg="#f1f5f9"):
        """Modern card-style section. Returns the content frame; the first
        child is always a clickable header (preserves winfo_children()[0] use)
        that collapses/expands the rest of the section's content."""
        section_frame = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        section_frame.pack(fill="x", pady=(0, 14), padx=14)

        title_upper = str(title).upper()
        header = ctk.CTkButton(
            section_frame,
            text="  ▼  " + title_upper,
            font=(FONTS["family_semibold"], 12, "bold"),
            text_color=COLORS["primary"],
            fg_color="transparent",
            hover_color=COLORS["section_bg"],
            anchor="w",
            corner_radius=10,
            height=30,
            command=lambda: self.toggle_section(section_frame),
        )
        header.pack(fill="x", padx=10, pady=(10, 6))

        # State used by toggle_section().
        section_frame._vmi_title = title_upper
        section_frame._vmi_header = header
        section_frame._vmi_collapsed = False
        section_frame._vmi_saved = []
        return section_frame

    def toggle_section(self, section_frame):
        """Collapse or expand a section built by create_section().

        Hides/shows every child except the header, remembering each child's
        pack options so expanding restores the exact layout. Independent per
        section, so collapsing one reflows the others without disturbing them.
        """
        header = getattr(section_frame, "_vmi_header", None)
        title = getattr(section_frame, "_vmi_title", "")
        body = [c for c in section_frame.winfo_children() if c is not header]

        if not getattr(section_frame, "_vmi_collapsed", False):
            # winfo_manager()=="pack" (not ismapped) so this works before the
            # window is rendered and for sections that are currently hidden.
            saved = []
            for c in body:
                try:
                    if c.winfo_manager() == "pack":
                        saved.append((c, c.pack_info()))
                except Exception:
                    pass
            for c, _ in saved:
                c.pack_forget()
            section_frame._vmi_saved = saved
            section_frame._vmi_collapsed = True
            if header is not None:
                header.configure(text="  ▶  " + title)
        else:
            for c, info in getattr(section_frame, "_vmi_saved", []):
                opts = dict(info)
                opts.pop("in", None)  # 'in' is the parent; re-packing keeps it
                try:
                    c.pack(**opts)
                except Exception:
                    try:
                        c.pack(fill="x", padx=16, pady=5)
                    except Exception:
                        pass
            section_frame._vmi_saved = []
            section_frame._vmi_collapsed = False
            if header is not None:
                header.configure(text="  ▼  " + title)

        self._refresh_scrollregion()

    def collapse_all_sections(self):
        """Collapse every section (used once at startup for a compact panel)."""
        for frame in getattr(self, "sections", {}).values():
            if not getattr(frame, "_vmi_collapsed", False):
                try:
                    self.toggle_section(frame)
                except Exception:
                    pass

    def reapply_collapsed_states(self):
        """Re-hide content that show_sections_for_analysis may have re-packed
        into sections the user has collapsed, so collapse survives analysis
        switches. Newly packed children are merged into the saved set so a later
        expand still restores them."""
        for frame in getattr(self, "sections", {}).values():
            if not getattr(frame, "_vmi_collapsed", False):
                continue
            header = getattr(frame, "_vmi_header", None)
            newly = []
            for c in frame.winfo_children():
                if c is header:
                    continue
                try:
                    if c.winfo_manager() == "pack":
                        newly.append((c, c.pack_info()))
                except Exception:
                    pass
            if not newly:
                continue
            for c, _ in newly:
                c.pack_forget()
            saved = getattr(frame, "_vmi_saved", [])
            known = {id(w) for w, _ in saved}
            for c, info in newly:
                if id(c) not in known:
                    saved.append((c, info))
            frame._vmi_saved = saved

    def _refresh_scrollregion(self):
        """Recompute the input panel's scroll region after a layout change."""
        canvas = getattr(self, "input_scroll_canvas", None)
        if canvas is None:
            return
        try:
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass

    def create_header(self):
        """Sleek modern app bar. Robust to missing brand images."""
        header_frame = ctk.CTkFrame(self, fg_color=COLORS["header_bg"], corner_radius=0, height=68)
        header_frame.pack(fill="x", pady=(0, 14))
        header_frame.pack_propagate(False)

        # Optional left brand image (falls back to a text mark)
        try:
            left_logo_image = ctk.CTkImage(light_image=Image.open("tvs_logo.webp"), size=(40, 40))
            ctk.CTkLabel(header_frame, image=left_logo_image, text="").pack(side="left", padx=(20, 14), pady=12)
        except Exception:
            ctk.CTkLabel(
                header_frame, text="\u26A1", font=(FONTS["family"], 26),
                text_color=COLORS["on_header"],
            ).pack(side="left", padx=(20, 10), pady=12)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", expand=True, fill="both")
        ctk.CTkLabel(
            title_box, text="Motor Design and Development Team",
            font=(FONTS["family_semibold"], 21, "bold"), text_color=COLORS["on_header"],
            anchor="w",
        ).pack(side="top", anchor="w", padx=6, pady=(12, 0))
        ctk.CTkLabel(
            title_box, text="Vehicle \u2194 Motor Integration Suite",
            font=(FONTS["family"], 12), text_color=COLORS["on_header_muted"],
            anchor="w",
        ).pack(side="top", anchor="w", padx=6, pady=(0, 10))

        try:
            right_logo_image = ctk.CTkImage(light_image=Image.open("motor.jpg"), size=(40, 40))
            ctk.CTkLabel(header_frame, image=right_logo_image, text="").pack(side="right", padx=20, pady=12)
        except Exception:
            pass

    def create_labeled_entry(self, parent, label, default_value, var_name, return_frame=False):
        """Modern label + entry row."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=5, padx=16)

        label_widget = ctk.CTkLabel(
            frame, text=label, font=(FONTS["family"], 12.5),
            text_color=COLORS["text_muted"], width=180, anchor="w",
        )
        label_widget.pack(side="left", padx=(2, 8), pady=6)

        entry = ctk.CTkEntry(
            frame, font=(FONTS["family"], 13),
            fg_color=COLORS["input_bg"], border_color=COLORS["border"],
            border_width=1, text_color=COLORS["text"], corner_radius=9, width=140, height=34,
        )
        entry.insert(0, default_value)
        entry.pack(side="right", padx=(8, 2), pady=6, fill="x", expand=True)

        setattr(self, var_name, entry)
        if return_frame:
            return frame

    def create_control_row(self, parent, label):
        """Label-left / control-right row for a non-entry widget (switch,
        segmented button, ...). Caller packs its control with side='right'
        into the returned frame. Same layout as create_labeled_entry, just
        without assuming the control is a CTkEntry."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(
            row, text=label, font=(FONTS["family"], 12),
            text_color=COLORS["text"],
        ).pack(side="left")
        return row

    def update_data_checklist(self, analysis_type=None):
        """Refresh the 'what data does this analysis need' line under the
        Analysis Type selector. Shows required/optional uploads with ✔/✖ so a
        new user immediately sees what's missing. Analyses with no uploads
        show nothing."""
        label = getattr(self, "data_checklist_label", None)
        if label is None:
            return
        analysis_type = analysis_type or getattr(self, "plot_mode", "")

        def have(attr):
            return getattr(self, attr, None) is not None

        # (name, present, required) per analysis; [] = no uploads needed.
        needs = {
            "Drive Cycle": [
                ("Drive cycle", have("dataframe"), True),
                ("Motor data", have("motor_dataframe"), False),
            ],
            "Drive Cycle Efficiency": [
                ("Drive cycle", have("dataframe"), True),
                ("Motor map", have("efficiency_data_1"), True),
                ("Controller map", have("efficiency_data_2"), True),
            ],
            "Range analysis": [
                ("Drive cycle", have("dataframe"), True),
                ("Motor map", have("efficiency_data_1"), False),
                ("Controller map", have("efficiency_data_2"), False),
            ],
            "Engine analysis": [
                ("Engine torque-RPM", have("engine_dataframe"), True),
                ("Gear efficiency", bool(getattr(self, "engine_efficiency_curves", None)), False),
            ],
            "MTPA / MTPV (PMSM)": [
                ("Ld map", have("mtpa_ld_map"), False),
                ("Lq map", have("mtpa_lq_map"), False),
                ("ψ_PM map", have("mtpa_psi_map"), False),
            ],
            "Motor BOM (Cost & Weight)": [
                ("BOM (template / Excel / editor)", have("bom_tree"), True),
                ("BOM B (for Compare A vs B)", have("bom_tree_b"), False),
            ],
        }.get(analysis_type, [])

        if not needs:
            label.configure(text="")
            return
        parts = []
        missing_required = False
        for name, present, required in needs:
            mark = "✔" if present else "✖"
            suffix = "" if required else " (optional)"
            parts.append(f"{mark} {name}{suffix}")
            if required and not present:
                missing_required = True
        label.configure(
            text="Data:  " + "   ".join(parts),
            text_color=COLORS["warning"] if missing_required else COLORS["text_muted"],
        )

    def setup_plot_style(self):
        """Apply a clean modern matplotlib look (cosmetic only)."""
        try:
            plt.style.use("seaborn-v0_8-whitegrid")
        except Exception:
            pass
        self.figure.patch.set_facecolor(COLORS["plot_bg"])
        self.ax.set_facecolor(COLORS["plot_axes_bg"])
        for spine in self.ax.spines.values():
            spine.set_color(COLORS["border"])
        self.ax.tick_params(colors=COLORS["text_muted"])

    def on_plot_mode_change(self, *args):
        self.plot_graph()

    def _sync_plot_part_lock(self):
        """Force is a wheel quantity, so 'At Motor' is meaningless for it: lock the
        Plotting Part selector to 'At Wheel' (greyed out) whenever Output = Force,
        and restore it to a normal, editable state for Torque."""
        try:
            if self.output_combo.get() == "Force":
                self.plot_part_combo.set("At Wheel")
                self.plot_part_combo.configure(state="disabled")
            else:
                self.plot_part_combo.configure(state="normal")
        except Exception:
            pass


    def clear_axes(self):
        """Clear the whole figure and recreate a single primary axis."""
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self.safe_remove_colorbar('range_eff_colorbar')
        self._remove_engine_secondary_axis()
        self.figure.clf()
        # figure.clf() keeps the previous patch facecolor, so reset to the light
        # baseline here; dark mode (if active) re-darkens via the wrapped draw.
        self.figure.patch.set_facecolor(COLORS["plot_bg"])
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(COLORS["plot_axes_bg"])


    def create_menu_bar(self):
        """Creates a menu bar with File, Help, Load, and Download options."""
        menu_bar = tk.Menu(self)

        # File Menu
        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Open", command=self.open_file)
        file_menu.add_command(label="Save", command=self.save_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)

        load_menu = tk.Menu(menu_bar, tearoff=0)
        load_menu.add_command(label="Load Excel", command=self.load_excel)

        # Download Menu
        download_menu = tk.Menu(menu_bar, tearoff=0)
        download_menu.add_command(label="Download Torque Plot", command=self.download_torque_plot)
        download_menu.add_command(label="Download Force Plot", command=self.download_force_plot)
        download_menu.add_command(label="Download Velocity-Time Plot", command=self.download_velocity_time_plot)
        download_menu.add_command(label="Download Drive Cycle Plot", command=self.download_drive_cycle_plot)
        download_menu.add_command(label="Download Torque-Speed Plot", command=self.download_torque_speed_plot)
        download_menu.add_command(label="Download Torque Speed Data Excel", command=self.download_torque_speed_data_excel)
        # Help Menu
        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)

        # Export Menu (new) -- generic figure/data/report/scenario actions.
        export_menu = tk.Menu(menu_bar, tearoff=0)
        export_menu.add_command(label="Export Figure (PNG / SVG / PDF)...", command=self.export_current_figure)
        export_menu.add_command(label="Export Data (CSV / XLSX)...", command=self.export_current_data)
        export_menu.add_separator()
        export_menu.add_command(label="Generate HTML Report...", command=self.generate_report)

        # Tools Menu (new) -- scenario save/load + plot theme.
        tools_menu = tk.Menu(menu_bar, tearoff=0)
        tools_menu.add_command(label="Save Scenario...", command=self.save_scenario)
        tools_menu.add_command(label="Load Scenario...", command=self.load_scenario)
        tools_menu.add_separator()
        tools_menu.add_command(label="Toggle Dark Plots", command=self.toggle_theme)
        tools_menu.add_command(label="Re-plot (Enter)", command=self._safe_plot)

        menu_bar.add_cascade(label="File", menu=file_menu)
        menu_bar.add_cascade(label="Export", menu=export_menu)
        menu_bar.add_cascade(label="Tools", menu=tools_menu)
        menu_bar.add_cascade(label="Help", menu=help_menu)
        # menu_bar.add_cascade(label="Load Files", menu=load_menu)
        menu_bar.add_cascade(label="Download", menu=download_menu)

        # Attach menu to window
        self.config(menu=menu_bar)



    def on_heatmap_toggle(self):
    # Only update if the plotting section is visible
        if self.plot_mode == "Drive Cycle":
            self.plot_torque_speed_drive_cycle()

    def on_show_positive_torque_toggle(self):
        if self.plot_mode == "Drive Cycle":
            self.plot_torque_speed_drive_cycle()


    def open_file(self):
        """Handles file opening (Placeholder)."""


    def save_file(self):
        """Handles file saving (Placeholder)."""


    def show_about(self):
        """Displays an About message."""
        messagebox.showinfo("About", "Torque-Speed Analysis Tool\nVersion 1.0")
    

    def _speed_unit_from_label(self, label):
        if "(" in label and ")" in label:
            return label.split("(")[-1].split(")")[0]
        return label.split()[-1]


    def show_placeholder_message(self, message="Please upload the required Files to continue."):
        """Displays a centered placeholder message on the plot area."""
        self.ax.clear()
        self.ax.text(
            0.5, 0.5, message,
            fontsize=18,
            color=COLORS['warning'],
            ha='center',
            va='center',
            transform=self.ax.transAxes
        )
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw()


    def _remove_engine_secondary_axis(self):
        secondary_ax = getattr(self, "engine_secondary_ax", None)
        if secondary_ax is not None:
            try:
                secondary_ax.remove()
            except Exception:
                pass
            self.engine_secondary_ax = None


    def safe_remove_colorbar(self, colorbar_attr='efficiency_colorbar'):
        colorbar = getattr(self, colorbar_attr, None)
        if colorbar is not None:
            try:
                colorbar.remove()
            except Exception:
                pass
            setattr(self, colorbar_attr, None)
            


    def on_xlim_force_manual_edit(self, event):
        self.xlim_force_manual = bool(self.xlim_force.get().strip())


    def on_ylim_force_manual_edit(self, event):
        self.ylim_force_manual = bool(self.ylim_force.get().strip())

    def on_xlim_manual_edit(self, event):
        self.xlim_manual = bool(self.xlim.get().strip())

    def on_xlim_rpm_vehicle_manual_edit(self, event):
        self.xlim_rpm_vehicle_manual = bool(self.xlim_rpm_vehicle.get().strip())


    def on_xlim_rpm_motor_manual_edit(self, event):
        self.xlim_rpm_motor_manual = bool(self.xlim_rpm_motor.get().strip())

    def on_ylim_manual_edit(self, event):
        self.ylim_manual = bool(self.ylim.get().strip())

    def on_ylim_wheel_manual_edit(self, event):
        self.ylim_wheel_manual = bool(self.ylim_wheel.get().strip())

    def on_ylim_wheel_force_manual_edit(self, event):
        self.ylim_wheel_force_manual = bool(self.ylim_wheel_force.get().strip())

    def on_crr_manual_edit(self, event):
        self.crr_manual = True
        self.crr_manual = bool(self.crr.get().strip())


    def on_cda_manual_edit(self, event):
        self.cda_manual = True
        self.cda_manual = bool(self.cd_a.get().strip())


    def on_gear_efficiency_focus_out(self, event=None):
        value = self.gear_efficiency.get().strip()
        if not value:
            self.gear_efficiency.delete(0, "end")
            self.gear_efficiency.insert(0, "1")
            return
        try:
            value_num = float(value)
            if not 0 <= value_num <= 1:
                raise ValueError
        except Exception:
            messagebox.showerror("Invalid Input", "Gear efficiency must be between 0 and 1.")
            self.gear_efficiency.delete(0, "end")
            self.gear_efficiency.insert(0, "1")


    def get_gear_efficiency_value(self):
        try:
            value_num = float(self.gear_efficiency.get().strip())
        except Exception:
            value_num = 1.0
        if not 0 <= value_num <= 1:
            value_num = 1.0
        return value_num


    def get_battery_power_cap_w(self):
        """Mechanical shaft-power cap (W) from the Battery DC Limit fields,
        or None when either field is blank/invalid -> no cap (original
        behaviour). cap = Vdc * Idc * battery-to-shaft efficiency."""
        try:
            v_raw = self.batt_voltage.get().strip()
            i_raw = self.batt_current_limit.get().strip()
        except Exception:
            return None
        if not v_raw or not i_raw:
            return None
        try:
            eta_raw = self.batt_to_shaft_eff.get().strip()
            eta = float(eta_raw) if eta_raw else 1.0
        except Exception:
            eta = 1.0
        try:
            return battery_power_cap_w(float(v_raw), float(i_raw), eta)
        except Exception:
            return None


    def get_battery_dc_power_w(self):
        """Raw battery DC power Vdc * Idc in W (NO efficiency applied), or
        None when either Battery DC Limit field is blank/invalid."""
        try:
            v_raw = self.batt_voltage.get().strip()
            i_raw = self.batt_current_limit.get().strip()
        except Exception:
            return None
        if not v_raw or not i_raw:
            return None
        try:
            return battery_power_cap_w(float(v_raw), float(i_raw), 1.0)
        except Exception:
            return None


    def _battery_eta_fn(self):
        """Combined motor x controller efficiency lookup eta(torque_nm,
        motor_rpm) built from the shared uploaded maps (slot 1 = Motor,
        slot 2 = Controller; a missing slot falls back to its constant-
        efficiency entry). Returns None when NEITHER map is loaded, in which
        case the battery cap keeps the original constant-eta behaviour."""
        m_mat = getattr(self, "eff1_map_matrix", None)
        c_mat = getattr(self, "eff2_map_matrix", None)
        if m_mat is None and c_mat is None:
            return None
        interp = getattr(self, "_interpolate_efficiency_or_constant", None)
        if interp is None:
            return None
        m_tq = getattr(self, "eff1_map_torques", None)
        m_rpm = getattr(self, "eff1_map_rpms", None)
        c_tq = getattr(self, "eff2_map_torques", None)
        c_rpm = getattr(self, "eff2_map_rpms", None)
        gec = getattr(self, "_get_eff_constant", None)
        m_const = (gec(self.motor_eff_const, 0.90)
                   if callable(gec) and getattr(self, "motor_eff_const", None) is not None
                   else 0.90)
        c_const = (gec(self.controller_eff_const, 0.95)
                   if callable(gec) and getattr(self, "controller_eff_const", None) is not None
                   else 0.95)

        # Optional smoothing for the battery-limit lookup ONLY (off by
        # default): fills NaN cells and blurs the map before it's used to
        # solve the battery cap, so a coarse or NaN-holed uploaded map
        # doesn't produce a hard efficiency cliff right at its coverage edge
        # -- which is usually exactly where the battery limit binds hardest,
        # showing up as an abrupt jump in the capped torque-speed curve. The
        # map's own contour views are untouched (they read m_mat/c_mat
        # directly, not through this function).
        switch = getattr(self, "battery_eff_smoothing_switch", None)
        if switch is not None:
            try:
                if switch.get():
                    m_mat = smooth_efficiency_matrix(m_mat) if m_mat is not None else None
                    c_mat = smooth_efficiency_matrix(c_mat) if c_mat is not None else None
            except Exception:
                pass

        def eta(torque_nm, motor_rpm):
            eta_m = interp(torque_nm, motor_rpm, m_mat, m_tq, m_rpm, m_const)
            eta_c = interp(torque_nm, motor_rpm, c_mat, c_tq, c_rpm, c_const)
            return np.clip(np.asarray(eta_m, dtype=float)
                           * np.asarray(eta_c, dtype=float), 0.01, 1.0)

        return eta


    def cap_torque_to_battery(self, torque_nm, motor_rpm):
        """THE battery-DC clip for a motor capability curve.

        With the Motor/Controller efficiency maps loaded, the battery limit
        is evaluated AFTER them: shaft torque is clipped so
        |T|*omega <= Vdc*Idc*eta_m(T,w)*eta_c(T,w), solved per speed point
        (see calc_ext.cap_torque_to_power_via_eff). With no maps loaded this
        reduces to the original constant chain efficiency from the
        Battery-to-Shaft Efficiency entry -- identical numbers to before.
        Blank battery fields -> no cap either way."""
        motor_rpm = np.asarray(motor_rpm, dtype=float)
        omega = np.maximum(np.abs(motor_rpm) * 2.0 * np.pi / 60.0, 1e-9)
        p_dc = self.get_battery_dc_power_w()
        eta_fn = self._battery_eta_fn() if p_dc is not None else None
        if eta_fn is None:
            return cap_torque_to_power(torque_nm, omega, self.get_battery_power_cap_w())
        return cap_torque_to_power_via_eff(
            torque_nm, omega, p_dc,
            lambda t, om: eta_fn(t, np.asarray(om, dtype=float) * 60.0 / (2.0 * np.pi)))


    def get_effective_inertial_mass(self, mass_kg, wheel_radius_m=None):
        """mass + J_wheel/r^2 for the inertial (m*a) terms only. A blank or
        0 Wheel Inertia field returns the plain mass unchanged. When no
        radius is passed, the Wheel Radius entry is used."""
        try:
            j_raw = self.wheel_inertia.get().strip()
            j = float(j_raw) if j_raw else 0.0
        except Exception:
            j = 0.0
        if j <= 0:
            return float(mass_kg)
        if wheel_radius_m is None:
            try:
                wheel_radius_m = float(self.wheel_radius.get())
            except Exception:
                return float(mass_kg)
        return effective_mass(mass_kg, j, wheel_radius_m)


    def gradient_unit_is_degrees(self):
        """True when the Gradient Unit selector is set to degrees. Missing
        selector (e.g. a mixin-only test host) -> percent, the original unit."""
        combo = getattr(self, "gradient_unit_combo", None)
        if combo is None:
            return False
        try:
            return "Degree" in str(combo.get())
        except Exception:
            return False

    def convert_gradient_to_pct(self, value):
        """One gradient input value -> slope percent, honoring the unit selector."""
        from .units import gradient_deg_to_pct
        value = float(value)
        return gradient_deg_to_pct(value) if self.gradient_unit_is_degrees() else value

    def get_gradients_pct(self):
        """Parse the Gradients entry into a list of slope-percent values,
        converting from degrees when that unit is selected. Raises ValueError
        on a malformed/empty list so callers keep their own error handling."""
        parts = [p.strip() for p in self.gradients.get().split(",") if p.strip()]
        if not parts:
            raise ValueError("Gradients list is empty.")
        return [self.convert_gradient_to_pct(float(p)) for p in parts]

    def fmt_gradient(self, pct):
        """Format a slope-percent value for plot labels in the unit the user
        typed it in: '7%' or, with degrees selected, '4°'."""
        from .units import gradient_pct_to_deg
        if self.gradient_unit_is_degrees():
            return f"{gradient_pct_to_deg(pct):g}°"
        return f"{float(pct):g}%"

    @staticmethod
    def _split_thermal_point_chunks(raw):
        """Split the Thermal Load Points entry into one chunk per point.

        Two accepted formats, auto-detected:
          * ';'-separated triples, e.g. '0,60,300; 7,30,600' -- each chunk is
            one point (lets a point's own numbers be visually grouped).
          * a FLAT comma list with no ';', e.g. '0,60,300,7,30,600' -- the
            same style as the Gradients field, extended to multiple points by
            grouping every 3 numbers into one point. This is what most users
            reach for first (Gradients is a flat comma list), so without this
            a list like that used to silently keep only the first point and
            drop everything after it.
        A trailing 1-2 leftover numbers (an incomplete final point) is still
        emitted as its own chunk so the parse loop can report it as an error
        rather than silently dropping it.
        """
        if ";" in raw:
            return [c for c in raw.split(";") if c.strip()]
        nums = [p.strip() for p in raw.split(",") if p.strip()]
        return [",".join(nums[i:i + 3]) for i in range(0, len(nums), 3)]

    def compute_thermal_load_points(self):
        """Parse the Thermal Load Points entry into motor operating points.

        Entry format: 'gradient,speed,duration' triples, either separated by
        ';' (e.g. '0,60,300; 7,30,600') or as one flat comma list grouped in
        3s (e.g. '0,60,300,7,30,600') -- see _split_thermal_point_chunks.
        Speed is km/h or motor RPM per the Point Speed Unit selector;
        gradient honors the Gradient Unit selector. Each steady condition is
        converted through the same resistive-force model the torque plot
        uses (rho = 1.225):

            F = m*g*Crr*cos(theta) + 0.5*rho*CdA*v^2 + m*g*sin(theta)
            wheel torque = F*r;  motor torque = F*r / (GR*eta_gear)

        Returns a list of dicts {grad_pct, v_kmh, duration_s, motor_rpm,
        motor_torque, wheel_torque} and refreshes thermal_results_label with
        the computed values. Returns [] (and clears the label) when the
        overlay is off, the entry is blank, or anything fails to parse --
        the overlay never blocks the underlying plot.
        """
        switch = getattr(self, "thermal_overlay_switch", None)
        entry = getattr(self, "thermal_points", None)
        label = getattr(self, "thermal_results_label", None)

        def _set_label(text):
            if label is not None:
                try:
                    label.configure(text=text)
                except Exception:
                    pass

        if switch is None or entry is None:
            return []
        try:
            if not switch.get():
                _set_label("")
                return []
            raw = entry.get().strip()
        except Exception:
            return []
        if not raw:
            _set_label("No points entered, e.g. 0,60,300,7,30,600 (grad,speed,time per point)")
            return []

        try:
            m_ref = float(self.m_ref.get())
            rear_load_ratio = float(self.rear_load_ratio.get())
            ambient_temp = float(self.ambient_temp.get())
            ambient_pressure = float(self.ambient_pressure.get())
            crr = float(self.crr.get()) if self.crr.get().strip() else None
            cd_a = float(self.cd_a.get()) if self.cd_a.get().strip() else None
            params = calculate_crr_cd_a(
                m_ref, rear_load_ratio, ambient_temp, ambient_pressure,
                crr=crr if self.crr_manual else None,
                cd_a=cd_a if self.cda_manual else None,
            )
            wheel_radius = float(self.wheel_radius.get())
            gear_ratio = float(self.gear_ratio.get())
            gear_eff = self.get_gear_efficiency_value()
        except Exception:
            _set_label("Thermal points: fill in vehicle/motor inputs first.")
            return []

        rpm_unit = False
        combo = getattr(self, "thermal_speed_unit_combo", None)
        if combo is not None:
            try:
                rpm_unit = "RPM" in str(combo.get())
            except Exception:
                rpm_unit = False

        points = []
        lines = []
        drive_scale = max(gear_ratio * gear_eff, 1e-9)
        for i, chunk in enumerate(self._split_thermal_point_chunks(raw), 1):
            try:
                parts = [p.strip() for p in chunk.split(",") if p.strip()]
                if len(parts) < 2:
                    raise ValueError(chunk)
                grad_pct = self.convert_gradient_to_pct(float(parts[0]))
                speed_val = float(parts[1])
                duration_s = float(parts[2]) if len(parts) > 2 else 0.0
                if rpm_unit:
                    motor_rpm = speed_val
                    v_mps = (motor_rpm / max(abs(gear_ratio), 1e-9)) \
                        * 2.0 * np.pi * wheel_radius / 60.0
                else:
                    v_mps = speed_val / 3.6
                    motor_rpm = (v_mps / max(wheel_radius, 1e-9)) \
                        * 60.0 / (2.0 * np.pi) * gear_ratio
                theta = np.arctan(grad_pct / 100.0)
                force_n = (
                    params['m_i'] * g * params['Crr'] * np.cos(theta)
                    + 0.5 * 1.225 * params['CdA'] * v_mps ** 2
                    + params['m_i'] * g * np.sin(theta)
                )
                wheel_torque = force_n * wheel_radius
                motor_torque = wheel_torque / drive_scale
                points.append(dict(
                    grad_pct=float(grad_pct), v_kmh=float(v_mps * 3.6),
                    duration_s=float(duration_s),
                    motor_rpm=float(motor_rpm),
                    motor_torque=float(motor_torque),
                    wheel_torque=float(wheel_torque),
                ))
                lines.append(
                    f"{i}) {self.fmt_gradient(grad_pct)} @ {v_mps * 3.6:.0f} km/h, "
                    f"{duration_s:g}s -> {motor_torque:.1f} Nm @ {motor_rpm:.0f} RPM"
                )
            except Exception:
                lines.append(f"{i}) could not parse '{chunk.strip()}'")
        _set_label("\n".join(lines))
        return points

    def on_mref_change(self, event):
        if not self.crr_manual or not self.cda_manual:
            self.plot_graph()

    @staticmethod
    def tyre_static_radius_m(spec):
        """Static (unloaded) tyre radius in metres from a size designation.

        Metric 'W/A-R'  (e.g. 90/90-12):  r = R*25.4/2 + W*(A/100)   [mm]
            W = section width (mm), A = aspect ratio (%), R = rim (in)
        Inch  'W.WW-R'  (e.g. 3.00-10):   r = R*25.4/2 + W*25.4      [mm]
            older bias-ply sizes; aspect ratio ~100% of the inch width.
        """
        s = str(spec).strip()
        body, rim_in = s.rsplit("-", 1)
        rim_mm = float(rim_in) * 25.4
        if "/" in body:
            width_mm, aspect_pct = body.split("/")
            section_mm = float(width_mm) * float(aspect_pct) / 100.0
        else:
            section_mm = float(body) * 25.4
        return round((rim_mm / 2.0 + section_mm) / 1000.0, 4)

    def on_dynamic_radius_factor_change(self, event=None):
        """Re-apply the selected tyre's radius when the Dynamic Radius Factor
        is edited, so the factor takes effect without re-picking the tyre.
        Skipped when no tyre is selected or the user typed a radius manually."""
        try:
            spec = self.tyre_spec_combo.get()
        except Exception:
            return
        if spec not in self.tyre_radius_map or self.wheel_radius_user_modified:
            return
        static_radius = self.tyre_radius_map[spec]
        factor = self.get_dynamic_radius_factor()
        dynamic_radius = round(static_radius * factor, 4)
        self.wheel_radius_entry.delete(0, "end")
        self.wheel_radius_entry.insert(0, str(dynamic_radius))
        try:
            self.set_status(
                f"Tyre {spec}: static r={static_radius:.4f} m "
                f"x {factor:g} -> dynamic r={dynamic_radius:.4f} m", "ok")
        except Exception:
            pass

    def get_dynamic_radius_factor(self):
        """Multiplier applied to the static (unloaded) tyre radius to get the
        dynamic rolling radius. Falls back to 0.98 on blank/invalid input and
        clamps to a sane band so a typo can't zero out the wheel."""
        try:
            factor = float(self.dynamic_radius_factor.get().strip())
        except Exception:
            factor = 0.98
        if not (0.5 <= factor <= 1.2):
            factor = 0.98
        return factor

    def set_radius_from_tyre_spec(self, selected_value):
        if selected_value in self.tyre_radius_map:
            if self.wheel_radius_user_modified:
                if not messagebox.askyesno(
                    "Override Wheel Radius",
                    "You have manually entered a wheel radius. Selecting a tyre specification will overwrite it. Continue?"
                ):
                    # User cancelled, reset dropdown
                    self.tyre_spec_combo.set("Select Tyre Type")
                    return
            static_radius = self.tyre_radius_map[selected_value]
            factor = self.get_dynamic_radius_factor()
            # Dynamic rolling radius = factor x static (unloaded) radius.
            dynamic_radius = round(static_radius * factor, 4)
            self.wheel_radius_entry.delete(0, "end")
            self.wheel_radius_entry.insert(0, str(dynamic_radius))
            self.wheel_radius_user_modified = False
            try:
                self.set_status(
                    f"Tyre {selected_value}: static r={static_radius:.4f} m "
                    f"x {factor:g} -> dynamic r={dynamic_radius:.4f} m", "ok"
                )
            except Exception:
                pass
            # Refresh the plot so the new radius takes effect immediately.
            try:
                self.plot_graph()
            except Exception:
                pass
