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
from .calc_ext import air_density_isa, trapz_energy_wh, check_energy_invariants



class RangeAnalysisMixin:

    def _range_apply_gs(self):
        """Apply the Range Graph Settings to every panel of the current
        figure (apply_graph_style only handles the single self.ax). Only
        settings the user has actually TOUCHED are applied: the stock panels
        deliberately differ in grid alpha / font size, so untouched settings
        must leave them exactly as drawn."""
        vals = getattr(self, "_gs_values", None)
        if not vals:
            return
        analysis = "Range analysis"

        def touched(*keys):
            return any((analysis, k) in vals for k in keys)

        from matplotlib.ticker import MultipleLocator
        for ax in self.figure.axes:
            try:
                if ax.get_label() == "<colorbar>":
                    continue
                if not ax.patch.get_visible():
                    continue  # twinx overlay (the loss panel's % axis)
                if touched("grid_x", "grid_y", "grid_style", "grid_alpha",
                           "grid_x_step", "grid_y_step"):
                    grid_x = self.gs_bool("grid_x", True)
                    grid_y = self.gs_bool("grid_y", True)
                    grid_ls = self.gs_linestyle("grid_style", "--")
                    grid_alpha = self.gs_float("grid_alpha", 0.6)
                    gx_step = self.gs_float("grid_x_step", 0.0)
                    gy_step = self.gs_float("grid_y_step", 0.0)
                    if gx_step and gx_step > 0:
                        lo, hi = ax.get_xlim()
                        if 0 < (hi - lo) / gx_step <= 1000:
                            ax.xaxis.set_major_locator(MultipleLocator(gx_step))
                    if gy_step and gy_step > 0:
                        lo, hi = ax.get_ylim()
                        if 0 < (hi - lo) / gy_step <= 1000:
                            ax.yaxis.set_major_locator(MultipleLocator(gy_step))
                    ax.set_axisbelow('line')
                    ax.grid(False)
                    if grid_x:
                        ax.grid(True, axis="x", linestyle=grid_ls, alpha=grid_alpha)
                    if grid_y:
                        ax.grid(True, axis="y", linestyle=grid_ls, alpha=grid_alpha)
                if touched("show_legend", "legend_loc"):
                    show = self.gs_bool("show_legend", True)
                    loc = self.gs_str("legend_loc", "best") or "best"
                    handles, _labels = ax.get_legend_handles_labels()
                    existing = ax.get_legend()
                    if show and handles:
                        ax.legend(loc=loc, fontsize=8)
                    elif not show and existing is not None:
                        existing.remove()
                if touched("title_size") and ax.get_title():
                    ax.title.set_fontsize(self.gs_int("title_size", 12))
                if touched("label_size"):
                    size = self.gs_int("label_size", 10)
                    ax.xaxis.label.set_size(size)
                    ax.yaxis.label.set_size(size)
                if touched("line_width"):
                    width = self.gs_float("line_width", 1.0)
                    for line in ax.get_lines():
                        line.set_linewidth(width)
            except Exception:
                pass

    def plot_power_energy_cycle(self):
        # Silence the verbose debug prints unless self._debug is set. This only
        # affects console logging; no calculation is touched.
        import builtins as _bi
        _dbg = getattr(self, "_debug", False)
        def print(*a, **k):
            if _dbg:
                _bi.print(*a, **k)

        self.clear_axes()

        if not hasattr(self, "dataframe") or self.dataframe is None:
            self.ax.set_title("No drive cycle loaded.", fontsize=16, color='red')
            self.params_label.configure(text="Range summary unavailable: upload drive cycle data first.")
            if hasattr(self, "range_results_label"):
                self.range_results_label.configure(text="Range summary unavailable: upload drive cycle data first.")
            self._update_drive_cycle_efficiency_label()
            self.figure.tight_layout()
            self.canvas.draw_idle()
            return

        try:
            m_ref = float(self.m_ref.get())
            rear_load_ratio = float(self.rear_load_ratio.get())
            ambient_temp = float(self.ambient_temp.get())
            ambient_pressure = float(self.ambient_pressure.get())
            crr = float(self.crr.get()) if self.crr.get().strip() else None
            cd_a = float(self.cd_a.get()) if self.cd_a.get().strip() else None
            wheel_radius = float(self.wheel_radius.get())
            gear_ratio = float(self.gear_ratio.get())
            gear_eff = self.get_gear_efficiency_value()
        except Exception as exc:
            messagebox.showerror("Input Error", f"Invalid base inputs: {exc}")
            return

        params = calculate_crr_cd_a(
            m_ref,
            rear_load_ratio,
            ambient_temp,
            ambient_pressure,
            crr=crr if self.crr_manual else None,
            cd_a=cd_a if self.cda_manual else None,
        )

        # Keep optional auto-calculation behavior in sync with UI.
        if not self.crr_manual:
            self.crr.delete(0, "end")
            self.crr.insert(0, str(params['Crr']))
        if not self.cda_manual:
            self.cd_a.delete(0, "end")
            self.cd_a.insert(0, str(params['CdA']))

        try:
            n_parallel = int(float(self.cells_parallel.get()))
        except Exception:
            n_parallel = 14
        try:
            n_series = int(float(self.cells_series.get()))
        except Exception:
            n_series = 7
        try:
            cell_capacity = float(self.cell_capacity.get())
        except Exception:
            cell_capacity = 4.8
        try:
            cell_voltage = float(self.cell_voltage.get())
        except Exception:
            cell_voltage = 3.7
        try:
            cell_efficiency = float(self.cell_efficiency.get())
        except Exception:
            cell_efficiency = 100.0
        try:
            dod = float(self.dod.get())
        except Exception:
            dod = 95.0
        try:
            aux_loss = float(self.aux_loss.get())
        except Exception:
            aux_loss = 25.0

        motor_eff_const = self._get_eff_constant(self.motor_eff_const, 0.90)
        controller_eff_const = self._get_eff_constant(self.controller_eff_const, 0.95)
        motor_map_tq, motor_map_rpm, motor_map_matrix, motor_map_source = self._resolve_range_efficiency_map(kind="motor")
        controller_map_tq, controller_map_rpm, controller_map_matrix, controller_map_source = self._resolve_range_efficiency_map(kind="controller")

        df_dc = self.dataframe.copy()
        if "dc_time" not in df_dc.columns or "dc_speed" not in df_dc.columns:
            messagebox.showerror("Drive Cycle Error", "Drive cycle data must contain time and speed columns.")
            return

        time = pd.to_numeric(df_dc["dc_time"], errors='coerce').to_numpy(dtype=float)
        speed = pd.to_numeric(df_dc["dc_speed"], errors='coerce').to_numpy(dtype=float)
        valid = np.isfinite(time) & np.isfinite(speed)
        time = time[valid]
        speed = speed[valid]
        if time.size < 2:
            messagebox.showerror("Drive Cycle Error", "Drive cycle has insufficient valid points.")
            return

        order = np.argsort(time)
        time = time[order]
        speed = speed[order]
        unique_mask = np.concatenate(([True], np.diff(time) > 0))
        time = time[unique_mask]
        speed = speed[unique_mask]
        if time.size < 2:
            messagebox.showerror("Drive Cycle Error", "Drive cycle time vector must be increasing.")
            return

        try:
            gradient = float(self.get_gradients_pct()[0])
        except Exception:
            gradient = 0.0

        speed_mps = speed / 3.6
        dt = np.diff(time, prepend=time[0])
        dt = np.clip(dt, 0.0, None)
        dt_hr = dt / 3600.0
        acc = np.zeros_like(speed_mps)
        delta_t = np.diff(time)
        with np.errstate(divide='ignore', invalid='ignore'):
            acc[1:] = np.divide(np.diff(speed_mps), delta_t, out=np.zeros_like(delta_t), where=delta_t > 0)

        # rho = ambient_pressure * 100000.0 / (287.0 * (273.0 + ambient_temp))
        rho = 1.225
        # Optional altitude/temperature-corrected density (off by default ->
        # the value above is unchanged). When enabled, uses the ISA model with
        # the altitude field and the ambient-temperature input.
        if getattr(self, "alt_density_toggle", None) is not None:
            try:
                if self.alt_density_toggle.get():
                    alt = float(self.altitude_m.get()) if self.altitude_m.get().strip() else 0.0
                    t_c = float(self.ambient_temp.get()) if self.ambient_temp.get().strip() else 15.0
                    rho = air_density_isa(alt, t_c)
            except Exception:
                rho = 1.225
        theta = np.arctan(gradient / 100.0)

        # Optional velocity-dependent rolling resistance: Crr(v) = Crr + Crr1*v.
        # Crr1 defaults to 0 -> identical to the original constant-Crr force.
        crr1 = 0.0
        if getattr(self, "crr_speed_coeff", None) is not None:
            try:
                if self.crr_speed_coeff.get().strip():
                    crr1 = float(self.crr_speed_coeff.get())
            except Exception:
                crr1 = 0.0
        crr_effective = params['Crr'] + crr1 * speed_mps

        f_roll = params['m_i'] * g * crr_effective * np.cos(theta) * np.ones_like(speed_mps)
        f_aero = 0.5 * rho * params['CdA'] * (speed_mps ** 2)
        f_grade = params['m_i'] * g * np.sin(theta) * np.ones_like(speed_mps)
        # Inertial term uses the wheel-inertia-corrected mass (m + J/r^2);
        # the steady-state forces above keep the actual mass. J=0 -> identical.
        f_inertia = self.get_effective_inertial_mass(params['m_i'], wheel_radius) * acc

        f_total = f_roll + f_aero + f_grade + f_inertia
        wheel_torque = f_total * wheel_radius
        wheel_omega = np.where(wheel_radius > 0, speed_mps / wheel_radius, 0.0)
        wheel_power = wheel_torque * wheel_omega

        if abs(gear_ratio) < 1e-9 or wheel_radius <= 0:
            motor_torque = np.zeros_like(wheel_torque)
            motor_rpm = np.zeros_like(speed_mps)
            motor_omega = np.zeros_like(speed_mps)
        else:
            wheel_rpm = wheel_omega * 60.0 / (2.0 * np.pi)
            motor_rpm = wheel_rpm * gear_ratio
            motor_omega = motor_rpm * 2.0 * np.pi / 60.0
            trans_ratio = max(abs(gear_ratio * gear_eff), 1e-9)
            motor_torque = wheel_torque / trans_ratio

        motor_power = motor_torque * motor_omega

        eta_motor = self._interpolate_efficiency_or_constant(
            motor_torque,
            motor_rpm,
            motor_map_matrix,
            motor_map_tq,
            motor_map_rpm,
            motor_eff_const,
        )
        eta_controller = self._interpolate_efficiency_or_constant(
            motor_torque,
            motor_rpm,
            controller_map_matrix,
            controller_map_tq,
            controller_map_rpm,
            controller_eff_const,
        )

        # Positive: motoring (battery discharge), Negative: regen path.
        motor_input_power = np.where(motor_power >= 0, motor_power / eta_motor, motor_power * eta_motor)
        
        controller_input_power = np.where(
            motor_input_power >= 0,
            motor_input_power / eta_controller,
            motor_input_power * eta_controller,
        )

        # Apply auxiliary load only during motoring/discharge.
        battery_output_power = np.where(controller_input_power >= 0, controller_input_power + aux_loss, controller_input_power)

        battery_output_power_clean = np.nan_to_num(battery_output_power)
        battery_regen_power = np.where(battery_output_power_clean < 0, -battery_output_power_clean, 0.0)

        # Optional regen acceptance cap (W). Blank field -> no cap -> unchanged.
        if getattr(self, "regen_cap_w", None) is not None:
            try:
                if self.regen_cap_w.get().strip():
                    _cap = float(self.regen_cap_w.get())
                    battery_regen_power = np.clip(battery_regen_power, 0.0, _cap)
            except Exception:
                pass

        # Fold the (possibly capped) regen back into the battery power trace:
        # the battery only ACCEPTS battery_regen_power of charge, so the
        # battery power/energy/C-rate and the net Wh/km below all see the
        # capped value. Without a cap this is a no-op (the negative side is
        # -battery_regen_power by construction). Previously the cap only
        # affected the displayed regen bar, not the battery energy or the
        # range estimate -- logically inconsistent.
        battery_output_power_clean = np.where(
            battery_output_power_clean < 0.0,
            -battery_regen_power,
            battery_output_power_clean,
        )

        distance_m = speed_mps * dt
        distance_km = distance_m / 1000.0
        cummulative_distance_km = np.cumsum(distance_km)

        p_roll_clean = np.nan_to_num(f_roll * speed_mps)
        p_aero_clean = np.nan_to_num(f_aero * speed_mps)
        p_grade_clean = np.nan_to_num(f_grade * speed_mps)
        p_inertia_clean = np.nan_to_num(f_inertia * speed_mps)
        p_inertia_motoring_clean = p_inertia_clean   # ✅ NET inertia (includes regen automatically)
        wheel_power_clean = np.nan_to_num(wheel_power)
        motor_power_clean = np.nan_to_num(motor_power)
        motor_input_power_clean = np.nan_to_num(motor_input_power)
        controller_input_power_clean = np.nan_to_num(controller_input_power)

        # Drive-cycle efficiency metrics (motoring region only).
        wheel_power_pos = np.where(wheel_power_clean > 0, wheel_power_clean, 0.0)
        motor_power_pos = np.where(motor_power_clean > 0, motor_power_clean, 0.0)
        motor_input_power_pos = np.where(motor_input_power_clean > 0, motor_input_power_clean, 0.0)
        controller_input_power_pos = np.where(controller_input_power_clean > 0, controller_input_power_clean, 0.0)

        total_wheel_out = float(np.nansum(wheel_power_pos))
        total_motor_out = float(np.nansum(motor_power_pos))
        total_motor_in = float(np.nansum(motor_input_power_pos))
        total_controller_in = float(np.nansum(controller_input_power_pos))

        motor_eff_total = (total_motor_out / total_motor_in) if total_motor_in > 1e-9 else 0.0
        controller_eff_total = (total_motor_in / total_controller_in) if total_controller_in > 1e-9 else 0.0
        drive_cycle_eff_total = (total_wheel_out / total_controller_in) if total_controller_in > 1e-9 else 0.0

        # Energy integration. Default is the original cumulative-sum
        # (rectangular) method; switching the toggle to "Trapezoidal" uses
        # trapezoidal integration over the time vector instead. With the
        # default selected, cum_wh() == np.cumsum(power * dt_hr) exactly.
        _use_trapz = False
        if getattr(self, "integration_method", None) is not None:
            try:
                _use_trapz = str(self.integration_method.get()).lower().startswith("trap")
            except Exception:
                _use_trapz = False

        def cum_wh(power):
            if _use_trapz:
                return trapz_energy_wh(power, time)
            return np.cumsum(power * dt_hr)

        e_aero_cum_wh = cum_wh(p_aero_clean)
        e_roll_cum_wh = cum_wh(p_roll_clean)
        e_grade_cum_wh = cum_wh(p_grade_clean)
        e_inertia_motoring_cum_wh = cum_wh(p_inertia_motoring_clean)
        wheel_energy_cum_wh = cum_wh(wheel_power_clean)
        motor_energy_cum_wh = cum_wh(motor_power_clean)
        motor_in_energy_cum_wh = cum_wh(motor_input_power_clean)
        controller_in_energy_cum_wh = cum_wh(controller_input_power_clean)
        battery_out_energy_cum_wh = cum_wh(battery_output_power_clean)
        battery_regen_energy_cum_wh = cum_wh(battery_regen_power)
        
        print(f"e_aero_cum_wh: {e_aero_cum_wh[-1]}")
        print(f"e_roll_cum_wh: {e_roll_cum_wh[-1]}")
        print(f"e_grade_cum_wh: {e_grade_cum_wh[-1]}")
        print(f"e_inertia_motoring_cum_wh: {e_inertia_motoring_cum_wh[-1]}")
        print(f"wheel_energy_cum_wh: {wheel_energy_cum_wh[-1]}")
        print(f"motor_energy_cum_wh: {motor_energy_cum_wh[-1]}")
        print(f"motor_in_energy_cum_wh: {motor_in_energy_cum_wh[-1]}")
        print(f"controller_in_energy_cum_wh: {controller_in_energy_cum_wh[-1]}")


        total_dist_km = float(cummulative_distance_km[-1]) if cummulative_distance_km.size > 0 else 0.0

        def per_km(energy_wh):
            # print(f"energy_wh: {energy_wh}, total_dist_km: {total_dist_km}")
            return float(energy_wh) / total_dist_km if total_dist_km > 0 else 0.0

        aerodynamic_loss_per_km = per_km(e_aero_cum_wh[-1])
        rolling_loss_per_km = per_km(e_roll_cum_wh[-1])
        grade_loss_per_km = per_km(e_grade_cum_wh[-1])
        inertia_loss_motoring_per_km = per_km(e_inertia_motoring_cum_wh[-1])
        transmission_loss_per_km = per_km(max(motor_energy_cum_wh[-1] - wheel_energy_cum_wh[-1], 0.0))
        motor_loss_per_km = per_km(max(motor_in_energy_cum_wh[-1] - motor_energy_cum_wh[-1], 0.0))
        controller_loss_per_km = per_km(max(controller_in_energy_cum_wh[-1] - motor_in_energy_cum_wh[-1], 0.0))
        aux_loss_total_per_km = per_km(max(battery_out_energy_cum_wh[-1] - controller_in_energy_cum_wh[-1], 0.0))
        regen_energy_per_km = per_km(battery_regen_energy_cum_wh[-1])
        
        print(f"aerodynamic_loss_per_km: {aerodynamic_loss_per_km}")
        print(f"rolling_loss_per_km: {rolling_loss_per_km}")
        print(f"grade_loss_per_km: {grade_loss_per_km}")
        print(f"inertia_loss_motoring_per_km: {inertia_loss_motoring_per_km}")
        print(f"transmission_loss_per_km: {transmission_loss_per_km}")
        print(f"motor_loss_per_km: {motor_loss_per_km}")
        print(f"controller_loss_per_km: {controller_loss_per_km}")
        print(f"aux_loss_total_per_km: {aux_loss_total_per_km}")
        print(f"regen_energy_per_km: {regen_energy_per_km}")

        total_energy_loss_per_km = (
            aerodynamic_loss_per_km +
            rolling_loss_per_km +
            grade_loss_per_km +
            inertia_loss_motoring_per_km +
            transmission_loss_per_km +
            motor_loss_per_km +
            controller_loss_per_km +
            aux_loss_total_per_km
        )
        # Net battery draw per km = what the pack actually delivers over the
        # cycle: motoring energy INCLUDING the auxiliary load, minus the regen
        # the battery actually accepts (capped above). The previous version
        # used controller input energy here -- it ignored both the aux load
        # (which the model itself adds to battery output) and the regen cap,
        # so the range estimate disagreed with the loss breakdown beside it.
        net_energy_loss_per_km = battery_out_energy_cum_wh[-1] / total_dist_km if total_dist_km > 1e-9 else 0.0
        
        print("WHEEL CHECK:",
            e_aero_cum_wh[-1] +
            e_roll_cum_wh[-1] +
            e_grade_cum_wh[-1] +
            e_inertia_motoring_cum_wh[-1],
            "vs wheel:", wheel_energy_cum_wh[-1])

        battery_capacity_wh = n_series * n_parallel * cell_capacity * cell_voltage
        usable_capacity_wh = battery_capacity_wh * dod / 100.0
        range_km = usable_capacity_wh / net_energy_loss_per_km if net_energy_loss_per_km > 1e-9 else float('inf')

        if abs(total_energy_loss_per_km) > 1e-9:
            aerodynamic_loss_percentage = (aerodynamic_loss_per_km / total_energy_loss_per_km) * 100.0
            rolling_loss_percentage = (rolling_loss_per_km / total_energy_loss_per_km) * 100.0
            grade_loss_percentage = (grade_loss_per_km / total_energy_loss_per_km) * 100.0
            inertia_loss_percentage = (inertia_loss_motoring_per_km / total_energy_loss_per_km) * 100.0
            regen_percentage = (regen_energy_per_km / total_energy_loss_per_km) * 100.0
            transmission_loss_percentage = (transmission_loss_per_km / total_energy_loss_per_km) * 100.0
            motor_loss_percentage = (motor_loss_per_km / total_energy_loss_per_km) * 100.0
            controller_loss_percentage = (controller_loss_per_km / total_energy_loss_per_km) * 100.0
            aux_loss_percentage = (aux_loss_total_per_km / total_energy_loss_per_km) * 100.0
        else:
            aerodynamic_loss_percentage = 0.0
            rolling_loss_percentage = 0.0
            grade_loss_percentage = 0.0
            inertia_loss_percentage = 0.0
            regen_percentage = 0.0
            transmission_loss_percentage = 0.0
            motor_loss_percentage = 0.0
            controller_loss_percentage = 0.0
            aux_loss_percentage = 0.0

        def draw_power_panel(ax):
            ax.plot(time, wheel_power_clean / 1000.0, color='green', label="Wheel Power (kW)", linewidth=1)
            ax.plot(time, motor_power_clean / 1000.0, color='purple', label="Motor Output Power (kW)", linewidth=1)
            ax.plot(time, motor_input_power_clean / 1000.0, color='red', label="Motor Input Power (kW)", linewidth=1)
            ax.plot(time, controller_input_power_clean / 1000.0, color='orange', label="Controller Input Power (kW)", linewidth=1)
            ax.plot(time, battery_output_power_clean / 1000.0, color='blue', label="Battery Power (kW)", linewidth=1)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Power (kW)")
            ax.set_title("Powers vs Time", fontsize=12, weight='bold', color='navy')
            ax.grid(True, linestyle='--', alpha=0.6, linewidth=0.7)
            ax.legend(fontsize=8)

        def draw_energy_panel(ax):
            ax.plot(time, e_aero_cum_wh, color='green', label="Aerodynamic Energy (Wh)", linewidth=1.2)
            ax.plot(time, e_roll_cum_wh, color='brown', label="Rolling Energy (Wh)", linewidth=1.2)
            ax.plot(time, e_grade_cum_wh, color='orange', label="Grade Energy (Wh)", linewidth=1.2)
            ax.plot(time, e_inertia_motoring_cum_wh, color='gray', label="Inertia Energy (Wh)", linewidth=1.2)
            ax.plot(time, motor_energy_cum_wh, color='purple', label="Motor Output Energy (Wh)", linewidth=1.2)
            ax.plot(time, motor_in_energy_cum_wh, color='red', label="Motor Input Energy (Wh)", linewidth=1.2)
            ax.plot(time, controller_in_energy_cum_wh, color='orange', label="Controller Input Energy (Wh)", linewidth=1.2)
            ax.plot(time, battery_out_energy_cum_wh, color='blue', label="Battery Energy (Wh)", linewidth=1.2)
            ax.plot(time, -battery_regen_energy_cum_wh, color='cyan', label="Regen Energy (Wh)", linewidth=1.2)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Cumulative Energy (Wh)")
            ax.set_title("Cumulative Energy vs Time", fontsize=12, weight='bold', color='navy')
            ax.grid(True, linestyle='--', alpha=0.3, linewidth=0.7)
            ax.legend(fontsize=8)

        def draw_crate_panel(ax):
            denom = cell_voltage * cell_capacity * max(n_series * n_parallel, 1) * max(cell_efficiency / 100.0, 1e-9)
            c_rate = battery_output_power_clean / denom if denom > 1e-9 else np.zeros_like(battery_output_power_clean)
            ax.plot(time, c_rate, color='purple', label="C-rate (1/h)", linewidth=1.5)
            ax.axhline(0, color='k', linewidth=0.5, alpha=0.3)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("C-rate (1/h)")
            ax.set_title("Battery C-rate vs Time", fontsize=12, weight='bold', color='magenta')
            ax.grid(True, linestyle='--', alpha=0.6, linewidth=0.7)
            ax.legend(fontsize=8)

        def draw_loss_panel(ax):
            ax_pct = ax.twinx()
            stages = [
                "Regeneration",
                "Aerodynamic Loss",
                "Rolling Loss",
                "Grade Loss",
                "Inertia Loss",
                "Transmission Loss",
                "Motor Loss",
                "Controller Loss",
                "Auxiliary Loss",
            ]
            energy_segments = [
                -regen_energy_per_km,
                aerodynamic_loss_per_km,
                rolling_loss_per_km,
                grade_loss_per_km,
                inertia_loss_motoring_per_km,
                transmission_loss_per_km,
                motor_loss_per_km,
                controller_loss_per_km,
                aux_loss_total_per_km,
            ]
            percent_segments = [
                regen_percentage,
                aerodynamic_loss_percentage,
                rolling_loss_percentage,
                grade_loss_percentage,
                inertia_loss_percentage,
                transmission_loss_percentage,
                motor_loss_percentage,
                controller_loss_percentage,
                aux_loss_percentage,
            ]
            colors = ["#24e17f", "#ffcc00", "#5e3c99", "#b2abd2", "#238b45", "#a6cee3", "#1f78b4", "#b15928", "#984ea3"]

            bar_width = 0.4
            x_abs = 0
            x_pct = 1

            # Regen below zero on absolute axis.
            ax.bar(x_abs, energy_segments[0], bottom=0, color=colors[0], width=bar_width, label=stages[0], alpha=0.7)
            if abs(energy_segments[0]) > 1e-6:
                ax.text(x_abs, energy_segments[0] / 2, f"{energy_segments[0]:.1f}", ha='center', va='center', fontsize=8, fontweight='bold')

            bottom_abs = 0.0
            for seg, lab, col in zip(energy_segments[1:], stages[1:], colors[1:]):
                seg_pos = max(seg, 0.0)
                ax.bar(x_abs, seg_pos, bottom=bottom_abs, color=col, width=bar_width, label=lab, alpha=0.7)
                if seg_pos > 0:
                    ax.text(x_abs, bottom_abs + seg_pos / 2, f"{seg_pos:.1f}", ha='center', va='center', fontsize=8)
                bottom_abs += seg_pos

            # Percent axis (also show regen below zero).
            ax_pct.bar(x_pct, -abs(percent_segments[0]), bottom=0, color=colors[0], width=bar_width, alpha=0.7)
            if abs(percent_segments[0]) > 1e-3:
                ax_pct.text(x_pct, -abs(percent_segments[0]) / 2, f"-{abs(percent_segments[0]):.1f}%", ha='center', va='center', fontsize=8, fontweight='bold')

            bottom_pct = 0.0
            for seg, col in zip(percent_segments[1:], colors[1:]):
                seg_pos = max(seg, 0.0)
                ax_pct.bar(x_pct, seg_pos, bottom=bottom_pct, color=col, width=bar_width, alpha=0.7)
                if seg_pos > 0:
                    ax_pct.text(x_pct, bottom_pct + seg_pos / 2, f"{seg_pos:.1f}%", ha='center', va='center', fontsize=8)
                bottom_pct += seg_pos

            textstr = (
                f"Usable Capacity: {usable_capacity_wh:.1f} Wh\n"
                f"Wh/km (net): {net_energy_loss_per_km:.1f}\n"
                f"Estimated Range: {range_km:.1f} km"
            )
            props = dict(boxstyle='round', facecolor='wheat', alpha=0.8)
            ax.text(0.57, 0.06, textstr, transform=ax.transAxes, fontsize=10, verticalalignment='bottom', bbox=props, zorder=10)

            ax.set_xticks([x_abs, x_pct])
            ax.set_xticklabels(["Wh/km", "%"])
            ax.set_ylabel("Wh/km")
            ax_pct.set_ylabel("%")
            ax.set_title("Component Level Energy Loss", fontsize=12, weight="bold", color="navy")
            ax.set_xlim(-0.6, 1.8)
            y_min = min(0.0, energy_segments[0] * 1.2)
            y_max = max(bottom_abs * 1.08, 1.0)
            ax.set_ylim(y_min, y_max)
            ax.grid(True, linestyle='--', alpha=0.3, linewidth=0.5)
            ax_pct.grid(False)

            handles, labels = ax.get_legend_handles_labels()
            uniq = []
            seen = set()
            for h, l in zip(handles, labels):
                if l not in seen:
                    uniq.append((h, l))
                    seen.add(l)
            if uniq:
                ax.legend(*zip(*uniq), loc='upper left', fontsize=8, frameon=True)

        def draw_drive_cycle_panels(axs):
            ax_speed = axs[0, 0]
            ax_speed.plot(time, speed, color='blue', linewidth=1.3, label="Speed (km/h)")
            ax_speed.set_xlabel("Time (s)")
            ax_speed.set_ylabel("Speed (km/h)")
            ax_speed.set_title("Drive Cycle Speed vs Time", fontsize=11, weight='bold', color='blue')
            ax_speed.grid(True, linestyle='--', alpha=0.6, linewidth=0.7)
            ax_speed.legend(fontsize=8)

            ax_wheel = axs[0, 1]
            ax_wheel.plot(time, wheel_torque, color='green', linewidth=1.2, label="Wheel Torque (Nm)")
            ax_wheel.set_xlabel("Time (s)")
            ax_wheel.set_ylabel("Wheel Torque (Nm)")
            ax_wheel.set_title("Wheel Torque vs Time", fontsize=11, weight='bold', color='green')
            ax_wheel.grid(True, linestyle='--', alpha=0.6, linewidth=0.7)
            ax_wheel.legend(fontsize=8)

            ax_motor = axs[1, 0]
            ax_motor.plot(time, motor_torque, color='red', linewidth=1.2, label="Motor Torque (Nm)")
            ax_motor.set_xlabel("Time (s)")
            ax_motor.set_ylabel("Motor Torque (Nm)")
            ax_motor.set_title("Motor Torque vs Time", fontsize=11, weight='bold', color='red')
            ax_motor.grid(True, linestyle='--', alpha=0.6, linewidth=0.7)
            ax_motor.legend(fontsize=8)

            ax_scatter = axs[1, 1]
            ax_scatter.scatter(motor_rpm, motor_torque, color='purple', s=12, alpha=0.8, label="Operating points", zorder=6)

            try:
                peak_torque_val = float(self.peak_torque.get())
            except Exception:
                peak_torque_val = 0.0
            try:
                peak_power_val = float(self.peak_power.get())
            except Exception:
                peak_power_val = 0.0

            if peak_torque_val > 0 and peak_power_val > 0:
                peak_power_w = peak_power_val * 1000.0
                base_speed_rpm_peak = (peak_power_w / (peak_torque_val + 1e-9)) * 60.0 / (2.0 * np.pi)
                rpm_max_candidates = [np.nanmax(np.abs(motor_rpm)) if motor_rpm.size else 1.0]
                if self.range_motor_eff_map_rpms is not None:
                    rpm_max_candidates.append(np.nanmax(self.range_motor_eff_map_rpms))
                rpm_max_candidates.append(base_speed_rpm_peak * 1.1)
                rpm_max_curve = max(rpm_max_candidates)
                rpm_grid = np.linspace(0.0, max(1.0, rpm_max_curve), 500)
                peak_curve = np.where(
                    rpm_grid <= base_speed_rpm_peak,
                    peak_torque_val,
                    peak_power_w / ((rpm_grid * 2.0 * np.pi / 60.0) + 1e-9),
                )
                label_text = f"Peak Torque-Power Curve (GR={gear_ratio:.2f})"
                ax_scatter.plot(rpm_grid, peak_curve, '--', color='black', linewidth=1.8, label=label_text, zorder=7)

            if (
                motor_map_matrix is not None
                and motor_map_rpm is not None
                and motor_map_tq is not None
            ):
                speed_grid, torque_grid = np.meshgrid(motor_map_rpm, motor_map_tq)
                eff_grid = np.asarray(motor_map_matrix, dtype=float) * 100.0
                contour = ax_scatter.contourf(speed_grid, torque_grid, eff_grid, cmap='RdYlGn', levels=35, alpha=0.45, zorder=1)
                ax_scatter.contour(speed_grid, torque_grid, eff_grid, colors='black', linewidths=0.15, levels=8, alpha=0.5)
                self.range_eff_colorbar = self.figure.colorbar(contour, ax=ax_scatter, label="Motor Efficiency (%)")

            ax_scatter.set_xlabel("Motor Speed (RPM)")
            ax_scatter.set_ylabel("Motor Torque (Nm)")
            ax_scatter.set_title("Motor Torque vs Motor Speed", fontsize=11, weight='bold', color='purple')
            ax_scatter.grid(True, linestyle='--', alpha=0.6, linewidth=0.7)

            handles, labels = ax_scatter.get_legend_handles_labels()
            uniq_h = []
            uniq_l = []
            seen = set()
            for h, l in zip(handles, labels):
                if l not in seen:
                    uniq_h.append(h)
                    uniq_l.append(l)
                    seen.add(l)
            if uniq_h:
                ax_scatter.legend(uniq_h, uniq_l, loc="upper right", fontsize=8)

        def draw_motor_eff_panel(ax):
            self._plot_range_efficiency_map_panel(
                ax=ax,
                torque_axis=motor_map_tq,
                rpm_axis=motor_map_rpm,
                eff_map=motor_map_matrix,
                title="Motor Efficiency Map",
                colorbar_label="Motor Efficiency (%)",
                default_eff=motor_eff_const,
                overlay_rpm=motor_rpm,
                overlay_torque=np.abs(motor_torque),
            )

        def draw_controller_eff_panel(ax):
            self._plot_range_efficiency_map_panel(
                ax=ax,
                torque_axis=controller_map_tq,
                rpm_axis=controller_map_rpm,
                eff_map=controller_map_matrix,
                title="Controller Efficiency Map",
                colorbar_label="Controller Efficiency (%)",
                default_eff=controller_eff_const,
                overlay_rpm=motor_rpm,
                overlay_torque=np.abs(motor_torque),
            )

        # Battery-utilization figures for the report's Range interpretation
        # (all derived from arrays already computed above; purely additive --
        # no existing metric changes).
        batt_kw = battery_output_power_clean / 1000.0
        peak_idx = int(np.argmax(batt_kw)) if batt_kw.size else 0
        peak_batt_kw = float(batt_kw[peak_idx]) if batt_kw.size else 0.0
        peak_batt_time_s = float(time[peak_idx]) if batt_kw.size else 0.0
        # Duration spent within 90% of the peak power (severity of the peak
        # events) and the average power while actually moving.
        near_peak_s = float(np.sum(dt[batt_kw >= 0.9 * peak_batt_kw])) if peak_batt_kw > 0 else 0.0
        moving = speed > 0.5
        avg_moving_kw = float(np.mean(batt_kw[moving])) if np.any(moving) else 0.0
        denom_crate = cell_voltage * cell_capacity * max(n_series * n_parallel, 1) * max(cell_efficiency / 100.0, 1e-9)
        peak_c_rate = float(np.max(battery_output_power_clean) / denom_crate) if denom_crate > 1e-9 else 0.0
        # Where does the motoring energy go: accelerating vs cruising?
        wheel_pos = np.where(wheel_power_clean > 0, wheel_power_clean, 0.0)
        e_wheel_pos = float(np.sum(wheel_pos * dt))
        e_wheel_accel = float(np.sum(wheel_pos[acc > 0.05] * dt[acc > 0.05]))
        accel_share_pct = 100.0 * e_wheel_accel / e_wheel_pos if e_wheel_pos > 1e-9 else 0.0

        # Stash the per-km energy breakdown so the export, report and the
        # waterfall view can reuse it; run cheap sanity checks and surface any
        # warning in the status bar.
        self._last_range_metrics = {
            "peak_battery_power_kw": round(peak_batt_kw, 3),
            "peak_battery_power_at_s": round(peak_batt_time_s, 1),
            "time_within_90pct_of_peak_s": round(near_peak_s, 1),
            "avg_battery_power_moving_kw": round(avg_moving_kw, 3),
            "peak_c_rate": round(peak_c_rate, 3),
            "total_battery_energy_wh": round(float(battery_out_energy_cum_wh[-1]), 2),
            "wheel_energy_share_accelerating_pct": round(accel_share_pct, 1),
            "trip_distance_km": round(float(total_dist_km), 3),
            "aerodynamic_loss_per_km": round(float(aerodynamic_loss_per_km), 3),
            "rolling_loss_per_km": round(float(rolling_loss_per_km), 3),
            "grade_loss_per_km": round(float(grade_loss_per_km), 3),
            "inertia_loss_motoring_per_km": round(float(inertia_loss_motoring_per_km), 3),
            "transmission_loss_per_km": round(float(transmission_loss_per_km), 3),
            "motor_loss_per_km": round(float(motor_loss_per_km), 3),
            "controller_loss_per_km": round(float(controller_loss_per_km), 3),
            "aux_loss_total_per_km": round(float(aux_loss_total_per_km), 3),
            "regen_energy_per_km": round(float(regen_energy_per_km), 3),
            "gross_loss_per_km": round(float(total_energy_loss_per_km), 3),
            "net_energy_loss_per_km": round(float(net_energy_loss_per_km), 3),
            "estimated_range_km": (round(float(range_km), 2) if np.isfinite(range_km) else None),
            "motor_eff": round(float(motor_eff_total), 4),
            "controller_eff": round(float(controller_eff_total), 4),
            "drive_cycle_eff": round(float(drive_cycle_eff_total), 4),
        }
        if hasattr(self, "set_status"):
            warns = check_energy_invariants(self._last_range_metrics)
            if warns:
                self.set_status("Range check: " + "; ".join(warns), "warn")

        selected_view = "All"
        if hasattr(self, "range_plot_toggle"):
            selected_view = self.range_plot_toggle.get()
        self.figure.clf()
        if selected_view == "All":
            axs = self.figure.subplots(2, 2)
            draw_power_panel(axs[0, 0])
            draw_energy_panel(axs[0, 1])
            draw_crate_panel(axs[1, 0])
            draw_loss_panel(axs[1, 1])
            self.ax = axs[0, 0]
        else:
            ax_main = self.figure.add_subplot(111)
            if selected_view == "Power":
                draw_power_panel(ax_main)
            elif selected_view == "Energy":
                draw_energy_panel(ax_main)
            elif selected_view == "C-rate":
                draw_crate_panel(ax_main)
            elif selected_view in ("Drive", "Drive Cycle"):
                self.figure.clf()
                axs = self.figure.subplots(2, 2)
                draw_drive_cycle_panels(axs)
                self.ax = axs[0, 0]
                ax_main = None
            elif selected_view in ("M Eff", "Motor Eff"):
                draw_motor_eff_panel(ax_main)
            elif selected_view in ("C Eff", "Ctrl Eff", "Controller Eff"):
                draw_controller_eff_panel(ax_main)
            elif selected_view == "Waterfall":
                self._draw_loss_waterfall(ax_main, self._last_range_metrics)
            else:
                draw_loss_panel(ax_main)
            if ax_main is not None:
                self.ax = ax_main

        motor_eff_source = (
            motor_map_source
            if motor_map_matrix is not None
            else f"Constant ({motor_eff_const * 100.0:.1f}%)"
        )
        ctrl_eff_source = (
            controller_map_source
            if controller_map_matrix is not None
            else f"Constant ({controller_eff_const * 100.0:.1f}%)"
        )
        summary_text = (
            f"Range Analysis Summary\n"
            f"Trip Distance: {total_dist_km:.3f} km\n"
            f"Gross Energy Loss: {total_energy_loss_per_km:.2f} Wh/km\n"
            f"Net Energy Loss (after regen): {net_energy_loss_per_km:.2f} Wh/km\n"
            f"Estimated Range: {range_km:.2f} km\n"
            f"Drive Cycle Eff (motoring) - Motor: {motor_eff_total * 100.0:.2f}% | "
            f"Controller: {controller_eff_total * 100.0:.2f}% | Wheel/Controller: {drive_cycle_eff_total * 100.0:.2f}%\n"
            f"Motor Efficiency Source: {motor_eff_source}\n"
            f"Controller Efficiency Source: {ctrl_eff_source}"
        )
        self.params_label.configure(text=summary_text)
        if hasattr(self, "range_results_label"):
            self.range_results_label.configure(text=summary_text)
        self._update_drive_cycle_efficiency_label(
            motor_eff_pct=motor_eff_total * 100.0,
            controller_eff_pct=controller_eff_total * 100.0,
            overall_eff_pct=drive_cycle_eff_total * 100.0,
            motor_source=motor_eff_source,
            controller_source=ctrl_eff_source,
        )
        print(summary_text.replace("\n", " | "))
        # Per-panel Graph Settings (only user-touched settings are applied).
        self._range_apply_gs()
        self.figure.tight_layout()
        self.canvas.draw_idle()

