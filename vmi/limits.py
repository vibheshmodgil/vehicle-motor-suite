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



class LimitsMixin:

    def get_x_limits(self, speed_unit, wheel_radius, gear_ratio):
        """
        Returns x_limits for the plot based on speed unit and plotting part.
        - If speed unit is km/h and plotting part is vehicle: use xlim entry.
        - If speed unit is RPM and plotting part is vehicle: use xlim_rpm_vehicle entry, or calculate from xlim.
        - If speed unit is RPM and plotting part is motor: use xlim_rpm_motor entry, or calculate from xlim_rpm_vehicle * gear_ratio.
        """
        analysis_type = self.plot_type.get()
        plot_part = self.plot_part_combo.get()
        
         # If motor data is uploaded and not manually set, use its max values with tolerance
        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            df_sorted = self.motor_dataframe.sort_values("motor_speed")
            max_motor_speed = df_sorted["motor_speed"].max()
            max_motor_speed_tol = max_motor_speed * 1.1  # 10% tolerance

            if not self.xlim_rpm_motor_manual:
                self.xlim_rpm_motor.delete(0, "end")
                self.xlim_rpm_motor.insert(0, f"0,{max_motor_speed_tol:.2f}")

            # Update xlim_rpm_vehicle if not manually set
            if not self.xlim_rpm_vehicle_manual:
                xlim_rpm_vehicle_max = max_motor_speed_tol / gear_ratio
                self.xlim_rpm_vehicle.delete(0, "end")
                self.xlim_rpm_vehicle.insert(0, f"0,{xlim_rpm_vehicle_max:.2f}")

            # Update xlim (km/h, vehicle) if not manually set
            if not self.xlim_manual:
                # Convert RPM to km/h: v_kmh = (rpm * 2 * pi * wheel_radius * 60) / 1000 / 3.6
                xlim_rpm_vehicle_str = self.xlim_rpm_vehicle.get().strip()
                if xlim_rpm_vehicle_str:
                    rpm_vehicle_max = float(xlim_rpm_vehicle_str.split(",")[-1])
                    v_mps = (rpm_vehicle_max * 2 * np.pi * wheel_radius) / 60
                    v_kmh = v_mps * 3.6
                    self.xlim.delete(0, "end")
                    self.xlim.insert(0, f"0,{v_kmh:.2f}")
                 # Return the correct x_limits for the current plot mode
            if analysis_type == "Force":
                xlim_force_str = self.xlim.get().strip()
                x_limits = [float(x.strip()) for x in xlim_force_str.split(",") if x.strip()]
                return x_limits  
                 
            if speed_unit == "RPM" and plot_part == "At Motor":
                xlim_rpm_motor_str = self.xlim_rpm_motor.get().strip()
                x_limits = [float(x.strip()) for x in xlim_rpm_motor_str.split(",") if x.strip()]
                return x_limits
            elif speed_unit == "RPM" and plot_part == "At Wheel":
                xlim_rpm_vehicle_str = self.xlim_rpm_vehicle.get().strip()
                x_limits = [float(x.strip()) for x in xlim_rpm_vehicle_str.split(",") if x.strip()]
                return x_limits
            elif speed_unit == "Km/hr":
                xlim_str = self.xlim.get().strip()
                x_limits = [float(x.strip()) for x in xlim_str.split(",") if x.strip()]
                return x_limits  
            # --- If no motor data, use normal logic ---  
         # --- Force analysis type: always use xlim_force (km/h) ---
        if analysis_type == "Force":
            xlim_force_str = self.xlim_force.get().strip()
            x_limits = [float(x.strip()) for x in xlim_force_str.split(",") if x.strip()]
            return x_limits


        # 1. Speed unit = km/h, plotting part = vehicle
        if speed_unit == "Km/hr" and plot_part == "At Wheel":
            xlim_str = self.xlim.get().strip()
            # if not xlim_str:
            #     return None
            x_limits = [float(x.strip()) for x in xlim_str.split(",")]
            return x_limits
        # 2. Speed unit = km/h, plotting part = motor
        if speed_unit == "Km/hr" and plot_part == "At Motor":
            xlim_str = self.xlim.get().strip()
            x_limits = [float(x.strip()) for x in xlim_str.split(",")]
            return x_limits

        # 2. Speed unit = RPM, plotting part = vehicle
        if speed_unit == "RPM" and plot_part == "At Wheel":
            xlim_rpm_vehicle_str = self.xlim_rpm_vehicle.get().strip()
            if xlim_rpm_vehicle_str:
                x_limits = [float(x.strip()) for x in xlim_rpm_vehicle_str.split(",")]
                return x_limits
            # If not manually given, calculate from xlim (km/h)
            xlim_str = self.xlim.get().strip()
            if not xlim_str:
                return None
            x_limits_kmh = [float(x.strip()) for x in xlim_str.split(",")]
            x_limits_rpm = []
            for v_kmh in x_limits_kmh:
                v_mps = v_kmh * 1000 / 3600
                rpm = (v_mps / wheel_radius) * 60 / (2 * np.pi)
                x_limits_rpm.append(rpm)
            # Update the entry in the UI
            # self.xlim_rpm_vehicle.delete(0, "end")
            # self.xlim_rpm_vehicle.insert(0, ",".join(f"{v:.2f}" for v in x_limits_rpm))
            return x_limits_rpm

        # 3. Speed unit = RPM, plotting part = motor
        if speed_unit == "RPM" and plot_part == "At Motor":
            xlim_rpm_motor_str = self.xlim_rpm_motor.get().strip()
            if xlim_rpm_motor_str:
                x_limits = [float(x.strip()) for x in xlim_rpm_motor_str.split(",")]
                return x_limits
            # If not manually given, calculate from xlim_rpm_vehicle * gear_ratio
            xlim_rpm_vehicle_str = self.xlim_rpm_vehicle.get().strip()
            if xlim_rpm_vehicle_str:
                x_limits_vehicle = [float(x.strip()) for x in xlim_rpm_vehicle_str.split(",")]
            else:
                # If xlim_rpm_vehicle is also not given, calculate from xlim (km/h)
                xlim_str = self.xlim.get().strip()
                if not xlim_str:
                    return None
                x_limits_kmh = [float(x.strip()) for x in xlim_str.split(",")]
                x_limits_vehicle = []
                for v_kmh in x_limits_kmh:
                    v_mps = v_kmh * 1000 / 3600
                    rpm = (v_mps / wheel_radius) * 60 / (2 * np.pi)
                    x_limits_vehicle.append(rpm)
                # Update the vehicle rpm entry
                # self.xlim_rpm_vehicle.delete(0, "end")
                # self.xlim_rpm_vehicle.insert(0, ",".join(f"{v:.2f}" for v in x_limits_vehicle))
            x_limits_motor = [v * gear_ratio for v in x_limits_vehicle]
            # Update the motor rpm entry
            # self.xlim_rpm_motor.delete(0, "end")
            # self.xlim_rpm_motor.insert(0, ",".join(f"{v:.2f}" for v in x_limits_motor))
            return x_limits_motor

        # Default fallback
        return None
    

    def get_compare_std_y_limits(self, plot_type, y_data_list, manual_flag_attr, ylim_entry_attr):
        """
        Returns y-limits for compare std motor data plots.
        - plot_type: "torque" or "force"
        - y_data_list: list of np.arrays (one per std motor)
        - manual_flag_attr: attribute name for manual flag (e.g. 'ylim_manual', 'ylim_wheel_force_manual')
        - ylim_entry_attr: attribute name for entry widget (e.g. 'ylim', 'ylim_wheel_force')
        """
        manual_flag = getattr(self, manual_flag_attr, False)
        ylim_entry = getattr(self, ylim_entry_attr)
        if not manual_flag:
            # Auto: set to 0, max(y) * 1.1
            if y_data_list:
            #     max_y = max([np.nanmax(np.abs(y)) for y in y_data_list if len(y) > 0])
            #     y_max = max_y * 1.1
               max_y = max([
                    np.nanmax(np.abs(np.asarray(y)))
                    for y in y_data_list
                    if isinstance(y, (list, tuple, np.ndarray)) and len(y) > 0
                ])

               y_max = max_y * 1.1
            else:
                y_max = 100  # fallback
            ylim_entry.delete(0, "end")
            ylim_entry.insert(0, f"0,{y_max:.2f}")
            return [0, y_max]
        else:
            # Manual: use entry
            ylim_str = ylim_entry.get().strip()
            y_limits = [float(y.strip()) for y in ylim_str.split(",") if y.strip()]
            return y_limits


    def get_y_limits(self, plot_part, gear_ratio):
            analysis_type = self.plot_type.get()
            # Torque and Force share the "Torque" analysis; the Output selector
            # decides the y quantity. Force => wheel force (N), regardless of part.
            is_force = (
                analysis_type == "Powertrain Sizing"
                and getattr(self, "output_combo", None) is not None
                and self.output_combo.get() == "Force"
            )
            gear_eff = self.get_gear_efficiency_value()
            # If motor data is uploaded and not manually set, use its max values with tolerance
            if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
                df_sorted = self.motor_dataframe.sort_values("motor_speed")
                max_motor_torque = df_sorted["motor_torque"].max()
                max_motor_torque_tol = max_motor_torque * 1.1  # 10% tolerance

                # Y-axis Limit (Nm) motor
                if not self.ylim_manual:
                    self.ylim.delete(0, "end")
                    self.ylim.insert(0, f"0,{max_motor_torque_tol:.2f}")

                # Y-axis Limit (Nm) wheel
                if not self.ylim_wheel_manual:
                    y_wheel = max_motor_torque_tol * gear_ratio * gear_eff
                    self.ylim_wheel.delete(0, "end")
                    self.ylim_wheel.insert(0, f"0,{y_wheel:.2f}")

                # Y-axis Limit (N) wheel
                if not self.ylim_wheel_force_manual:
                    wheel_radius = float(self.wheel_radius.get())
                    y_force = (max_motor_torque_tol / wheel_radius) * gear_ratio * gear_eff
                    self.ylim_wheel_force.delete(0, "end")
                    self.ylim_wheel_force.insert(0, f"0,{y_force:.2f}")
                # Return the correct y_limits for the current plot mode. Force is
                # checked first: it is wheel force (N) whatever the plotting part.
                if is_force:
                    ylim_wheel_force_str = self.ylim_wheel_force.get().strip()
                    y_limits = [float(y.strip()) for y in ylim_wheel_force_str.split(",") if y.strip()]
                    return y_limits
                elif plot_part == "At Motor":
                    ylim_str = self.ylim.get().strip()
                    y_limits = [float(y.strip()) for y in ylim_str.split(",") if y.strip()]
                    return y_limits
                elif plot_part == "At Wheel":
                    ylim_wheel_str = self.ylim_wheel.get().strip()
                    y_limits = [float(y.strip()) for y in ylim_wheel_str.split(",") if y.strip()]
                    return y_limits

            if is_force:
                # Use the new wheel force y-limit entry
                if not hasattr(self, "ylim_wheel_force_manual"):
                    self.ylim_wheel_force_manual = False
                if not self.ylim_wheel_force_manual:
                    try:
                        peak_torque = float(self.peak_torque.get())
                        wheel_radius = float(self.wheel_radius.get())
                        y_max = (peak_torque / wheel_radius) * gear_ratio * gear_eff + 20
                    except Exception:
                        y_max = 1000  # fallback
                    self.ylim_wheel_force.delete(0, "end")
                    self.ylim_wheel_force.insert(0, f"0,{y_max}")
                ylim_str = self.ylim_wheel_force.get().strip()
                y_limits = [float(y.strip()) for y in ylim_str.split(",") if y.strip()]
                return y_limits
            elif plot_part == "At Wheel":
                # Use the new wheel y-limit entry
                if not hasattr(self, "ylim_wheel_manual"):
                    self.ylim_wheel_manual = False
                if not self.ylim_wheel_manual:
                    try:
                        peak_torque = float(self.peak_torque.get())
                        y_max = peak_torque * gear_ratio * gear_eff + 5
                    except Exception:
                        y_max = 300  # fallback
                    self.ylim_wheel.delete(0, "end")
                    self.ylim_wheel.insert(0, f"0,{y_max}")
                ylim_str = self.ylim_wheel.get().strip()
                y_limits = [float(y.strip()) for y in ylim_str.split(",") if y.strip()]
                return y_limits
            else:  # At Motor
                if not hasattr(self, "ylim_manual"):
                    self.ylim_manual = False
                if not self.ylim_manual:
                    try:
                        peak_torque = float(self.peak_torque.get())
                        y_max = peak_torque + 5
                    except Exception:
                        y_max = 300  # fallback
                    self.ylim.delete(0, "end")
                    self.ylim.insert(0, f"0,{y_max}")
                ylim_str = self.ylim.get().strip()
                y_limits = [float(y.strip()) for y in ylim_str.split(",") if y.strip()]
                return y_limits

    def get_x_limits_force(self):
        # If not manually set, copy from main xlim entry
        if not hasattr(self, "xlim_force_manual"):
            self.xlim_force_manual = False
        if not self.xlim_force_manual:
            xlim_str = self.xlim.get().strip()
            if not xlim_str:
                xlim_str = "0,80"
            self.xlim_force.delete(0, "end")
            self.xlim_force.insert(0, xlim_str)
        xlim_force_str = self.xlim_force.get().strip()
        x_limits = [float(x.strip()) for x in xlim_force_str.split(",") if x.strip()]
        return x_limits

    def get_y_limits_force(self, peak_torque, wheel_radius, gear_ratio):
        if not hasattr(self, "ylim_force_manual"):
            self.ylim_force_manual = False
        gear_eff = self.get_gear_efficiency_value()
        if not self.ylim_force_manual:
            try:
                y_max = (peak_torque / wheel_radius) * gear_ratio * gear_eff + 5
            except Exception:
                y_max = 1000  # fallback
            self.ylim_force.delete(0, "end")
            self.ylim_force.insert(0, f"0,{y_max:.2f}")
        ylim_str = self.ylim_force.get().strip()
        y_limits = [float(y.strip()) for y in ylim_str.split(",") if y.strip()]
        return y_limits

