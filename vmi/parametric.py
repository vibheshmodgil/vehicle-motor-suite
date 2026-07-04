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



class ParametricMixin:

    def _parse_range_triplet(self, text_value, label):
        parts = [p.strip() for p in text_value.split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError(f"{label} must be in 'min,max,step' format.")
        min_val, max_val, step_val = map(float, parts)
        if step_val <= 0:
            raise ValueError(f"{label} step must be > 0.")
        if max_val < min_val:
            raise ValueError(f"{label} max must be >= min.")
        values = np.arange(min_val, max_val + (step_val * 0.5), step_val)
        if len(values) == 0:
            raise ValueError(f"{label} produced an empty range.")
        return values


    def _parse_speed_sweep(self, text_value):
        parts = [p.strip() for p in text_value.split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError("Speed Sweep must be in 'min,max,points' format.")
        min_speed = float(parts[0])
        max_speed = float(parts[1])
        n_points = int(float(parts[2]))
        if min_speed < 0 or max_speed <= min_speed:
            raise ValueError("Speed Sweep requires 0 <= min < max.")
        if n_points < 100:
            raise ValueError("Speed Sweep points must be at least 100.")
        return np.linspace(min_speed, max_speed, n_points)


    def _compute_available_wheel_force(self, speeds_kmh, wheel_radius, peak_torque, peak_power, gear_ratio):
        speeds_kmh = np.asarray(speeds_kmh, dtype=float)
        speeds_rpm_wheel = speeds_kmh * 60 / (2 * np.pi * wheel_radius) / 3.6
        speeds_rpm_motor = speeds_rpm_wheel * gear_ratio
        motor_omega = np.maximum((speeds_rpm_motor * 2 * np.pi) / 60, 1e-6)
        gear_eff = self.get_gear_efficiency_value()

        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            df_sorted = self.motor_dataframe.sort_values("motor_speed")
            motor_torque = np.interp(
                speeds_rpm_motor,
                df_sorted["motor_speed"].values,
                df_sorted["motor_torque"].values,
                left=df_sorted["motor_torque"].values[0],
                right=df_sorted["motor_torque"].values[-1],
            )
        else:
            peak_power_w = peak_power * 1000
            base_speed_rpm_peak = (peak_power_w / peak_torque) * 60 / (2 * np.pi)
            motor_torque = np.where(
                speeds_rpm_motor <= base_speed_rpm_peak,
                peak_torque,
                peak_power_w / motor_omega,
            )

        wheel_torque = motor_torque * gear_ratio * gear_eff
        wheel_force = wheel_torque / wheel_radius
        return wheel_force


    def _compute_resistive_force(self, speeds_kmh, mass_kg, crr, cda, gradient_pct):
        speeds_kmh = np.asarray(speeds_kmh, dtype=float)
        speed_mps = speeds_kmh / 3.6
        theta = np.arctan(float(gradient_pct) / 100.0)
        rolling = mass_kg * g * crr * np.cos(theta)
        aero = 0.5 * 1.225 * cda * speed_mps ** 2
        grade = mass_kg * g * np.sin(theta)
        return rolling + aero + grade


    def _estimate_top_speed(self, speeds_kmh, wheel_force_curve, mass_kg, crr, cda):
        speeds_kmh = np.asarray(speeds_kmh, dtype=float)
        resist = self._compute_resistive_force(speeds_kmh, mass_kg, crr, cda, gradient_pct=0.0)
        margin = np.asarray(wheel_force_curve, dtype=float) - resist
        feasible = margin >= 0
        # Cannot overcome resistance at the lowest sweep speed -> can't launch.
        if not feasible[0]:
            return 0.0
        # Still feasible at the top of the sweep -> top speed is capped by sweep.
        if feasible.all():
            return float(speeds_kmh[-1])
        # Top speed is the FIRST positive->negative crossing: the vehicle stops
        # accelerating there. Using the first crossing (rather than the last
        # feasible point) avoids reporting an unreachable speed beyond an
        # infeasible dip in a non-monotonic (uploaded) motor curve. For the
        # default monotonic model the two coincide.
        first_neg = int(np.argmax(~feasible))  # >= 1 given the checks above
        last_idx = first_neg - 1
        x1, x2 = float(speeds_kmh[last_idx]), float(speeds_kmh[first_neg])
        y1, y2 = float(margin[last_idx]), float(margin[first_neg])
        if y2 == y1:
            return x1
        return x1 - y1 * (x2 - x1) / (y2 - y1)


    def _estimate_max_gradability(self, speeds_kmh, wheel_force_curve, mass_kg, crr, cda, grad_max, grad_step):
        grad_values = np.arange(0.0, grad_max + (grad_step * 0.5), grad_step)
        best_grad = 0.0
        for grad_pct in grad_values:
            resist = self._compute_resistive_force(speeds_kmh, mass_kg, crr, cda, gradient_pct=grad_pct)
            if np.max(np.asarray(wheel_force_curve, dtype=float) - resist) >= 0:
                best_grad = float(grad_pct)
            else:
                break
        return best_grad


    def _estimate_acceleration_time(
        self,
        speeds_kmh,
        wheel_force_curve,
        mass_kg,
        crr,
        cda,
        target_speed_kmh,
        max_time_s,
        dt_s=0.05,
    ):
        target_speed_kmh = float(target_speed_kmh)
        if target_speed_kmh <= 0:
            return 0.0
        if max_time_s <= 0:
            return np.nan

        speed_grid = np.asarray(speeds_kmh, dtype=float)
        force_grid = np.asarray(wheel_force_curve, dtype=float)
        current_speed_kmh = 0.0
        elapsed_time_s = 0.0

        while elapsed_time_s <= max_time_s:
            if current_speed_kmh >= target_speed_kmh:
                return elapsed_time_s

            available_force = float(
                np.interp(
                    current_speed_kmh,
                    speed_grid,
                    force_grid,
                    left=force_grid[0],
                    right=force_grid[-1],
                )
            )
            resist_force = float(
                self._compute_resistive_force(
                    np.array([current_speed_kmh], dtype=float),
                    mass_kg,
                    crr,
                    cda,
                    gradient_pct=0.0,
                )[0]
            )
            net_force = available_force - resist_force
            acceleration = net_force / mass_kg
            if acceleration <= 0:
                return np.nan

            current_speed_mps = current_speed_kmh / 3.6
            current_speed_mps += acceleration * dt_s
            current_speed_kmh = current_speed_mps * 3.6
            elapsed_time_s += dt_s

        return np.nan


    def _mark_current_param_1d(self, values, current_val, y_values, param_symbol):
        """Highlight where the vehicle's currently configured CdA/Crr (the
        value actually used everywhere else in the app, held constant while
        the OTHER parameter is swept) falls on a 1D parametric line, so the
        user isn't looking at an abstract curve with no sense of where their
        real vehicle sits on it. Draws a vertical reference line (always) plus
        a star marker on the curve itself (only when the current value falls
        inside the swept range -- outside it, the line is still drawn but
        labeled so, since there's no curve point to mark)."""
        values = np.asarray(values, dtype=float)
        lo, hi = float(np.min(values)), float(np.max(values))
        in_range = lo <= current_val <= hi
        xlim = self.ax.get_xlim()  # axvline can otherwise stretch the x-axis
        label = f"Current {param_symbol} = {current_val:.4g}"
        if not in_range:
            label += " (outside sweep)"
        self.ax.axvline(current_val, color='crimson', linestyle=':', linewidth=1.8,
                         zorder=4, label=label)
        if in_range and y_values is not None:
            y_here = float(np.interp(current_val, values, np.asarray(y_values, dtype=float)))
            self.ax.plot(current_val, y_here, marker='*', markersize=14, color='crimson',
                         markeredgecolor='black', markeredgewidth=0.6, zorder=6)
        self.ax.set_xlim(xlim)

    def _mark_current_param_2d(self, current_cda, current_crr):
        """Same idea as `_mark_current_param_1d` but for the CdA & Crr contour
        maps: marks the (CdA, Crr) point the rest of the app is actually using
        right now. If it falls outside the swept grid there's nowhere sensible
        to put a marker without distorting the axes, so a text note in the
        corner states the current values instead."""
        cda_lo, cda_hi = self.ax.get_xlim()
        crr_lo, crr_hi = self.ax.get_ylim()
        if cda_lo <= current_cda <= cda_hi and crr_lo <= current_crr <= crr_hi:
            self.ax.plot(current_cda, current_crr, marker='*', markersize=18, color='white',
                         markeredgecolor='black', markeredgewidth=1.3, zorder=6,
                         label="Current CdA & Crr")
            self.ax.annotate(
                "Current", xy=(current_cda, current_crr), xytext=(8, 8),
                textcoords='offset points', color='black', fontsize=10, fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.2', fc='white', ec='black', alpha=0.85),
            )
        else:
            self.ax.text(
                0.02, 0.98,
                f"Current CdA={current_cda:.4g}, Crr={current_crr:.4g} (outside plotted range)",
                transform=self.ax.transAxes, fontsize=9, va='top', ha='left', color='crimson',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='crimson', alpha=0.9),
            )

    def _draw_parametric_contour(self, cda_values, crr_values, value_map, title, cbar_label,
                                  current_cda=None, current_crr=None):
        """Filled contour of a CdA x Crr result grid, with the black line overlay
        and colorbar. Fails soft: if every cell is NaN/constant (e.g. the motor
        can't reach the target anywhere) it shows a message instead of letting
        matplotlib raise on an all-masked / zero-range array."""
        value_map = np.asarray(value_map, dtype=float)
        finite = np.isfinite(value_map)
        if not finite.any():
            self.show_placeholder_message(
                "No feasible result over this CdA/Crr range.\n"
                "Widen the ranges or check motor/vehicle inputs."
            )
            return

        cda_grid, crr_grid = np.meshgrid(cda_values, crr_values)
        masked = np.ma.masked_invalid(value_map)
        vmin = float(np.nanmin(value_map))
        vmax = float(np.nanmax(value_map))

        # Number of filled bands / contour lines are user-controllable via the
        # Graph Settings panel (defaults reproduce the original 20 / 10).
        fill_levels = max(2, self.gs_int('fill_levels', 20)) if hasattr(self, 'gs_int') else 20
        line_levels = max(1, self.gs_int('line_levels', 10)) if hasattr(self, 'gs_int') else 10
        cmap = self.gs_str('cmap', 'viridis') if hasattr(self, 'gs_str') else 'viridis'
        # contourf needs a non-zero range; give a flat map a tiny band.
        levels = fill_levels if vmax > vmin else np.linspace(vmin - 0.5, vmax + 0.5, 3)

        contour = self.ax.contourf(cda_grid, crr_grid, masked, levels=levels, cmap=cmap)
        self.parametric_colorbar = self.figure.colorbar(
            contour, ax=self.ax, fraction=0.035, pad=0.02, shrink=0.92
        )
        self.parametric_colorbar.set_label(cbar_label, fontsize=12)
        if vmax > vmin:
            self.ax.contour(cda_grid, crr_grid, masked, colors='black', linewidths=0.5, levels=line_levels)
        self.ax.set_title(title, fontsize=16, weight='bold')
        self.ax.set_xlabel("CdA (m^2)", fontsize=14)
        self.ax.set_ylabel("Crr", fontsize=14)
        self.ax.grid(True, linestyle='--', alpha=0.5)
        if current_cda is not None and current_crr is not None:
            self._mark_current_param_2d(current_cda, current_crr)

    def plot_parametric_study(self, params, wheel_radius, peak_torque, peak_power, gear_ratio):
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self.ax.clear()

        try:
            graph_type = self.parametric_graph_combo.get()
            cda_values = self._parse_range_triplet(self.param_cda_range.get().strip(), "CdA Range")
            crr_values = self._parse_range_triplet(self.param_crr_range.get().strip(), "Crr Range")
            speed_sweep_kmh = self._parse_speed_sweep(self.param_speed_sweep.get().strip())
            accel_target_speed = float(self.param_accel_target_speed.get().strip())
            accel_max_time = float(self.param_accel_max_time.get().strip())
            grad_max = float(self.param_grad_max.get().strip())
            grad_step = float(self.param_grad_step.get().strip())
            # Only validate the inputs the selected graph actually uses, so a
            # leftover value in an unused field can't block an unrelated plot.
            is_accel = "Acceleration Time" in graph_type
            is_grad = "Gradability" in graph_type
            if is_grad and (grad_max <= 0 or grad_step <= 0):
                raise ValueError("Gradient search values must be > 0.")
            if is_accel:
                if accel_target_speed <= 0 or accel_max_time <= 0:
                    raise ValueError("Acceleration inputs must be > 0.")
                if accel_target_speed > float(np.max(speed_sweep_kmh)):
                    raise ValueError("Accel Target Speed must be <= Speed Sweep max.")
        except Exception as exc:
            messagebox.showerror("Parametric Input Error", str(exc))
            self.show_placeholder_message("Fix Parametric Study inputs and click Update Plot.")
            return

        wheel_force_curve = self._compute_available_wheel_force(
            speed_sweep_kmh, wheel_radius, peak_torque, peak_power, gear_ratio
        )
        mass_kg = float(params['m_i'])
        base_cda = float(params['CdA'])
        base_crr = float(params['Crr'])

        if graph_type == "Effect of CdA on Top Speed":
            top_speeds = [
                self._estimate_top_speed(speed_sweep_kmh, wheel_force_curve, mass_kg, base_crr, cda)
                for cda in cda_values
            ]
            self.ax.plot(cda_values, top_speeds, marker='o', color=COLORS['primary'], linewidth=2.0)
            self.ax.set_title("Effect of CdA on Top Speed", fontsize=16, weight='bold')
            self.ax.set_xlabel("CdA (m^2)", fontsize=14)
            self.ax.set_ylabel("Top Speed (km/h)", fontsize=14)
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self._mark_current_param_1d(cda_values, base_cda, top_speeds, "CdA")

        elif graph_type == "Effect of Crr on Top Speed":
            top_speeds = [
                self._estimate_top_speed(speed_sweep_kmh, wheel_force_curve, mass_kg, crr, base_cda)
                for crr in crr_values
            ]
            self.ax.plot(crr_values, top_speeds, marker='o', color=COLORS['secondary'], linewidth=2.0)
            self.ax.set_title("Effect of Crr on Top Speed", fontsize=16, weight='bold')
            self.ax.set_xlabel("Crr", fontsize=14)
            self.ax.set_ylabel("Top Speed (km/h)", fontsize=14)
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self._mark_current_param_1d(crr_values, base_crr, top_speeds, "Crr")

        elif graph_type == "Effect of CdA & Crr on Top Speed":
            top_speed_map = np.full((len(crr_values), len(cda_values)), np.nan, dtype=float)
            for i, crr in enumerate(crr_values):
                for j, cda in enumerate(cda_values):
                    top_speed_map[i, j] = self._estimate_top_speed(
                        speed_sweep_kmh, wheel_force_curve, mass_kg, crr, cda
                    )
            self._draw_parametric_contour(
                cda_values, crr_values, top_speed_map,
                title="Effect of CdA & Crr on Top Speed",
                cbar_label="Top Speed (km/h)",
                current_cda=base_cda, current_crr=base_crr,
            )

        elif graph_type == "Effect of CdA on Acceleration Time":
            accel_times = [
                self._estimate_acceleration_time(
                    speed_sweep_kmh,
                    wheel_force_curve,
                    mass_kg,
                    base_crr,
                    cda,
                    accel_target_speed,
                    accel_max_time,
                )
                for cda in cda_values
            ]
            self.ax.plot(cda_values, accel_times, marker='o', color=COLORS['accent'], linewidth=2.0)
            self.ax.set_title(f"Effect of CdA on 0-{accel_target_speed:.0f} km/h Time", fontsize=16, weight='bold')
            self.ax.set_xlabel("CdA (m^2)", fontsize=14)
            self.ax.set_ylabel("Acceleration Time (s)", fontsize=14)
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self._mark_current_param_1d(cda_values, base_cda, accel_times, "CdA")

        elif graph_type == "Effect of Crr on Acceleration Time":
            accel_times = [
                self._estimate_acceleration_time(
                    speed_sweep_kmh,
                    wheel_force_curve,
                    mass_kg,
                    crr,
                    base_cda,
                    accel_target_speed,
                    accel_max_time,
                )
                for crr in crr_values
            ]
            self.ax.plot(crr_values, accel_times, marker='o', color=COLORS['success'], linewidth=2.0)
            self.ax.set_title(f"Effect of Crr on 0-{accel_target_speed:.0f} km/h Time", fontsize=16, weight='bold')
            self.ax.set_xlabel("Crr", fontsize=14)
            self.ax.set_ylabel("Acceleration Time (s)", fontsize=14)
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self._mark_current_param_1d(crr_values, base_crr, accel_times, "Crr")

        elif graph_type == "Effect of CdA & Crr on Acceleration Time":
            accel_time_map = np.full((len(crr_values), len(cda_values)), np.nan, dtype=float)
            for i, crr in enumerate(crr_values):
                for j, cda in enumerate(cda_values):
                    accel_time_map[i, j] = self._estimate_acceleration_time(
                        speed_sweep_kmh,
                        wheel_force_curve,
                        mass_kg,
                        crr,
                        cda,
                        accel_target_speed,
                        accel_max_time,
                    )
            self._draw_parametric_contour(
                cda_values, crr_values, accel_time_map,
                title=f"Effect of CdA & Crr on 0-{accel_target_speed:.0f} km/h Time",
                cbar_label="Acceleration Time (s)",
                current_cda=base_cda, current_crr=base_crr,
            )

        elif graph_type == "Effect of CdA on Max Gradability":
            max_gradabilities = [
                self._estimate_max_gradability(
                    speed_sweep_kmh,
                    wheel_force_curve,
                    mass_kg,
                    base_crr,
                    cda,
                    grad_max=grad_max,
                    grad_step=grad_step,
                )
                for cda in cda_values
            ]
            self.ax.plot(cda_values, max_gradabilities, marker='o', color=COLORS['warning'], linewidth=2.0)
            self.ax.set_title("Effect of CdA on Max Gradability", fontsize=16, weight='bold')
            self.ax.set_xlabel("CdA (m^2)", fontsize=14)
            self.ax.set_ylabel("Max Gradability (%)", fontsize=14)
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self._mark_current_param_1d(cda_values, base_cda, max_gradabilities, "CdA")

        elif graph_type == "Effect of Crr on Max Gradability":
            max_gradabilities = [
                self._estimate_max_gradability(
                    speed_sweep_kmh,
                    wheel_force_curve,
                    mass_kg,
                    crr,
                    base_cda,
                    grad_max=grad_max,
                    grad_step=grad_step,
                )
                for crr in crr_values
            ]
            self.ax.plot(crr_values, max_gradabilities, marker='o', color=COLORS['secondary'], linewidth=2.0)
            self.ax.set_title("Effect of Crr on Max Gradability", fontsize=16, weight='bold')
            self.ax.set_xlabel("Crr", fontsize=14)
            self.ax.set_ylabel("Max Gradability (%)", fontsize=14)
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self._mark_current_param_1d(crr_values, base_crr, max_gradabilities, "Crr")

        else:
            gradability_map = np.zeros((len(crr_values), len(cda_values)))
            for i, crr in enumerate(crr_values):
                for j, cda in enumerate(cda_values):
                    gradability_map[i, j] = self._estimate_max_gradability(
                        speed_sweep_kmh,
                        wheel_force_curve,
                        mass_kg,
                        crr,
                        cda,
                        grad_max=grad_max,
                        grad_step=grad_step,
                    )
            self._draw_parametric_contour(
                cda_values, crr_values, gradability_map,
                title="Effect of CdA & Crr on Max Gradability",
                cbar_label="Max Gradability (%)",
                current_cda=base_cda, current_crr=base_crr,
            )

        self.canvas.draw()
