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



class TorqueForceMixin:

    def _auto_cap_xlimits(self, x_limits, intersection_x, manual, entry, margin=1.1):
        """Cap the upper x-limit to `margin` * the furthest intersection speed.

        The region beyond where the motor capability curve last crosses a
        resistive curve is dead space (the vehicle can't go faster), so trimming
        it makes the useful part of the plot fill the axes. Skipped when the axis
        is in manual mode or no crossings were found. When applied, the matching
        entry box is updated so the shown limit matches the axis.
        """
        xs = [float(v) for v in intersection_x if np.isfinite(v) and v > 0]
        if manual or not xs:
            return x_limits
        lo = float(x_limits[0]) if x_limits else 0.0
        hi = max(xs) * float(margin)
        if hi <= lo:
            return x_limits
        if entry is not None:
            try:
                entry.delete(0, "end")
                entry.insert(0, f"{lo:.2f},{hi:.2f}")
            except Exception:
                pass
        return [lo, hi]

    def _annotate_intersections(
        self,
        x_vals,
        y_main_vals,
        y_resist_vals,
        x_axis_label,
        y_unit,
        marker_color,
        marker_style='o',
        text_color='black',
        show_text=True,
        fontsize=8,
    ):
        x_arr = np.asarray(x_vals, dtype=float)
        y_main_arr = np.asarray(y_main_vals, dtype=float)
        y_resist_arr = np.asarray(y_resist_vals, dtype=float)
        if len(x_arr) < 2 or len(y_main_arr) < 2 or len(y_resist_arr) < 2:
            return []

        diff = y_main_arr - y_resist_arr
        sign_change_idx = np.where(np.diff(np.sign(diff)) != 0)[0]
        speed_unit_text = self._speed_unit_from_label(x_axis_label)
        intersections = []

        for idx in sign_change_idx:
            x1, x2 = x_arr[idx], x_arr[idx + 1]
            d1, d2 = diff[idx], diff[idx + 1]
            if d2 == d1:
                intersection_speed = x1
            else:
                intersection_speed = x1 - d1 * (x2 - x1) / (d2 - d1)

            intersection_y = np.interp(intersection_speed, x_arr, y_main_arr)
            intersections.append((float(intersection_speed), float(intersection_y)))
            self.ax.scatter(intersection_speed, intersection_y, color=marker_color, marker=marker_style, zorder=5)

            if show_text:
                self.ax.text(
                    float(intersection_speed),
                    float(intersection_y),
                    f"{float(intersection_speed):.1f} {speed_unit_text}\n{float(intersection_y):.1f} {y_unit}",
                    fontsize=fontsize,
                    ha='left',
                    va='bottom',
                    color=text_color,
                    bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', pad=1.5),
                )
        return intersections



    def plot_torque_graph(
        self,
        speeds,
        params,
        gradients,
        wheel_radius,
        peak_torque,
        peak_power,
        continuous_power,
        x_limits,
        y_limits,
        gear_ratio,
        plot_part,
        speed_unit,
        overlay_std=True,
        show_main_label=True,
        show_continuous=True,
        peak_to_rated_torque_ratio=2.0,
    ):
        """Plots torque vs speed with peak and continuous curves and intersection annotations.

        `show_continuous=False` (used by the Compare Standard Motor Data view)
        drops the continuous/"rated" torque curve and its gradient-intersection
        markers entirely -- comparing several motors' *rated* torque adds
        clutter with little value, so that view only cares about peak torque.
        """
        if overlay_std:
            self.ax.clear()
        if hasattr(self, "heatmap_colorbar") and self.heatmap_colorbar is not None:
            self.heatmap_colorbar.remove()
            self.heatmap_colorbar = None
        self.ax.clear()

        ratio = float(peak_to_rated_torque_ratio) if float(peak_to_rated_torque_ratio) > 0 else 2.0
        gear_eff = self.get_gear_efficiency_value()
        wheel_drive_scale = gear_ratio * gear_eff
        motor_back_drive_scale = max(wheel_drive_scale, 1e-9)

        speeds_kmh_wheel = speeds
        speeds_rpm_wheel = speeds * 60 / (2 * np.pi * wheel_radius) / 3.6
        speeds_rpm_motor = speeds_rpm_wheel * gear_ratio
        speeds_kmh_motor = speeds_kmh_wheel

        peak_power_w = peak_power * 1000
        continuous_power_w = continuous_power * 1000
        base_speed_rpm_peak = (peak_power_w / peak_torque) * 60 / (2 * np.pi)
        motor_omega = np.maximum((speeds_rpm_motor * 2 * np.pi) / 60, 1e-6)

        peak_torque_curve = np.where(
            speeds_rpm_motor <= base_speed_rpm_peak,
            peak_torque,
            peak_power_w / motor_omega
        )
        rated_torque_motor = peak_torque / ratio
        continuous_torque_curve = np.minimum(rated_torque_motor, continuous_power_w / motor_omega)

        df_motor = pd.DataFrame({
            "speeds_rpm_motor": speeds_rpm_motor,
            "speeds_kmh_motor": speeds_kmh_motor,
            "speeds_rpm_wheel": speeds_rpm_wheel,
            "speeds_kmh_wheel": speeds_kmh_wheel,
            "motor_torque": peak_torque_curve,
            "wheel_torque": peak_torque_curve * wheel_drive_scale,
            "motor_continuous_torque": continuous_torque_curve,
            "wheel_continuous_torque": continuous_torque_curve * wheel_drive_scale,
        })

        for gradient in gradients:
            wheel_resistive_torque = (
                params['m_i'] * g * params['Crr'] * np.cos(np.arctan(gradient / 100)) +
                0.5 * 1.225 * params['CdA'] * (df_motor["speeds_kmh_wheel"] / 3.6) ** 2 +
                params['m_i'] * g * np.sin(np.arctan(gradient / 100))
            ) * wheel_radius
            motor_resistive_torque = wheel_resistive_torque / motor_back_drive_scale
            df_motor[f"wheel_resistive_torque_{gradient}"] = wheel_resistive_torque
            df_motor[f"motor_resistive_torque_{gradient}"] = motor_resistive_torque

        # Collect peak-curve / resistive intersection speeds so the x-axis can be
        # capped just past the furthest one (see _auto_cap_xlimits).
        peak_int_x = []

        # Per-line appearance from the Graph Settings panel (defaults reproduce
        # the original look exactly).
        peak_c = self.gs_color("peak_color", "black")
        peak_ls = self.gs_linestyle("peak_style", "--")
        peak_lw = self.gs_float("peak_width", 2.0)
        cont_c = self.gs_color("cont_color", "gray")
        cont_ls = self.gs_linestyle("cont_style", ":")
        cont_lw = self.gs_float("cont_width", 2.0)
        grad_c = self.gs_color("grad_color", None)
        grad_ls = self.gs_linestyle("grad_style", "--")
        grad_lw = self.gs_float("grad_width", 1.5)
        grad_ckw = {"color": grad_c} if grad_c else {}

        analysis_type = self.plot_type.get()
        if analysis_type == "Powertrain Sizing":
            speed_unit = self.speed_unit_combo.get()
            plot_part = self.plot_part_combo.get()

        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            df_sorted = self.motor_dataframe.sort_values("motor_speed")
            if plot_part == "At Motor":
                if speed_unit == "RPM":
                    x = speeds_rpm_motor
                    x_label = "Motor Speed (RPM)"
                    motor_rpm_for_interp = x
                else:
                    x = speeds_kmh_motor
                    x_label = "Motor Speed (km/h)"
                    motor_rpm_for_interp = x * 60 / (2 * np.pi * wheel_radius) / 3.6 * gear_ratio

                y = np.interp(motor_rpm_for_interp, df_sorted["motor_speed"], df_sorted["motor_torque"])
                # Rated/continuous from the UPLOADED curve / ratio -- the Excel
                # peak curve replaces the theoretical one, so the manual Peak
                # Torque entry must not drive the continuous curve (it made
                # "rated" exceed the uploaded peak). The ratio field stays live.
                y_cont = y / ratio
                peak_curve_name = "Peak Motor Torque"
                cont_curve_name = "Continuous Motor Torque"

                for gradient in gradients:
                    y_resist = df_motor[f"motor_resistive_torque_{gradient}"]
                    self.ax.plot(x, y_resist, label=f"Gradient {gradient}%", linestyle=grad_ls, linewidth=grad_lw, **grad_ckw)
                    peak_int_x += [p[0] for p in self._annotate_intersections(x, y, y_resist, x_label, "Nm", "red")]
                    if show_continuous:
                        self._annotate_intersections(x, y_cont, y_resist, x_label, "Nm", "darkorange")

                if show_main_label:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw, label=peak_curve_name)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw, label=cont_curve_name)
                else:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw)
            else:
                if speed_unit == "RPM":
                    x = speeds_rpm_wheel
                    x_label = "Wheel Speed (RPM)"
                    motor_rpm = x * gear_ratio
                else:
                    x = speeds_kmh_wheel
                    x_label = "Wheel Speed (km/h)"
                    motor_rpm = x * 60 / (2 * np.pi * wheel_radius) / 3.6 * gear_ratio

                y = np.interp(motor_rpm, df_sorted["motor_speed"], df_sorted["motor_torque"]) * wheel_drive_scale
                # Same fix as the At-Motor branch: continuous = uploaded peak
                # curve / ratio (already scaled to the wheel here).
                y_cont = y / ratio
                peak_curve_name = "Peak Wheel Torque"
                cont_curve_name = "Continuous Wheel Torque"

                for gradient in gradients:
                    y_resist = df_motor[f"wheel_resistive_torque_{gradient}"]
                    self.ax.plot(x, y_resist, label=f"Gradient {gradient}%", linestyle=grad_ls, linewidth=grad_lw, **grad_ckw)
                    peak_int_x += [p[0] for p in self._annotate_intersections(x, y, y_resist, x_label, "Nm", "red")]
                    if show_continuous:
                        self._annotate_intersections(x, y_cont, y_resist, x_label, "Nm", "darkorange")

                if show_main_label:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw, label=peak_curve_name)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw, label=cont_curve_name)
                else:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw)
        else:
            if plot_part == "At Motor":
                if speed_unit == "RPM":
                    x = df_motor["speeds_rpm_motor"]
                    x_label = "Motor Speed (RPM)"
                else:
                    x = df_motor["speeds_kmh_motor"]
                    x_label = "Motor Speed (km/h)"

                y = df_motor["motor_torque"]
                y_cont = df_motor["motor_continuous_torque"]
                peak_curve_name = "Peak Motor Torque"
                cont_curve_name = "Continuous Motor Torque"

                for gradient in gradients:
                    y_resist = df_motor[f"motor_resistive_torque_{gradient}"]
                    self.ax.plot(x, y_resist, label=f"Motor Resistive Torque (Gradient {gradient}%)", linestyle=grad_ls, linewidth=grad_lw, **grad_ckw)
                    peak_int_x += [p[0] for p in self._annotate_intersections(x, y, y_resist, x_label, "Nm", "red")]
                    if show_continuous:
                        self._annotate_intersections(x, y_cont, y_resist, x_label, "Nm", "darkorange")

                if show_main_label:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw, label=peak_curve_name)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw, label=cont_curve_name)
                else:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw)
            else:
                if speed_unit == "RPM":
                    x = df_motor["speeds_rpm_wheel"]
                    x_label = "Wheel Speed (RPM)"
                else:
                    x = df_motor["speeds_kmh_wheel"]
                    x_label = "Wheel Speed (km/h)"

                y = df_motor["wheel_torque"]
                y_cont = df_motor["wheel_continuous_torque"]
                peak_curve_name = "Peak Wheel Torque"
                cont_curve_name = "Continuous Wheel Torque"

                for gradient in gradients:
                    y_resist = df_motor[f"wheel_resistive_torque_{gradient}"]
                    self.ax.plot(x, y_resist, label=f"Gradient {gradient}%", linestyle=grad_ls, linewidth=grad_lw, **grad_ckw)
                    peak_int_x += [p[0] for p in self._annotate_intersections(x, y, y_resist, x_label, "Nm", "red")]
                    if show_continuous:
                        self._annotate_intersections(x, y_cont, y_resist, x_label, "Nm", "darkorange")

                if show_main_label:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw, label=peak_curve_name)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw, label=cont_curve_name)
                else:
                    self.ax.plot(x, y, color=peak_c, linestyle=peak_ls, linewidth=peak_lw)
                    if show_continuous:
                        self.ax.plot(x, y_cont, color=cont_c, linestyle=cont_ls, linewidth=cont_lw)

        # Trim the dead space past the last useful intersection (auto mode only).
        if speed_unit == "RPM":
            if plot_part == "At Motor":
                _xmanual, _xentry = getattr(self, "xlim_rpm_motor_manual", False), getattr(self, "xlim_rpm_motor", None)
            else:
                _xmanual, _xentry = getattr(self, "xlim_rpm_vehicle_manual", False), getattr(self, "xlim_rpm_vehicle", None)
        else:
            _xmanual, _xentry = getattr(self, "xlim_manual", False), getattr(self, "xlim", None)
        x_limits = self._auto_cap_xlimits(x_limits, peak_int_x, _xmanual, _xentry)

        self.ax.set_title(f"{plot_part} Torque vs {x_label}", fontsize=16, weight='bold')
        self.ax.set_xlabel(x_label, fontsize=14)
        self.ax.set_ylabel("Torque (Nm)", fontsize=14)
        self.ax.set_xlim(x_limits)
        self.ax.set_ylim(y_limits)
        self.ax.legend()
        # Grid/legend/title styling is owned by apply_graph_style (called at the
        # end of plot_graph), so don't hard-code a grid here.
        if overlay_std:
            self.canvas.draw()


    def plot_force_graph(
        self,
        speeds,
        params,
        gradients,
        wheel_radius,
        peak_torque,
        peak_power,
        continuous_power,
        x_limits,
        y_limits,
        gear_ratio,
        plot_part="At Wheel",
        speed_unit="Km/hr",
        peak_to_rated_torque_ratio=2.0,
    ):
        """Plots wheel force vs speed with peak and continuous intersection points.

        The force value (y-axis) is always the *wheel* force in N. Only the x-axis
        representation follows the shared Plot Mode selectors, exactly like the
        torque view: Km/hr -> vehicle speed, RPM + At Wheel -> wheel RPM,
        RPM + At Motor -> motor RPM (these are just re-scalings of the same speed,
        so the plotted force curve is unchanged).
        """
        if hasattr(self, "heatmap_colorbar") and self.heatmap_colorbar is not None:
            self.heatmap_colorbar.remove()
            self.heatmap_colorbar = None
        self.ax.clear()

        ratio = float(peak_to_rated_torque_ratio) if float(peak_to_rated_torque_ratio) > 0 else 2.0
        gear_eff = self.get_gear_efficiency_value()
        wheel_drive_scale = gear_ratio * gear_eff

        # Per-line appearance from the Graph Settings panel (defaults = original).
        peak_c = self.gs_color("peak_color", "black")
        peak_ls = self.gs_linestyle("peak_style", "--")
        peak_lw = self.gs_float("peak_width", 2.0)
        cont_c = self.gs_color("cont_color", "gray")
        cont_ls = self.gs_linestyle("cont_style", ":")
        cont_lw = self.gs_float("cont_width", 2.0)
        grad_ls = self.gs_linestyle("grad_style", "-")
        grad_lw = self.gs_float("grad_width", 2.0)
        grad_c = self.gs_color("grad_color", None)
        grad_ckw = {"color": grad_c} if grad_c else {}

        max_speed_rpm_wheel = 60 * max(speeds) / (2 * np.pi * wheel_radius * 3.6)
        speeds_rpm_wheel = np.linspace(1e-3, max_speed_rpm_wheel, 1000)
        speeds_rpm_motor = speeds_rpm_wheel * gear_ratio
        vehicle_speeds_kmh = (speeds_rpm_wheel * 2 * np.pi * wheel_radius) * 3.6 / 60

        peak_power_w = peak_power * 1000
        continuous_power_w = continuous_power * 1000
        motor_omega = np.maximum((speeds_rpm_motor * 2 * np.pi) / 60, 1e-6)

        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            df_sorted = self.motor_dataframe.sort_values("motor_speed")
            peak_motor_torque_curve = np.interp(
                speeds_rpm_motor,
                df_sorted["motor_speed"].values,
                df_sorted["motor_torque"].values,
                left=df_sorted["motor_torque"].values[0],
                right=df_sorted["motor_torque"].values[-1],
            )
        else:
            base_speed_rpm_peak_motor = (peak_power_w / peak_torque) * 60 / (2 * np.pi)
            peak_motor_torque_curve = np.where(
                speeds_rpm_motor <= base_speed_rpm_peak_motor,
                peak_torque,
                peak_power_w / motor_omega,
            )

        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            # Uploaded curve: rated/continuous = uploaded peak / ratio (the
            # manual Peak Torque / Continuous Power entries don't describe
            # this motor; the ratio field remains the user's control).
            continuous_motor_torque_curve = peak_motor_torque_curve / ratio
        else:
            rated_torque_motor = peak_torque / ratio
            continuous_motor_torque_curve = np.minimum(rated_torque_motor, continuous_power_w / motor_omega)

        peak_wheel_torque_curve = peak_motor_torque_curve * wheel_drive_scale
        continuous_wheel_torque_curve = continuous_motor_torque_curve * wheel_drive_scale
        peak_force_curve = peak_wheel_torque_curve / wheel_radius
        continuous_force_curve = continuous_wheel_torque_curve / wheel_radius

        max_y_value = max(np.max(peak_force_curve), np.max(continuous_force_curve))

        # --- X-axis representation (shared with the torque view) --------------
        # Convert any vehicle-speed-in-km/h array to the selected x unit. km/h is
        # identical at wheel and motor (it's the road speed); RPM differs by the
        # gear ratio between wheel and motor.
        def kmh_to_x(v_kmh):
            v = np.asarray(v_kmh, dtype=float)
            if speed_unit == "RPM":
                rpm_wheel = (v / 3.6 / wheel_radius) * 60 / (2 * np.pi)
                return rpm_wheel * gear_ratio if plot_part == "At Motor" else rpm_wheel
            return v

        if speed_unit == "RPM":
            x_label = "Motor Speed (RPM)" if plot_part == "At Motor" else "Wheel Speed (RPM)"
            unit_text = "RPM"
        else:
            x_label = "Vehicle Speed (km/h)"
            unit_text = "km/h"

        x_peak = kmh_to_x(vehicle_speeds_kmh)

        self.ax.plot(x_peak, peak_force_curve, color=peak_c, linestyle=peak_ls, linewidth=peak_lw, label="Peak Force")
        self.ax.plot(x_peak, continuous_force_curve, color=cont_c, linestyle=cont_ls, linewidth=cont_lw, label="Continuous Force")

        def _annotate_force_intersections(speed_axis_kmh, force_curve, resistive_force, marker_color):
            # Intersections are found in the km/h domain (where the physics lives),
            # then the marker/label are placed in the selected x unit.
            speed_arr = np.asarray(speed_axis_kmh, dtype=float)
            force_arr = np.asarray(force_curve, dtype=float)
            resist_arr = np.asarray(resistive_force, dtype=float)
            diff = force_arr - resist_arr
            sign_change_idx = np.where(np.diff(np.sign(diff)) != 0)[0]
            found = []

            for idx in sign_change_idx:
                s1, s2 = speed_arr[idx], speed_arr[idx + 1]
                d1, d2 = diff[idx], diff[idx + 1]
                if d2 == d1:
                    intersection_speed = s1
                else:
                    intersection_speed = s1 - d1 * (s2 - s1) / (d2 - d1)
                intersection_force = np.interp(intersection_speed, speed_arr, force_arr)
                x_pt = float(kmh_to_x(intersection_speed))
                found.append(x_pt)
                self.ax.scatter(x_pt, intersection_force, color=marker_color, marker='o', zorder=5)
                self.ax.text(
                    x_pt,
                    float(intersection_force),
                    f"{x_pt:.1f} {unit_text}\n{float(intersection_force):.1f} N",
                    fontsize=9,
                    ha='left',
                    va='bottom',
                    color='black',
                    bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', pad=1.5),
                )
            return found

        x_resist = kmh_to_x(speeds)
        peak_int_x = []
        for gradient in gradients:
            wheel_forces = np.array(
                [
                    (
                        params['m_i'] * g * params['Crr'] * np.cos(np.arctan(gradient / 100))
                        + 0.5 * 1.225 * params['CdA'] * (s / 3.6) ** 2
                        + params['m_i'] * g * np.sin(np.arctan(gradient / 100))
                    )
                    for s in speeds
                ],
                dtype=float,
            )
            self.ax.plot(x_resist, wheel_forces, label=f"Gradient {gradient}%", linestyle=grad_ls, linewidth=grad_lw, **grad_ckw)

            peak_force_interp = np.interp(speeds, vehicle_speeds_kmh, peak_force_curve)
            continuous_force_interp = np.interp(speeds, vehicle_speeds_kmh, continuous_force_curve)
            peak_int_x += _annotate_force_intersections(speeds, peak_force_interp, wheel_forces, "red")
            _annotate_force_intersections(speeds, continuous_force_interp, wheel_forces, "darkorange")

        # Trim the dead space past the last useful intersection (auto mode only).
        # Cap against whichever x-limit entry matches the selected unit/part, so
        # the shown limit stays consistent with the axis (mirrors the torque view).
        if speed_unit == "RPM":
            if plot_part == "At Motor":
                _xmanual, _xentry = getattr(self, "xlim_rpm_motor_manual", False), getattr(self, "xlim_rpm_motor", None)
            else:
                _xmanual, _xentry = getattr(self, "xlim_rpm_vehicle_manual", False), getattr(self, "xlim_rpm_vehicle", None)
        else:
            _xmanual, _xentry = getattr(self, "xlim_manual", False), getattr(self, "xlim", None)
        x_limits = self._auto_cap_xlimits(x_limits, peak_int_x, _xmanual, _xentry)

        self.ax.set_title(f"Wheel Force vs {x_label}", fontsize=16, weight='bold')
        self.ax.set_xlabel(x_label, fontsize=14)
        self.ax.set_ylabel("Wheel Force (N)", fontsize=14)
        self.ax.set_xlim(x_limits)
        self.ax.set_ylim(0, max(y_limits[1], max_y_value * 1.1))
        self.ax.legend()
        # Apply the Graph Settings (grid on/off, per-axis spacing, legend, sizes)
        # here too so live setting changes always take effect for this view.
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()
        self.canvas.draw()
        

    def plot_vehicle_max_speed_vs_time(self, speeds, params, wheel_radius, peak_torque, peak_power,gear_ratio):
        """Plots vehicle speed vs. time as it accelerates with max available force."""
        if hasattr(self, "heatmap_colorbar") and self.heatmap_colorbar is not None:
            self.heatmap_colorbar.remove()
            self.heatmap_colorbar = None
        self.ax.clear()  # Clear previous plot

        # Per-line appearance from the Graph Settings panel (defaults = original).
        speed_c = self.gs_color("speed_color", "black")
        speed_ls = self.gs_linestyle("speed_style", "--")
        speed_lw = self.gs_float("speed_width", 2.0)

        # Convert speeds to m/s
        speeds_mps = np.array(speeds) / 3.6  # km/h to m/s
        speeds_rpm = ((speeds_mps / wheel_radius) * 60 / (2 * np.pi))*gear_ratio  # Convert to motor RPM

        # Convert power to watts
        peak_power_w = peak_power * 1000

        # --- Torque Calculation: Use Motor Data if available ---
        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            # Interpolate torque from uploaded motor data
            df = self.motor_dataframe
            # Ensure sorted by speed
            df_sorted = df.sort_values("motor_speed")
            interp_torque = np.interp(
                speeds_rpm,
                df_sorted["motor_speed"].values,
                df_sorted["motor_torque"].values,
                left=df_sorted["motor_torque"].values[0],
                right=df_sorted["motor_torque"].values[-1]
            )
            torque_values = interp_torque
        else:
            # Theoretical calculation
            base_speed_rad_s = peak_power_w / peak_torque  # Base speed in rad/s
            base_speed_rpm = (base_speed_rad_s * 60) / (2 * np.pi)  # Convert rad/s to RPM
            torque_values = np.where(
                speeds_rpm < base_speed_rpm, 
                peak_torque, 
                peak_power_w / ((speeds_rpm * 2 * np.pi) / 60)  # T = P / Ï‰
            )

        # Calculate max wheel force (includes gear efficiency)
        gear_eff = self.get_gear_efficiency_value()
        max_wheel_force = np.array(torque_values * gear_ratio * gear_eff / wheel_radius)

        # Compute wheel resistance forces
        wheel_forces = np.array([
            params['m_i'] * g * params['Crr'] +
            0.5 * 1.225 * params['CdA'] * (s ** 2) +
            params['m_i'] * g * np.sin(np.arctan(params.get('gradient', 0) / 100))
            for s in speeds_mps
        ])
        net_force = max_wheel_force - wheel_forces  # Available force for acceleration

        # Compute maximum acceleration
        max_acceleration = net_force / params['m_i']

        dt = 0.001  # seconds
        max_time = float(self.max_time.get())  # Maximum simulation time in seconds
        time_steps = np.arange(0, max_time, dt)  # Time array

        # Initialize velocity array
        velocity = [0]  # Start from rest

        # Perform numerical integration
        for t in time_steps[:-1]:  # Iterate through time steps
            current_speed = velocity[-1]  # Get current velocity

            # Find the closest speed in speeds_mps array
            closest_idx = np.argmin(np.abs(speeds_mps - current_speed))
            
            # Get corresponding acceleration
            current_acceleration = max_acceleration[closest_idx]

            # Update velocity
            new_velocity = velocity[-1] + current_acceleration * dt
            
            # Ensure velocity does not exceed max speed
            if new_velocity >= speeds_mps[-1]:  
                break

            velocity.append(new_velocity)

        # Convert time and velocity to numpy arrays
        velocity = np.array(velocity)
        velocity_kmh = np.array(velocity) * 3.6  
        time_values = np.array(time_steps[:len(velocity)])


        speed_target = float(self.target_speed.get())
        index_60 = np.where(velocity_kmh >= speed_target)[0]  # Get all indices where speed >= target

        if len(index_60) > 0:
            time_60 = time_values[index_60[0]]  # First time when speed reaches target

            # Plot the velocity curve
            self.ax.plot(time_values, velocity_kmh, color=speed_c, linestyle=speed_ls, linewidth=speed_lw, label="Vehicle Speed")

            # Mark the target speed point on the plot
            self.ax.axvline(x=time_60, color='red', linestyle='--', label=f"{speed_target} km/h at {time_60:.1f}s")
            self.ax.scatter(time_60, speed_target, color='red', zorder=3)  # Highlight the exact point

            # Annotate the point
            self.ax.text(time_60, speed_target + 5, f"{time_60:.1f}s", color='red', fontsize=10)
            # Add horizontal line from y-axis to the intersection point
            self.ax.axhline(y=speed_target, color='blue', linestyle='--', label=f"{speed_target} km/h")
            self.ax.text(0.5, speed_target + 2, f"{speed_target:.0f} km/h", color='blue', fontsize=10)
        else:
            messagebox.showerror("Speed Error", "Vehicle never reaches " + str(float(self.target_speed.get())) + "km/h in the given simulation.")

        self.ax.plot(time_values, velocity_kmh, color=speed_c, linestyle=speed_ls, linewidth=speed_lw, label="Vehicle Speed")
        final_velocity = velocity_kmh[-1]
        final_time = time_values[-1]
        self.ax.scatter(final_time, final_velocity, color='green', zorder=3, label=f"Final: {final_velocity:.1f} km/h at {final_time:.1f}s")
        self.ax.text(final_time, final_velocity + 2, f"{final_velocity:.1f} km/h", color='green', fontsize=10)

        # Formatting
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Vehicle Speed (km/h)")
        self.ax.legend()
        self.ax.set_title("Vehicle Speed vs. Time under Max Acceleration")
        # Grid/legend/spacing owned by Graph Settings; apply here so live
        # changes take effect for the Acceleration view.
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()

        self.canvas.draw()  # Refresh plot

