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



class EngineMixin:

    def _extract_rpm_eff_columns(self, df_sheet):
        if df_sheet is None or df_sheet.empty:
            return np.array([]), np.array([])

        columns = list(df_sheet.columns)
        lower = {col: str(col).strip().lower() for col in columns}
        rpm_col = next((col for col in columns if "rpm" in lower[col]), None)
        eff_col = next((col for col in columns if ("eff" in lower[col] or "eta" in lower[col])), None)

        if rpm_col is None or eff_col is None:
            numeric_cols = []
            for col in columns:
                numeric_series = pd.to_numeric(df_sheet[col], errors='coerce')
                if numeric_series.notna().sum() > 0:
                    numeric_cols.append(col)
            if len(numeric_cols) < 2:
                return np.array([]), np.array([])
            if rpm_col is None:
                rpm_col = numeric_cols[0]
            if eff_col is None:
                eff_col = numeric_cols[1] if numeric_cols[1] != rpm_col else numeric_cols[-1]

        parsed = pd.DataFrame({
            "rpm": pd.to_numeric(df_sheet[rpm_col], errors='coerce'),
            "eff": pd.to_numeric(df_sheet[eff_col], errors='coerce'),
        }).dropna()

        if parsed.empty:
            return np.array([]), np.array([])

        parsed = parsed.sort_values("rpm")
        rpm_vals = parsed["rpm"].to_numpy(dtype=float)
        eff_vals = parsed["eff"].to_numpy(dtype=float)
        if np.nanmax(np.abs(eff_vals)) > 1.5:
            eff_vals = eff_vals / 100.0
        eff_vals = np.clip(eff_vals, 0.0, 1.0)
        return rpm_vals, eff_vals


    def _get_engine_gear_ratios(self):
        ratio_entries = [
            self.engine_gear_ratio_1,
            self.engine_gear_ratio_2,
            self.engine_gear_ratio_3,
            self.engine_gear_ratio_4,
            self.engine_gear_ratio_5,
            self.engine_gear_ratio_6,
        ]
        ratios = []
        for entry in ratio_entries:
            try:
                value = float(entry.get().strip())
            except Exception:
                value = 0.0
            if value <= 0:
                value = 0.0
            ratios.append(value)
        return ratios


    def _get_engine_efficiency_values(self, gear_idx, rpm_values):
        rpm_values = np.asarray(rpm_values, dtype=float)
        if gear_idx not in self.engine_efficiency_curves:
            return np.ones_like(rpm_values, dtype=float)

        eff_curve = self.engine_efficiency_curves[gear_idx]
        rpm_arr = np.asarray(eff_curve.get("rpm", []), dtype=float)
        eff_arr = np.asarray(eff_curve.get("eff", []), dtype=float)
        if len(rpm_arr) == 0 or len(eff_arr) == 0:
            return np.ones_like(rpm_values, dtype=float)

        rpm_unique, unique_indices = np.unique(rpm_arr, return_index=True)
        eff_unique = eff_arr[unique_indices]
        if len(rpm_unique) < 2:
            return np.ones_like(rpm_values, dtype=float)

        spline = eff_curve.get("spline")
        if spline is None or eff_curve.get("spline_points") != len(rpm_unique):
            try:
                k = min(3, len(rpm_unique) - 1)
                spline = UnivariateSpline(rpm_unique, eff_unique, s=0.001, k=k)
            except Exception:
                spline = None
            eff_curve["spline"] = spline
            eff_curve["spline_points"] = len(rpm_unique)

        if spline is not None:
            eff_vals = spline(rpm_values)
        else:
            eff_vals = np.interp(
                rpm_values,
                rpm_unique,
                eff_unique,
                left=eff_unique[0],
                right=eff_unique[-1],
            )
        return np.clip(np.asarray(eff_vals, dtype=float), 0.0, 1.0)


    def _sync_engine_curve_to_motor_inputs(self):
        """
        Keep engine-analysis uploads isolated from motor-analysis inputs.
        This sync now updates only Peak Torque / Peak Power input fields from
        engine data; it does NOT populate motor curve data.
        """
        # Cleanup any legacy engine-sourced motor curve state.
        if self.motor_curve_source == "engine":
            self.motor_dataframe = None
            self.motor_curve_source = None
            self.motor_data_indicator.configure(text="\u274C", text_color=COLORS['warning'])
            self.motor_data_delete_button.configure(state="disabled")

        if self.engine_dataframe is None or self.engine_dataframe.empty:
            return

        engine_df = self.engine_dataframe.sort_values("engine_rpm")
        engine_rpm = engine_df["engine_rpm"].to_numpy(dtype=float)
        engine_torque = engine_df["engine_torque"].to_numpy(dtype=float)

        gear_ratios = self._get_engine_gear_ratios()
        active_gears = [(idx + 1, ratio) for idx, ratio in enumerate(gear_ratios) if ratio > 0]

        # Keep backward-compatible behavior for this field:
        # peak torque is derived as max available wheel-side peak torque.
        if active_gears:
            wheel_torque_peaks = []
            for gear_idx, ratio in active_gears:
                eff_vals = self._get_engine_efficiency_values(gear_idx, engine_rpm)
                wheel_torque_peaks.append(float(np.nanmax(engine_torque * ratio * eff_vals)))
            peak_torque_val = float(np.nanmax(wheel_torque_peaks))
        else:
            peak_torque_val = float(np.nanmax(engine_torque))

        power_kw = (engine_torque * engine_rpm * 2 * np.pi / 60.0) / 1000.0
        peak_power_val = float(np.nanmax(power_kw))

        self.peak_torque.delete(0, "end")
        self.peak_torque.insert(0, f"{peak_torque_val:.2f}")
        self.peak_power.delete(0, "end")
        self.peak_power.insert(0, f"{peak_power_val:.2f}")


    def plot_engine_analysis(self):
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()
        self.engine_results_label.configure(text="")

        if self.engine_dataframe is None or self.engine_dataframe.empty:
            self.show_placeholder_message("Upload Engine Torque-RPM data to view Engine analysis.")
            return

        try:
            wheel_radius = float(self.wheel_radius.get())
            if wheel_radius <= 0:
                raise ValueError("Wheel radius must be > 0.")

            theoretical_gear_ratio = float(self.gear_ratio.get())
            if theoretical_gear_ratio <= 0:
                theoretical_gear_ratio = 1.0

            peak_torque = float(self.peak_torque.get())
            peak_power = float(self.peak_power.get())
            if peak_torque <= 0 or peak_power <= 0:
                raise ValueError("Peak torque and peak power must be > 0.")

            m_ref = float(self.m_ref.get())
            rear_load_ratio = float(self.rear_load_ratio.get())
            ambient_temp = float(self.ambient_temp.get())
            ambient_pressure = float(self.ambient_pressure.get())
            crr = float(self.crr.get()) if self.crr.get().strip() else None
            cd_a = float(self.cd_a.get()) if self.cd_a.get().strip() else None

            params = calculate_crr_cd_a(
                m_ref,
                rear_load_ratio,
                ambient_temp,
                ambient_pressure,
                crr=crr if self.crr_manual else None,
                cd_a=cd_a if self.cda_manual else None,
            )
        except Exception as exc:
            messagebox.showerror(
                "Input Error",
                str(exc),
            )
            return

        try:
            gradients = [float(grad.strip()) for grad in self.gradients.get().split(",") if grad.strip()]
        except Exception:
            gradients = []
        if not gradients:
            gradients = [0.0]

        gear_ratios = self._get_engine_gear_ratios()
        active_gears = [(idx + 1, ratio) for idx, ratio in enumerate(gear_ratios) if ratio > 0]
        if not active_gears:
            self.show_placeholder_message("Enter at least one gear ratio > 0 for Engine analysis.")
            return

        df_engine = self.engine_dataframe.sort_values("engine_rpm")
        engine_rpm = df_engine["engine_rpm"].to_numpy(dtype=float)
        engine_torque = df_engine["engine_torque"].to_numpy(dtype=float)
        if len(engine_rpm) == 0:
            self.show_placeholder_message("Engine curve is empty.")
            return
        plot_force = "Force" in self.engine_output_combo.get()
        y_label = "Wheel Force (N)" if plot_force else "Wheel Torque (Nm)"
        y_unit = "N" if plot_force else "Nm"
        x_axis_label = "Vehicle Speed (km/h)"
        gear_eff = self.get_gear_efficiency_value()

        peak_power_w = peak_power * 1000.0
        base_speed_rpm_peak = (peak_power_w / peak_torque) * 60 / (2 * np.pi)

        # Engine gear lines use uploaded engine torque data.
        # Main intersection line uses theoretical peak curve from peak torque/power.
        overall_max_force = 0.0
        overall_max_torque = 0.0
        overall_max_power = 0.0
        max_vehicle_speed = 0.0
        gear_summary = []
        gear_curves = []

        color_map = plt.get_cmap("tab10")
        for color_idx, (gear_idx, ratio) in enumerate(active_gears):
            color = color_map(color_idx % 10)
            eff_vals = self._get_engine_efficiency_values(gear_idx, engine_rpm)

            wheel_torque_peak = engine_torque * ratio * eff_vals
            wheel_force_peak = wheel_torque_peak / wheel_radius
            wheel_rpm = engine_rpm / ratio
            wheel_omega = wheel_rpm * (2 * np.pi / 60.0)
            wheel_speed_kmph = wheel_omega * wheel_radius * 3.6
            wheel_power_kw = (wheel_torque_peak * wheel_omega) / 1000.0

            speed_order = np.argsort(wheel_speed_kmph)
            wheel_speed_kmph = wheel_speed_kmph[speed_order]
            wheel_torque_peak = wheel_torque_peak[speed_order]
            wheel_force_peak = wheel_force_peak[speed_order]

            peak_curve_y = wheel_force_peak if plot_force else wheel_torque_peak

            self.ax.plot(
                wheel_speed_kmph,
                peak_curve_y,
                linewidth=2.3,
                color=color,
                label=f"G{gear_idx} (GR={ratio:g})",
            )

            gear_curves.append(
                {
                    "gear_idx": gear_idx,
                    "speed": wheel_speed_kmph,
                    "peak_y": peak_curve_y,
                    "color": color,
                }
            )

            max_force = float(np.nanmax(wheel_force_peak))
            max_torque = float(np.nanmax(wheel_torque_peak))
            max_power = float(np.nanmax(wheel_power_kw))
            overall_max_force = max(overall_max_force, max_force)
            overall_max_torque = max(overall_max_torque, max_torque)
            overall_max_power = max(overall_max_power, max_power)
            max_vehicle_speed = max(max_vehicle_speed, float(np.nanmax(wheel_speed_kmph)))
            gear_summary.append(
                f"G{gear_idx}: Max T {max_torque:.1f} Nm, Max F {max_force:.1f} N, Max P {max_power:.2f} kW"
            )

        def _theoretical_peak_curve(speed_kmh_vals):
            speed_kmh_vals = np.asarray(speed_kmh_vals, dtype=float)
            motor_rpm_theoretical = (
                speed_kmh_vals * 60.0 / (2 * np.pi * wheel_radius * 3.6)
            ) * theoretical_gear_ratio
            motor_omega_theoretical = np.maximum((motor_rpm_theoretical * 2 * np.pi) / 60.0, 1e-6)
            motor_torque_theoretical = np.where(
                motor_rpm_theoretical <= base_speed_rpm_peak,
                peak_torque,
                peak_power_w / motor_omega_theoretical,
            )
            wheel_torque_theoretical = motor_torque_theoretical * theoretical_gear_ratio * gear_eff
            return wheel_torque_theoretical / wheel_radius if plot_force else wheel_torque_theoretical

        # Expand x-range until the theoretical peak curve reaches top speed
        # (intersection with the easiest/lowest-gradient resistive curve).
        reference_gradient = min(gradients) if gradients else 0.0
        search_speed_max = max(float(max_vehicle_speed), 120.0)
        top_speed_estimate = None
        for _ in range(9):
            probe_speed = np.linspace(0.0, search_speed_max, 2400)
            probe_peak = _theoretical_peak_curve(probe_speed)
            theta_ref = np.arctan(float(reference_gradient) / 100.0)
            probe_resist_force = (
                params['m_i'] * g * params['Crr'] * np.cos(theta_ref)
                + 0.5 * 1.225 * params['CdA'] * (probe_speed / 3.6) ** 2
                + params['m_i'] * g * np.sin(theta_ref)
            )
            probe_resist = probe_resist_force if plot_force else probe_resist_force * wheel_radius
            diff_probe = probe_peak - probe_resist
            sign_change_idx = np.where(np.diff(np.sign(diff_probe)) != 0)[0]
            if len(sign_change_idx) > 0:
                idx = int(sign_change_idx[-1])
                x1, x2 = float(probe_speed[idx]), float(probe_speed[idx + 1])
                d1, d2 = float(diff_probe[idx]), float(diff_probe[idx + 1])
                if d2 == d1:
                    top_speed_estimate = x1
                else:
                    top_speed_estimate = x1 - d1 * (x2 - x1) / (d2 - d1)
                break
            if search_speed_max >= 450.0:
                break
            search_speed_max *= 1.35

        if top_speed_estimate is not None and np.isfinite(top_speed_estimate):
            plot_speed_max = max(float(max_vehicle_speed), float(top_speed_estimate) * 1.08)
        else:
            plot_speed_max = max(float(max_vehicle_speed), float(search_speed_max))
        plot_speed_max = float(np.clip(plot_speed_max, 20.0, 450.0))

        resistive_curves = {}
        resistive_speed = np.linspace(0.0, plot_speed_max, 1200)
        for gradient in gradients:
            theta = np.arctan(float(gradient) / 100.0)
            resist_force = (
                params['m_i'] * g * params['Crr'] * np.cos(theta)
                + 0.5 * 1.225 * params['CdA'] * (resistive_speed / 3.6) ** 2
                + params['m_i'] * g * np.sin(theta)
            )
            resist_curve_y = resist_force if plot_force else resist_force * wheel_radius
            curve_kind = "Force" if plot_force else "Torque"
            self.ax.plot(
                resistive_speed,
                resist_curve_y,
                linestyle="-.",
                linewidth=1.5,
                label=f"Resistive {curve_kind} @ {gradient:g}%",
            )
            resistive_curves[gradient] = (resistive_speed.copy(), np.asarray(resist_curve_y, dtype=float))

        # Build main theoretical peak curve using the same logic as Analysis tab:
        # hold peak torque until base speed, then reduce torque to maintain peak power.
        # Convert to wheel side using the single vehicle gear ratio (not engine gear list).
        main_peak_speed = np.linspace(0.0, plot_speed_max, 1600)
        main_peak_y = _theoretical_peak_curve(main_peak_speed)

        main_valid = np.isfinite(main_peak_y)
        if np.any(main_valid):
            main_speed_valid = main_peak_speed[main_valid]
            main_peak_valid = main_peak_y[main_valid]
            self.ax.plot(
                main_speed_valid,
                main_peak_valid,
                linestyle='--',
                linewidth=2.6,
                color='black',
                label=f"Peak {y_label} ",
                zorder=6,
            )

            for gradient in gradients:
                resist_x, resist_y = resistive_curves[gradient]
                resist_interp = np.interp(main_speed_valid, resist_x, resist_y, left=resist_y[0], right=resist_y[-1])
                self._annotate_intersections(
                    main_speed_valid,
                    main_peak_valid,
                    resist_interp,
                    x_axis_label,
                    y_unit,
                    'black',
                    marker_style='o',
                    show_text=True,
                )

        self.ax.scatter([], [], color='black', marker='o', label='Peak vs Resistive Intersection')

        self.ax.set_title(f"Engine Analysis: {y_label} vs Vehicle Speed", fontsize=16, weight='bold')
        self.ax.set_xlabel(x_axis_label, fontsize=14)
        self.ax.set_ylabel(y_label, fontsize=14)
        self.ax.set_xlim(0.0, plot_speed_max)
        self.ax.grid(True, linestyle='--', alpha=0.7)
        self.ax.legend(loc="best", fontsize=8.5, ncol=2)

        summary_text = (
            f"Overall Max Wheel Torque: {overall_max_torque:.1f} Nm\n"
            f"Overall Max Wheel Force: {overall_max_force:.1f} N\n"
            f"Overall Max Wheel Power: {overall_max_power:.2f} kW\n"
            + "\n".join(gear_summary)
        )
        self.engine_results_label.configure(text=summary_text)
        self.canvas.draw()

