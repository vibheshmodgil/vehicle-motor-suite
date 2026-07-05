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



class DispatchMixin:

    def plot_graph(self):
        """Updates the plot based on the selected toggle state"""
        self.clear_axes()

        # MTPA/MTPV is a pure d-q machine analysis -- it has its own inputs
        # and doesn't use the vehicle/Crr/CdA model, so branch before the
        # generic input parsing (same pattern as Range analysis below).
        if self.plot_mode == "MTPA / MTPV (PMSM)":
            self.plot_mtpa_mtpv()
            return

        # Mechanical Design is likewise a self-contained hand-calc analysis
        # with its own inputs (no vehicle/Crr/CdA model).
        if self.plot_mode == "Mechanical Design (Motor)":
            self.plot_mechanical_design()
            return

        # Motor BOM: pure cost/weight breakdown of the BOM tree — also no
        # vehicle model involved.
        if self.plot_mode == "Motor BOM (Cost & Weight)":
            self.plot_motor_bom()
            return

        # Retrieve user inputs. parse_float red-borders every bad field and
        # collects all messages, so the user sees everything wrong at once in
        # the status bar instead of one modal per field.
        from .validation import parse_float
        errors = []
        m_ref = parse_float(self.m_ref, "Reference Mass", errors=errors)
        rear_load_ratio = parse_float(self.rear_load_ratio, "Rear Load Ratio", errors=errors)
        ambient_temp = parse_float(self.ambient_temp, "Ambient Temperature", errors=errors)
        ambient_pressure = parse_float(self.ambient_pressure, "Ambient Pressure", errors=errors)
        crr = parse_float(self.crr, "Crr", allow_blank=True, errors=errors)
        cd_a = parse_float(self.cd_a, "CdA", allow_blank=True, errors=errors)
        gear_ratio = parse_float(self.gear_ratio, "Gear ratio", errors=errors)
        if errors:
            self.set_status("Fix inputs: " + "; ".join(errors), "error")
            self.show_placeholder_message("Invalid inputs — fields marked in red")
            self.canvas.draw()
            return
        try:
            params = calculate_crr_cd_a(
                m_ref,
                rear_load_ratio,
                ambient_temp,
                ambient_pressure,
                crr=crr if self.crr_manual else None,
                cd_a=cd_a if self.cda_manual else None,
            )
        except Exception as exc:
            # e.g. reference mass outside the lookup table without manual Crr/CdA.
            messagebox.showerror("Input Error", str(exc))
            return
        # --- Set calculated Crr and CdA in entries if not given manually ---
        if not self.crr_manual:
            self.crr.delete(0, "end")
            self.crr.insert(0, str(params['Crr']))
        if not self.cda_manual:
            self.cd_a.delete(0, "end")
            self.cd_a.insert(0, str(params['CdA']))

        # --- Tell the user whether Crr / CdA are manual or mass-estimated ---
        crr_src = "manual entry" if self.crr_manual else "estimated from mass"
        cda_src = "manual entry" if self.cda_manual else "estimated from mass"
        try:
            self.params_label.configure(
                text=(
                    f"Crr = {params['Crr']}  ({crr_src})\n"
                    f"CdA = {params['CdA']} m²  ({cda_src})"
                ),
                text_color=COLORS['text'],
                justify="left",
            )
        except Exception:
            pass

        if self.plot_mode == "Range analysis":
            self.plot_power_energy_cycle()
            return

        # self.params_label.configure(text=f"Rolling Resistance Coefficient (Crr): {params['Crr']}\nAerodynamic Drag Area (CdA): {params['CdA']} mÂ²")

        # Retrieve remaining inputs (same collect-all-errors treatment).
        try:
            gradients = [float(g.strip()) for g in self.gradients.get().split(",") if g.strip()]
            if not gradients:
                raise ValueError
        except Exception:
            errors.append("Gradients must be comma-separated numbers (e.g. 0,7,12).")
            gradients = [0.0]
        wheel_radius = parse_float(self.wheel_radius, "Wheel Radius", minimum=1e-6, errors=errors)
        peak_torque = parse_float(self.peak_torque, "Peak Torque", minimum=1e-9, errors=errors)
        peak_power = parse_float(self.peak_power, "Peak Power", minimum=1e-9, errors=errors)
        continuous_power = parse_float(self.continuous_power, "Continuous Power", errors=errors)
        if errors:
            self.set_status("Fix inputs: " + "; ".join(errors), "error")
            self.show_placeholder_message("Invalid inputs — fields marked in red")
            self.canvas.draw()
            return
        try:
            peak_to_rated_torque_ratio = float(self.peak_to_rated_torque_ratio.get())
            if peak_to_rated_torque_ratio <= 0:
                raise ValueError
        except Exception:
            peak_to_rated_torque_ratio = 2.0
            self.peak_to_rated_torque_ratio.delete(0, "end")
            self.peak_to_rated_torque_ratio.insert(0, "2")
        speed_unit = self.speed_unit_combo.get()
        plot_part = self.plot_part_combo.get()
        speed_unit = self.speed_unit_combo.get()
        wheel_radius = float(self.wheel_radius.get())
        gear_ratio = float(self.gear_ratio.get())

        # --- X-axis Limit (km/h, vehicle) ---
        if not hasattr(self, "xlim_manual"):
            self.xlim_manual = False
        if not self.xlim_manual:
            self.xlim.delete(0, "end")
            self.xlim.insert(0, "0,80")  # or your preferred default

        # --- X-axis Limit (RPM, vehicle) ---
        if not hasattr(self, "xlim_rpm_vehicle_manual"):
            self.xlim_rpm_vehicle_manual = False
        if not self.xlim_rpm_vehicle_manual:
            # Calculate from xlim (km/h)
            xlim_str = self.xlim.get().strip()
            if xlim_str:
                x_limits_kmh = [float(x.strip()) for x in xlim_str.split(",")]
                x_limits_rpm = []
                for v_kmh in x_limits_kmh:
                    v_mps = v_kmh * 1000 / 3600
                    rpm = (v_mps / wheel_radius) * 60 / (2 * np.pi)
                    x_limits_rpm.append(rpm)
                self.xlim_rpm_vehicle.delete(0, "end")
                self.xlim_rpm_vehicle.insert(0, ",".join(f"{v:.2f}" for v in x_limits_rpm))

        # --- X-axis Limit (RPM, motor) ---
        if not hasattr(self, "xlim_rpm_motor_manual"):
            self.xlim_rpm_motor_manual = False
        if not self.xlim_rpm_motor_manual:
            # Calculate from xlim_rpm_vehicle * gear_ratio
            xlim_rpm_vehicle_str = self.xlim_rpm_vehicle.get().strip()
            if xlim_rpm_vehicle_str:
                x_limits_vehicle = [float(x.strip()) for x in xlim_rpm_vehicle_str.split(",")]
                x_limits_motor = [v * gear_ratio for v in x_limits_vehicle]
                self.xlim_rpm_motor.delete(0, "end")
                self.xlim_rpm_motor.insert(0, ",".join(f"{v:.2f}" for v in x_limits_motor))

        # Now call get_x_limits as usual
        x_limits = self.get_x_limits(speed_unit, wheel_radius, gear_ratio)
        # x_limits = self.get_x_limits(speed_unit, wheel_radius, gear_ratio)
        if x_limits is None:
            # Fallback when the limit fields can't be parsed: default km/h span.
            x_limits = [0.0, 80.0]
        # y_limits = [float(y.strip()) for y in self.ylim.get().split(",")]

        plot_part = self.plot_part_combo.get()
        if not self.ylim_manual:
            try:
                peak_torque = float(self.peak_torque.get())
            except Exception:
                peak_torque = 400  # fallback
            self.ylim.delete(0, "end")
            self.ylim.insert(0, f"0,{peak_torque + 5}")
        y_limits = self.get_y_limits(plot_part, gear_ratio)
        if y_limits is None:
            # Fallback when the limit fields can't be parsed: 0..peak+margin Nm.
            y_limits = [0.0, float(peak_torque) + 5.0]
        x_lim_force=self.get_x_limits_force()
        y_lim_force = self.get_y_limits_force(peak_torque, wheel_radius, gear_ratio)
        # Generate speed range
        max_speed = max(x_limits)
        speeds = np.linspace(0.1, max_speed, 6000)
        

        # Call the appropriate plot function based on the toggle mode
        if self.plot_mode == "Powertrain Sizing":
            # Torque and Force are one analysis; the Output selector chooses which.
            # Both share the same x-axis limits (km/h / RPM-vehicle / RPM-motor);
            # only the y quantity differs (Nm torque vs N wheel force).
            if self.output_combo.get() == "Force":
                self.plot_force_graph(
                    speeds, params, gradients, wheel_radius, peak_torque, peak_power, continuous_power,
                    x_limits, y_limits, gear_ratio, plot_part, speed_unit,
                    peak_to_rated_torque_ratio=peak_to_rated_torque_ratio
                )
            else:
                self.plot_torque_graph(
                    speeds, params, gradients, wheel_radius, peak_torque, peak_power, continuous_power,
                    x_limits, y_limits, gear_ratio, plot_part, speed_unit,
                    overlay_std=True, peak_to_rated_torque_ratio=peak_to_rated_torque_ratio
                )
        elif self.plot_mode == "Acceleration":
            self.plot_vehicle_max_speed_vs_time(speeds, params, wheel_radius, peak_torque, peak_power,gear_ratio)
        elif self.plot_mode == "Parametric Study":
            self.plot_parametric_study(params, wheel_radius, peak_torque, peak_power, gear_ratio)
        elif self.plot_mode == "Engine analysis":
            self.plot_engine_analysis()
        elif self.plot_mode == "Drive Cycle":
            # Re-run whichever drive-cycle view is currently shown (speed-time,
            # torque-speed scatter, or heatmap) so pressing Update Plot / changing
            # a bin keeps the user on the same plot instead of resetting to the
            # speed-vs-time view. Falls back to the speed-time plot on first open.
            if hasattr(self, "dataframe") and self.dataframe is not None:
                last = getattr(self, "_last_dc_plot", None)
                if callable(last):
                    last()
                else:
                    self.plot_drive_cycle()
            else:
                self.show_placeholder_message("Insert Data or click the plot button")


        else:

            self.show_placeholder_message("Insert Data or Update Plot")
        # Apply per-analysis grid/legend/title/label settings to the single axis.
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()
        self.canvas.draw()


    def show_sections_for_analysis(self, analysis_type):
        """Show only the sections relevant for the selected analysis, in order, with update button at end."""
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        
        # Hide motor data upload widgets first
        self.motor_data_row.pack_forget()
        try:
            self.motor_data_status_row.pack_forget()
        except Exception:
            pass
        # Show in correct section
        if analysis_type in ["Powertrain Sizing", "Acceleration", "Parametric Study"]:
            self.motor_data_row.pack(in_=self.sections['vehicle'], before=self.sections['vehicle'].winfo_children()[0], fill="x", pady=(8, 2), padx=8)
            self.motor_data_status_row.pack(in_=self.sections['vehicle'], after=self.motor_data_row, fill="x", pady=(0, 2), padx=16)
        elif analysis_type == "Drive Cycle":
            # Pack under the Drive Cycle Data Upload section, after the drive cycle row
            self.motor_data_row.pack(in_=self.sections['drive_cycle'], after=self.sections['drive_cycle'].winfo_children()[0], fill="x", pady=(8, 2), padx=8)
            self.motor_data_status_row.pack(in_=self.sections['drive_cycle'], after=self.motor_data_row, fill="x", pady=(0, 2), padx=16)
        # to_show = self.analysis_sections.get(analysis_type, []).copy()
        # if hasattr(self, "dataframe") and self.dataframe is not None:
        #     if 'drive_cycle_props' not in to_show:
        #         to_show.append('drive_cycle_props')
        to_show = self.analysis_sections.get(analysis_type, []).copy()
        if analysis_type == "Drive Cycle" and hasattr(self, "dataframe") and self.dataframe is not None:
            if 'drive_cycle_props' not in to_show:
                to_show.append('drive_cycle_props')
        if analysis_type == "Range analysis" and hasattr(self, "dataframe") and self.dataframe is not None:
            if 'drive_cycle_props' not in to_show:
                to_show.append('drive_cycle_props')
        for frame in self.sections.values():
            frame.pack_forget()
        for key in to_show:
            self.sections[key].pack(fill="x", pady=5)
            # if key == 'sim':
            #     if analysis_type == "Acceleration":
            #         self.max_time_frame.pack(fill="x", pady=8, padx=16)
            #         self.target_speed_frame.pack(fill="x", pady=8, padx=16)
            #     else:
            #         self.max_time_frame.pack_forget()
            #         self.target_speed_frame.pack_forget()
            if key == 'sim':
                # Hide all sim entry frames first
                self.xlim_frame.pack_forget()
                self.xlim_rpm_vehicle_frame.pack_forget()
                self.xlim_rpm_motor_frame.pack_forget()
                self.ylim_frame.pack_forget()
                self.ylim_wheel_frame.pack_forget()
                self.ylim_wheel_force_frame.pack_forget()
                self.max_time_frame.pack_forget()
                self.target_speed_frame.pack_forget()

                if analysis_type == "Powertrain Sizing":
                    # X-limit fields are shared by Torque and Force (km/h + both RPM).
                    self.xlim_frame.pack(fill="x", pady=8, padx=16)
                    self.xlim_rpm_vehicle_frame.pack(fill="x", pady=8, padx=16)
                    self.xlim_rpm_motor_frame.pack(fill="x", pady=8, padx=16)
                    if self.output_combo.get() == "Force":
                        # Force: y-axis is wheel force (N).
                        self.ylim_wheel_force_frame.pack(fill="x", pady=8, padx=16)
                    else:
                        # Torque: y-axis is torque (Nm), motor and wheel.
                        self.ylim_frame.pack(fill="x", pady=8, padx=16)
                        self.ylim_wheel_frame.pack(fill="x", pady=8, padx=16)
                elif analysis_type == "Acceleration":
                    self.max_time_frame.pack(fill="x", pady=8, padx=16)
                    self.target_speed_frame.pack(fill="x", pady=8, padx=16)
                    self.update_button.pack_forget()
                    self.params_label.pack_forget()
                elif analysis_type == "Compare Standard Motor Data":
                    # Which axis-limit / simulation fields are relevant depends on
                    # which of the four Compare radio buttons is selected. The
                    # x-axis is always plotted in km/h here (see update_compare_std_plot),
                    # so only the km/h X-limit field applies -- no RPM variants.
                    plot_kind = self.compare_std_plot_var.get() if hasattr(self, "compare_std_plot_var") else "torque"
                    if plot_kind == "torque":
                        self.xlim_frame.pack(fill="x", pady=8, padx=16)
                        self.ylim_frame.pack(fill="x", pady=8, padx=16)
                        self.ylim_wheel_frame.pack(fill="x", pady=8, padx=16)
                    elif plot_kind == "force":
                        self.xlim_frame.pack(fill="x", pady=8, padx=16)
                        self.ylim_wheel_force_frame.pack(fill="x", pady=8, padx=16)
                    elif plot_kind == "acceleration":
                        self.max_time_frame.pack(fill="x", pady=8, padx=16)
                        self.target_speed_frame.pack(fill="x", pady=8, padx=16)
                    # "efficiency": the map's color range is data-driven -- no
                    # axis-limit fields apply, so none are shown.

            # Keep the Crr/CdA info label at the bottom of the section list.
            # (The in-scroll Update button is gone; the pinned top button is
            # the single Update action.)
            self.params_label.pack_forget()
            self.params_label.pack(pady=10, anchor="w")

        # Refresh the required/optional data checklist for this analysis.
        if hasattr(self, "update_data_checklist"):
            self.update_data_checklist(analysis_type)

        # Fill the Graph Settings panel with the controls for this analysis.
        # For Powertrain Sizing this resolves to the Torque or Force namespace
        # depending on the Output selector, so each keeps its own settings.
        if hasattr(self, "populate_graph_settings"):
            self.populate_graph_settings(self._gs_analysis())

        # Keep user-collapsed sections collapsed even after this method re-packs
        # their subframes for the newly selected analysis.
        if hasattr(self, "reapply_collapsed_states"):
            self.reapply_collapsed_states()


    def update_plot(self, choice=None):
        """Update plot mode and refresh graph based on the selected toggle option"""
        self.plot_mode = self.plot_type.get()
        # Keep the Plotting Part selector locked to At Wheel while Output = Force.
        if hasattr(self, "_sync_plot_part_lock"):
            self._sync_plot_part_lock()
        self.show_sections_for_analysis(self.plot_mode)  # Show/hide sections
        # Route through _safe_plot (busy cursor, red-bordered bad fields, errors
        # surfaced in the status bar) instead of calling the plot directly. It
        # dispatches to update_compare_std_plot / plot_graph itself.
        if hasattr(self, "_safe_plot"):
            self._safe_plot()
        elif self.plot_type.get() == "Compare Standard Motor Data":
            self.update_compare_std_plot()
        else:
            self.plot_graph()
