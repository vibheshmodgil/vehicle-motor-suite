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
        plot_type = self.compare_std_plot_var.get()  # "torque", "force", "acceleration"
        mode = self.torque_compare_mode.get()  # "Wheel" or "Motor"
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()
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
            gradients = [float(g.strip()) for g in self.gradients.get().split(",")]
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
        speed_unit = self.speed_unit_combo.get()
        plot_part = "At Wheel" if mode == "Wheel" else "At Motor"
        x_limits = self.get_x_limits(speed_unit, wheel_radius, gear_ratio)
        y_limits = self.get_y_limits(plot_part, gear_ratio)
        max_speed = max(x_limits)
        speeds = np.linspace(0.1, max_speed, 6000)
        speeds_in_rpm = np.linspace(1e-3, max_speed * 60 / (2 * np.pi * wheel_radius * 3.6), 1000)  # RPM for interpolation
        y_data_list = []
        # Call the correct plot function
        if plot_type == "torque":
            
            self.plot_torque_graph(
                speeds, params, gradients, wheel_radius, peak_torque, peak_power, continuous_power,
                x_limits, y_limits, gear_ratio, plot_part=plot_part, speed_unit="km/hr",
                overlay_std=False, show_main_label=False,
                peak_to_rated_torque_ratio=peak_to_rated_torque_ratio
            )
            y_data_list.append(y_limits) 
        elif plot_type == "force":
            self.plot_force_graph(
                speeds, params, gradients, wheel_radius, peak_torque, peak_power, continuous_power,
                x_limits, y_limits, gear_ratio, peak_to_rated_torque_ratio=peak_to_rated_torque_ratio
            )
            y_data_list.append(y_limits[1]) 
            
        elif plot_type == "acceleration":
            self.plot_vehicle_max_speed_vs_time(
                speeds, params, wheel_radius, peak_torque, peak_power, gear_ratio
            )
        # y_data_list = []
        for entry in self.selected_std_motors:
            name = entry["name"]
            std_gear_ratio = float(entry["gear_ratio"])
            std_wheel_radius = float(entry["wheel_radius"])
            std_data = self.std_motor_data[name]
            speeds_rpm_std = np.array(std_data["speed_rpm"])
            # speeds_rpm = np.linspace(1e-3, max(speeds_rpm), 1000)  # Ensure non-zero RPM for calculations
            speed_km_hr_wheel =(speeds_rpm_std * 2 * np.pi * wheel_radius / 60 * 3.6)/gear_ratio
            torque_std = np.array(std_data["torque"])
            force_std = (torque_std * std_gear_ratio * gear_eff) / std_wheel_radius  # Convert to force at wheel
            # Interpolation grid
            speeds_rpm_wheel = speeds * 60 / (2 * np.pi * std_wheel_radius) / 3.6
            speeds_rpm_motor = speeds_rpm_wheel * std_gear_ratio
            if plot_type == "torque":
                
                # Plot torque vs speed (at wheel or motor)
                if mode == "Wheel":
                    interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                    y = interp_torque * std_gear_ratio * gear_eff
                    label = f"{name} (Wheel)"
                else:
                    interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                    y = interp_torque
                    label = f"{name} (Motor)"
                y_data_list.append(y)   
                
                self.ax.plot( speeds, y, label=label, linewidth=2)
                self.ax.set_xlabel ("wheel Speed (km/hr)") 
                self.ax.set_ylabel("Torque (Nm)")
                self.ax.set_title("Compare Standard Motor Data: Torque")
            elif plot_type == "force":
                # F = (torque * gear_ratio) / wheel_radius
                interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                force = (interp_torque * std_gear_ratio * gear_eff) / wheel_radius
                y=force
                y_data_list.append(y)
                print("ylimits force",y_data_list) 
                self.ax.plot(speeds, y, label=f"{name} (Wheel Force)", linewidth=2)
                self.ax.set_xlabel("Wheel Speed (km/hr)")
                self.ax.set_ylabel("Force (N)")
                self.ax.set_title("Compare Standard Motor Data: Force")

            elif plot_type == "acceleration":
                # Simulate velocity-time for this std motor (interpolated torque)
                speeds_kmh = np.linspace(0.1, max_speed, 6000)
                speeds_mps = speeds_kmh / 3.6
                speeds_rpm_wheel = speeds_mps * 60 / (2 * np.pi * std_wheel_radius)
                speeds_rpm_motor = speeds_rpm_wheel * std_gear_ratio
                interp_torque = np.interp(speeds_rpm_motor, speeds_rpm_std, torque_std, left=torque_std[0], right=torque_std[-1])
                max_wheel_force = interp_torque * std_gear_ratio * gear_eff / std_wheel_radius
                wheel_forces = np.array([
                    params['m_i'] * g * params['Crr'] +
                    0.5 * 1.225 * params['CdA'] * (s ** 2) +
                    params['m_i'] * g * np.sin(np.arctan(params.get('gradient', 0) / 100))
                    for s in speeds_mps
                ])
                net_force = max_wheel_force - wheel_forces
                max_acceleration = net_force / params['m_i']
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
                self.ax.plot(time_values, velocity_kmh, label=f"{name} (Velocity-Time)", linewidth=2)
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
            y_limits = self.get_compare_std_y_limits("torque", y_data_list, 'ylim_manual', 'ylim')
        elif plot_type == "force":
            y_limits = self.get_compare_std_y_limits("force", y_data_list, 'ylim_wheel_force_manual', 'ylim_wheel_force')
        else:
            y_limits = None 
        if y_limits is not None:
           self.ax.set_ylim(y_limits) #            
        self.ax.legend()
        self.ax.grid(True)
        self.canvas.draw()

