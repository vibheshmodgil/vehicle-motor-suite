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
