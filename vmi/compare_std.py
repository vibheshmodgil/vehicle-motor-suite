"""Auto-generated module (method bodies copied verbatim from the original app)."""
import json
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import numpy as np
import pandas as pd
import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import TwoSlopeNorm
from PIL import Image
from scipy.interpolate import RegularGridInterpolator, UnivariateSpline
from scipy.ndimage import gaussian_filter

from .theme import COLORS, FONTS
from .physics import calculate_crr_cd_a, df, g
from .calc_ext import cap_torque_to_power



class CompareStdMixin:

    def refresh_std_motor_table(self):
        # Clear previous rows
        for row in self.std_motor_table_rows:
            row.destroy()
        self.std_motor_table_rows.clear()

        # Header
        header = ctk.CTkFrame(self.std_motor_table_frame, fg_color=COLORS['section_bg'])
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Motor", width=120).pack(side="left", padx=4)
        ctk.CTkLabel(header, text="Gear Ratio", width=100).pack(side="left", padx=4)
        ctk.CTkLabel(header, text="Wheel Radius", width=100).pack(side="left", padx=4)
        ctk.CTkLabel(header, text="Delete", width=60).pack(side="left", padx=4)
        self.std_motor_table_rows.append(header)
         # Rows
        for i, entry in enumerate(self.selected_std_motors):
            row = ctk.CTkFrame(self.std_motor_table_frame, fg_color=COLORS['section_bg'])
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=entry["name"], width=120).pack(side="left", padx=4)
            # Editable gear ratio
            gear_entry = ctk.CTkEntry(row, width=100)
            gear_entry.insert(0, str(entry["gear_ratio"]))
            gear_entry.pack(side="left", padx=4)
            # Editable wheel radius
            radius_entry = ctk.CTkEntry(row, width=100)
            radius_entry.insert(0, str(entry["wheel_radius"]))
            radius_entry.pack(side="left", padx=4)
            # Update the entry dict on change
            def update_gear(e, idx=i):
                self.selected_std_motors[idx]["gear_ratio"] = float(gear_entry.get())
            def update_radius(e, idx=i):
                self.selected_std_motors[idx]["wheel_radius"] = float(radius_entry.get())
            gear_entry.bind("<KeyRelease>", update_gear)
            radius_entry.bind("<KeyRelease>", update_radius)
            # Delete button
            del_btn = ctk.CTkButton(row, text="Delete", width=60, fg_color=COLORS['warning'],
                                    command=lambda idx=i: self.delete_std_motor(idx))
            del_btn.pack(side="left", padx=4)
            self.std_motor_table_rows.append(row)

    def delete_std_motor(self, idx):
        del self.selected_std_motors[idx]
        self.refresh_std_motor_table()
        self.update_compare_std_plot()  # Refresh the plot if needed

    def update_compare_std_plot(self):
        plot_type = self.compare_std_plot_var.get()  # "torque", "force", "acceleration", "efficiency"
        mode = self.torque_compare_mode.get()  # "Wheel" or "Motor"
        _speed_unit_widget = getattr(self, "compare_speed_unit_combo", None)
        x_axis_unit = _speed_unit_widget.get() if _speed_unit_widget is not None else "Km/hr"
        if plot_type not in ("torque", "force"):
            # Acceleration's x-axis is Time; Efficiency has its own axes --
            # RPM has no meaning there, so the toggle only touches Torque/Force.
            x_axis_unit = "Km/hr"
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()

        # Efficiency comparison needs none of the vehicle/torque inputs below
        # (it only reads Motor Input Parameters + the two efficiency maps), so
        # branch out before that parsing can fail on an unrelated blank field.
        if plot_type == "efficiency":
            self._plot_compare_std_efficiency_map()
            return

        # Use the same parameters as your main plot_graph
        try:
            m_ref = float(self.m_ref.get())
            rear_load_ratio = float(self.rear_load_ratio.get())
            ambient_temp = float(self.ambient_temp.get())
            ambient_pressure = float(self.ambient_pressure.get())
            crr = float(self.crr.get()) if self.crr.get().strip() else None
            cd_a = float(self.cd_a.get()) if self.cd_a.get().strip() else None
            gear_ratio = float(self.gear_ratio.get())
            gear_eff = self.get_gear_efficiency_value()
            params = calculate_crr_cd_a(
                m_ref,
                rear_load_ratio,
                ambient_temp,
                ambient_pressure,
                crr=crr if self.crr_manual else None,
                cd_a=cd_a if self.cda_manual else None,
            )
            gradients = self.get_gradients_pct()
            wheel_radius = float(self.wheel_radius.get())
            peak_torque = float(self.peak_torque.get())
            peak_power = float(self.peak_power.get())
            continuous_power = float(self.continuous_power.get())
        except Exception as exc:
            messagebox.showerror("Input Error", str(exc))
            return
        try:
            peak_to_rated_torque_ratio = float(self.peak_to_rated_torque_ratio.get())
            if peak_to_rated_torque_ratio <= 0:
                raise ValueError
        except Exception:
            peak_to_rated_torque_ratio = 2.0
            self.peak_to_rated_torque_ratio.delete(0, "end")
            self.peak_to_rated_torque_ratio.insert(0, "2")
        # Compare always plots against km/h on the x-axis (see the "speeds" /
        # "speeds_in_rpm" arrays below and the axis labels each branch sets) --
        # hardcode it rather than reading self.speed_unit_combo, which belongs
        # to the (hidden-in-this-analysis) Plot Mode section and may be left on
        # "RPM" from a different analysis, which would return an RPM-valued
        # x_limits pair for a km/h-valued speeds array.
        speed_unit = "Km/hr"
        plot_part = "At Wheel" if mode == "Wheel" else "At Motor"
        x_limits = self.get_x_limits(speed_unit, wheel_radius, gear_ratio)
        y_limits = self.get_y_limits(plot_part, gear_ratio)
        max_speed = max(x_limits)
        speeds = np.linspace(0.1, max_speed, 6000)  # always km/h -- the shared physical sweep
        speeds_in_rpm = np.linspace(1e-3, max_speed * 60 / (2 * np.pi * wheel_radius * 3.6), 1000)  # RPM for interpolation
        y_data_list = []

        # When the user picked RPM, hand plot_torque_graph/plot_force_graph an
        # RPM-scaled x_limits (converted via the CURRENT vehicle's wheel_radius
        # / gear_ratio only) and speed_unit="RPM" -- they already derive their
        # own wheel/motor RPM axis from `speeds` + those same two params
        # (see torque_force.py), so no saved motor's own wheel radius is
        # involved in the CURRENT motor's curve at all, in either mode.
        if x_axis_unit == "RPM":
            wheel_rpm_max = max_speed / 3.6 / wheel_radius * 60 / (2 * np.pi)
            plot_speed_unit = "RPM"
            plot_x_limits = [0.0, (wheel_rpm_max * gear_ratio if mode == "Motor" else wheel_rpm_max) * 1.05]
        else:
            plot_speed_unit = "km/hr"
            plot_x_limits = x_limits

        # Call the correct plot function
        if plot_type == "torque":

            self.plot_torque_graph(
                speeds, params, gradients, wheel_radius, peak_torque, peak_power, continuous_power,
                plot_x_limits, y_limits, gear_ratio, plot_part=plot_part, speed_unit=plot_speed_unit,
                overlay_std=False, show_main_label=False, show_continuous=False,
                peak_to_rated_torque_ratio=peak_to_rated_torque_ratio
            )
            y_data_list.append(y_limits)
        elif plot_type == "force":
            self.plot_force_graph(
                speeds, params, gradients, wheel_radius, peak_torque, peak_power, continuous_power,
                plot_x_limits, y_limits, gear_ratio, plot_part="At Wheel", speed_unit=plot_speed_unit,
                peak_to_rated_torque_ratio=peak_to_rated_torque_ratio
            )
            y_data_list.append(y_limits[1])
            
        elif plot_type == "acceleration":
            self.plot_vehicle_max_speed_vs_time(
                speeds, params, wheel_radius, peak_torque, peak_power, gear_ratio
            )
        # y_data_list = []
        # Battery DC limit (optional): the battery is a property of the
        # VEHICLE, so the same shaft-power cap clips every saved motor's
        # curve too, not just the current motor's (map-aware when the
        # session's efficiency maps are loaded). None -> untouched.
        for entry in self.selected_std_motors:
            name = entry["name"]
            std_gear_ratio = float(entry["gear_ratio"])
            std_wheel_radius = float(entry["wheel_radius"])
            std_data = self.std_motor_data[name]
            speeds_rpm_std = np.array(std_data["speed_rpm"])
            torque_std = np.array(std_data["torque"])
            # Interpolation grid: which RPM of THIS motor's own saved curve
            # corresponds to each point of the shared `speeds` sweep.
            # - Km/hr mode: converted via this motor's own wheel radius (its
            #   "what if this motor were installed on a wheel this size"
            #   radius) -- unchanged, existing behaviour.
            # - RPM mode: converted via the CURRENT VEHICLE's wheel radius
            #   only, same for every motor being compared, so a saved motor's
            #   own wheel_radius affects *which point of its curve* is looked
            #   up (still correct -- more torque data available), but never
            #   *where that point lands on the shared axis*. gear_ratio still
            #   correctly moves the Motor-RPM axis per motor -- that's real.
            if x_axis_unit == "RPM":
                speeds_rpm_wheel = speeds * 60 / (2 * np.pi * wheel_radius) / 3.6
            else:
                speeds_rpm_wheel = speeds * 60 / (2 * np.pi * std_wheel_radius) / 3.6
            speeds_rpm_motor = speeds_rpm_wheel * std_gear_ratio

            if x_axis_unit == "RPM":
                x_for_plot = speeds_rpm_motor if (plot_type == "torque" and mode == "Motor") else speeds_rpm_wheel
            else:
                x_for_plot = speeds

            if plot_type == "torque":

                # Plot torque vs speed (at wheel or motor)
                interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                interp_torque = self.cap_torque_to_battery(interp_torque, speeds_rpm_motor)
                if mode == "Wheel":
                    y = interp_torque * std_gear_ratio * gear_eff
                    label = f"{name} (Wheel)"
                else:
                    y = interp_torque
                    label = f"{name} (Motor)"
                y_data_list.append(y)

                (line,) = self.ax.plot(x_for_plot, y, label=label, linewidth=self.gs_float("line_width", 2.0))
                if x_axis_unit == "RPM":
                    self.ax.set_xlabel("Motor Speed (RPM)" if mode == "Motor" else "Wheel Speed (RPM)")
                else:
                    self.ax.set_xlabel("wheel Speed (km/hr)")
                self.ax.set_ylabel("Torque (Nm)")
                self.ax.set_title("Compare Standard Motor Data: Torque")

                # Mark where THIS motor's torque crosses each gradient's
                # resistive-torque line -- the whole point of comparing motors
                # is to see who reaches (or fails to reach) a given gradient,
                # so each saved motor needs its own intersection markers, not
                # just the current motor's. Recomputed here (matches the
                # resistive-torque formula plot_torque_graph already used to
                # draw the shared "Gradient X%" line above) since that array
                # isn't returned by the call that drew it. The resistive-force
                # physics always needs REAL road speed, so it's computed from
                # `speeds` (km/h) regardless of what unit is plotted on x --
                # `speeds` and `x_for_plot` stay index-aligned either way.
                if x_axis_unit == "RPM":
                    x_label = "Motor Speed (RPM)" if mode == "Motor" else "Wheel Speed (RPM)"
                else:
                    x_label = "Wheel Speed (km/hr)" if mode == "Wheel" else "Motor Speed (km/hr)"
                motor_back_drive_scale = max(gear_ratio * gear_eff, 1e-9)
                for gradient in gradients:
                    wheel_resistive_torque = (
                        params['m_i'] * g * params['Crr'] * np.cos(np.arctan(gradient / 100)) +
                        0.5 * 1.225 * params['CdA'] * (speeds / 3.6) ** 2 +
                        params['m_i'] * g * np.sin(np.arctan(gradient / 100))
                    ) * wheel_radius
                    y_resist = (wheel_resistive_torque if mode == "Wheel"
                               else wheel_resistive_torque / motor_back_drive_scale)
                    self._annotate_intersections(
                        x_for_plot, y, y_resist, x_label, "Nm",
                        marker_color=line.get_color(), marker_style='D',
                        text_color=line.get_color(), fontsize=8)
            elif plot_type == "force":
                # F = (torque * gear_ratio) / wheel_radius -- Force is always
                # a wheel quantity, so x_for_plot above used speeds_rpm_wheel.
                interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                interp_torque = self.cap_torque_to_battery(interp_torque, speeds_rpm_motor)
                force = (interp_torque * std_gear_ratio * gear_eff) / wheel_radius
                y=force
                y_data_list.append(y)
                self.ax.plot(x_for_plot, y, label=f"{name} (Wheel Force)", linewidth=self.gs_float("line_width", 2.0))
                self.ax.set_xlabel("Wheel Speed (RPM)" if x_axis_unit == "RPM" else "Wheel Speed (km/hr)")
                self.ax.set_ylabel("Force (N)")
                self.ax.set_title("Compare Standard Motor Data: Force")

            elif plot_type == "acceleration":
                # Simulate velocity-time for this std motor (interpolated torque)
                speeds_kmh = np.linspace(0.1, max_speed, 6000)
                speeds_mps = speeds_kmh / 3.6
                speeds_rpm_wheel = speeds_mps * 60 / (2 * np.pi * std_wheel_radius)
                speeds_rpm_motor = speeds_rpm_wheel * std_gear_ratio
                interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                interp_torque = self.cap_torque_to_battery(interp_torque, speeds_rpm_motor)
                max_wheel_force = interp_torque * std_gear_ratio * gear_eff / std_wheel_radius
                wheel_forces = np.array([
                    params['m_i'] * g * params['Crr'] +
                    0.5 * 1.225 * params['CdA'] * (s ** 2) +
                    params['m_i'] * g * np.sin(np.arctan(params.get('gradient', 0) / 100))
                    for s in speeds_mps
                ])
                net_force = max_wheel_force - wheel_forces
                # Wheel rotational inertia (J/r^2) slows the m*a term only.
                max_acceleration = net_force / self.get_effective_inertial_mass(params['m_i'], std_wheel_radius)
                dt = 0.01
                max_time = float(self.max_time.get())
                time_steps = np.arange(0, max_time, dt)
                velocity = [0]
                for t in time_steps[:-1]:
                    current_speed = velocity[-1]
                    closest_idx = np.argmin(np.abs(speeds_mps - current_speed))
                    current_acceleration = max_acceleration[closest_idx]
                    new_velocity = velocity[-1] + current_acceleration * dt
                    if new_velocity >= speeds_mps[-1]:
                        break
                    velocity.append(new_velocity)
                velocity = np.array(velocity)
                velocity_kmh = velocity * 3.6
                time_values = np.array(time_steps[:len(velocity)])
                self.ax.plot(time_values, velocity_kmh, label=f"{name} (Velocity-Time)", linewidth=self.gs_float("line_width", 2.0))
                self.ax.set_xlabel("Time (s)")
                self.ax.set_ylabel("Vehicle Speed (km/h)")
                self.ax.set_title("Compare Standard Motor Data: Acceleration")
                # Mark intersection for target speed
                speed_target = float(self.target_speed.get())
                index_target = np.where(velocity_kmh >= speed_target)[0]
                if len(index_target) > 0:
                    time_target = time_values[index_target[0]]
                    self.ax.axvline(x=time_target, color='orange', linestyle='--', label=f"{name} {speed_target} km/h at {time_target:.1f}s")
                    self.ax.scatter(time_target, speed_target, color='orange', zorder=3)
                    self.ax.text(time_target, speed_target + 2, f"{time_target:.1f}s", color='orange', fontsize=10)
        if plot_type == "torque":
            # "Y-axis Limit (Nm) motor" vs "...wheel" are two separate entry
            # fields (Simulation Settings section) -- read/write whichever one
            # actually matches what's plotted for the selected Wheel/Motor mode.
            if mode == "Wheel":
                y_limits = self.get_compare_std_y_limits("torque", y_data_list, 'ylim_wheel_manual', 'ylim_wheel')
            else:
                y_limits = self.get_compare_std_y_limits("torque", y_data_list, 'ylim_manual', 'ylim')
        elif plot_type == "force":
            y_limits = self.get_compare_std_y_limits("force", y_data_list, 'ylim_wheel_force_manual', 'ylim_wheel_force')
        else:
            y_limits = None
        if y_limits is not None:
           self.ax.set_ylim(y_limits) #
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()
        else:
            self.ax.legend()
            self.ax.grid(True)
        self.canvas.draw()

    def _plot_compare_std_efficiency_map(self):
        """Compare the currently loaded Motor efficiency map (Efficiency Maps
        section, Motor 1) against whichever selected standard motor was saved
        WITH an efficiency map (see `_current_eff_map_for_save` /
        `save_std_motor_data_popup`). Same diverging-colormap difference-map
        style as `plot_efficiency_difference_map` (Drive Cycle Efficiency):
        positive = the saved motor is more efficient there, negative = the
        currently loaded motor is. Masked to the current motor's capability
        envelope, with the envelope curve drawn on top -- same convention as
        every other efficiency map in the app. Only the FIRST selected motor
        that has a saved map is used (mirrors the existing "first selected"
        fallback already used when saving)."""
        tq1 = getattr(self, "eff1_map_torques", None)
        rpm1 = getattr(self, "eff1_map_rpms", None)
        eff1 = getattr(self, "eff1_map_matrix", None)
        if tq1 is None or rpm1 is None or eff1 is None:
            self.show_placeholder_message(
                "Upload the current Motor's efficiency map\n"
                "(Efficiency Maps section) to compare.")
            return

        saved = next((e for e in self.selected_std_motors if e.get("eff_map")), None)
        if saved is None:
            self.show_placeholder_message(
                "None of the selected standard motors have a saved efficiency map.\n"
                "Load a Motor efficiency map, then use \"Save Motor Data\" to store\n"
                "it with that motor, and choose it here.")
            return

        name = saved["name"]
        eff_map = saved["eff_map"]
        tq2 = np.asarray(eff_map["torque_axis"], dtype=float)
        rpm2 = np.asarray(eff_map["rpm_axis"], dtype=float)
        eff2 = np.asarray(eff_map["matrix"], dtype=float)
        tq1 = np.asarray(tq1, dtype=float)
        rpm1 = np.asarray(rpm1, dtype=float)
        eff1 = np.asarray(eff1, dtype=float)

        rpm_min = max(float(np.nanmin(rpm1)), float(np.nanmin(rpm2)))
        rpm_max = min(float(np.nanmax(rpm1)), float(np.nanmax(rpm2)))
        tq_min = max(float(np.nanmin(tq1)), float(np.nanmin(tq2)))
        tq_max = min(float(np.nanmax(tq1)), float(np.nanmax(tq2)))
        if rpm_max <= rpm_min or tq_max <= tq_min:
            self.show_placeholder_message(
                f"No overlapping RPM/Torque region between the current map and '{name}'.")
            return

        rpm_grid = np.linspace(rpm_min, rpm_max, 200)
        torque_grid = np.linspace(tq_min, tq_max, 200)
        speed_mesh, torque_mesh = np.meshgrid(rpm_grid, torque_grid)
        pts = np.column_stack((torque_mesh.ravel(), speed_mesh.ravel()))

        interp_current = RegularGridInterpolator((tq1, rpm1), eff1 * 100.0, bounds_error=False, fill_value=np.nan)
        interp_saved = RegularGridInterpolator((tq2, rpm2), eff2 * 100.0, bounds_error=False, fill_value=np.nan)
        current_interp = interp_current(pts).reshape(torque_mesh.shape)
        saved_interp = interp_saved(pts).reshape(torque_mesh.shape)

        diff_map = saved_interp - current_interp
        diff_map[np.abs(diff_map) > 15.0] = np.nan

        cap_mask = self._motor_capability_mask(torque_mesh, speed_mesh, motor=1)
        if cap_mask is not None:
            diff_map = np.where(cap_mask, diff_map, np.nan)

        finite = diff_map[np.isfinite(diff_map)]
        vmax = max(float(np.nanmax(np.abs(finite))) if finite.size else 1.0, 0.5)
        norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)

        cmap_name = self.gs_str("cmap", "RdBu")
        fill_levels = self.gs_int("fill_levels", 30)
        line_levels = self.gs_int("line_levels", 10)

        contour = self.ax.contourf(speed_mesh, torque_mesh, diff_map, cmap=cmap_name, norm=norm, levels=fill_levels)
        self.efficiency_colorbar = self.figure.colorbar(contour, ax=self.ax)
        self.efficiency_colorbar.set_label(f"Efficiency Difference (%)  ('{name}' minus Current)",
                                           fontsize=12, weight='bold')
        contour_lines = self.ax.contour(speed_mesh, torque_mesh, diff_map, colors='#334155', levels=line_levels, linewidths=0.5)
        self.ax.clabel(contour_lines, inline=True, fontsize=9, fmt='%1.0f%%')
        self.ax.set_xlabel('Speed (RPM)', fontsize=14, weight='bold')
        self.ax.set_ylabel('Torque (Nm)', fontsize=14, weight='bold')
        self.ax.set_title(f"Compare Efficiency Map: Current vs '{name}'", fontsize=16, weight='bold')
        self._draw_motor_capability_curve()
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()
        else:
            self.ax.set_axisbelow('line')
            self.ax.grid(True, linestyle='--', alpha=0.6)
        self.canvas.draw()

