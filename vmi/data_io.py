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



class DataIOMixin:

    def save_std_motor_data_popup(self):
        # Prompt for a name
        popup = tk.Toplevel(self)
        popup.title("Save Standard Motor Data")
        popup.geometry("350x150")
        popup.configure(bg=COLORS['background'])
        popup.grab_set()

        label = tk.Label(popup, text="Enter name for this standard motor:", font=("Segoe UI", 11, "bold"), bg=COLORS['background'])
        label.pack(pady=(18, 5))

        name_entry = ctk.CTkEntry(popup, width=200)
        name_entry.pack(pady=10)
        name_entry.focus_set()

        def on_save():
            motor_name = name_entry.get().strip()
            if not motor_name:
                messagebox.showerror("Error", "Please enter a name for the motor.")
                return
            # Gather current torque-speed data (from the first selected std motor or from uploaded motor data)
            # Here, as an example, we use the first selected std motor in the table
            if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
                # Save uploaded motor data
                df = self.motor_dataframe.sort_values("motor_speed")
                data_dict = {
                    "speed_rpm": df["motor_speed"].tolist(),
                    "torque": df["motor_torque"].tolist(),
                    "gear_ratio_std": float(self.gear_ratio.get()),
                    "wheel_radius": float(self.wheel_radius.get())
                }
            elif self.selected_std_motors:
                # Save the first selected std motor's data
                entry = self.selected_std_motors[0]
                std_data = self.std_motor_data[entry["name"]]
                data_dict = {
                    "speed_rpm": std_data["speed_rpm"],
                    "torque": std_data["torque"],
                    "gear_ratio_std": entry["gear_ratio"],
                    "wheel_radius": entry["wheel_radius"]
                }
            else:
                messagebox.showerror("Error", "No motor data available to save.")
                popup.destroy()
                return

            # Load existing JSON
            try:
                with open("std_motor_data_sample.json", "r") as f:
                    std_data = json.load(f)
            except Exception:
                std_data = {}

            # Add or update
            std_data[motor_name] = data_dict

            # Save back to JSON
            try:
                with open("std_motor_data_sample.json", "w") as f:
                    json.dump(std_data, f, indent=2)
                messagebox.showinfo("Success", f"Motor data saved as '{motor_name}' in std_motor_data_sample.json.")
                # Reload std_motor_data in memory
                self.std_motor_data = std_data
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save: {e}")
            popup.destroy()

        save_btn = ctk.CTkButton(popup, text="Save", command=on_save)
        save_btn.pack(pady=10)

    def choose_std_motor_popup(self):
        popup = tk.Toplevel(self)
        popup.title("Choose Standard Motor")
        popup.geometry("350x200")
        popup.configure(bg=COLORS['background'])
        popup.grab_set()

        # Load std motor names from your JSON
        std_motor_names = list(self.std_motor_data.keys())
        label = tk.Label(popup, text="Select Standard Motor:", font=("Segoe UI", 11, "bold"), bg=COLORS['background'])
        label.pack(pady=(18, 5))

        std_combo = ctk.CTkComboBox(popup, values=std_motor_names, width=200)
        std_combo.pack(pady=10)
        std_combo.set(std_motor_names[0])
        def on_add():
            name = std_combo.get()
            # Get default values from JSON
            gear_ratio = self.std_motor_data[name].get("gear_ratio_std", 1.0)
            wheel_radius = self.std_motor_data[name].get("wheel_radius", 0.266)
            self.selected_std_motors.append({
                "name": name,
                "gear_ratio": gear_ratio,
                "wheel_radius": wheel_radius
            })
            self.refresh_std_motor_table()
            popup.destroy()

        add_btn = ctk.CTkButton(popup, text="Add", command=on_add)
        add_btn.pack(pady=10)

    def upload_range_efficiency_map(self, kind="motor"):
        file_path = filedialog.askopenfilename(
            title=f"Select {kind.title()} Efficiency Map Excel",
            filetypes=[("Excel Files", "*.xlsx;*.xls")]
        )
        if not file_path:
            return

        try:
            torque_axis, rpm_axis, eff_map = self._read_efficiency_map_excel(file_path)
            if kind == "motor":
                self.range_motor_eff_map_torques = torque_axis
                self.range_motor_eff_map_rpms = rpm_axis
                self.range_motor_efficiency_map = eff_map
                self.range_motor_eff_delete_button.configure(state="normal")

                # Mirror to Drive Cycle Efficiency section (Motor 1) for shared workflow.
                self.efficiency_data_1 = pd.DataFrame(eff_map, index=torque_axis, columns=rpm_axis)
                self.eff1_map_torques = np.asarray(torque_axis, dtype=float)
                self.eff1_map_rpms = np.asarray(rpm_axis, dtype=float)
                self.eff1_map_matrix = np.asarray(eff_map, dtype=float)
                self._autofill_motor_params_from_map(1, torque_axis, rpm_axis)
                self.eff1_delete_button.configure(state="normal")
            else:
                self.range_controller_eff_map_torques = torque_axis
                self.range_controller_eff_map_rpms = rpm_axis
                self.range_controller_efficiency_map = eff_map
                self.range_controller_eff_delete_button.configure(state="normal")

                # Mirror to Drive Cycle Efficiency section (Motor 2) for shared workflow.
                self.efficiency_data_2 = pd.DataFrame(eff_map, index=torque_axis, columns=rpm_axis)
                self.eff2_map_torques = np.asarray(torque_axis, dtype=float)
                self.eff2_map_rpms = np.asarray(rpm_axis, dtype=float)
                self.eff2_map_matrix = np.asarray(eff_map, dtype=float)
                self._autofill_motor_params_from_map(2, torque_axis, rpm_axis)
                self.eff2_delete_button.configure(state="normal")

            self._sync_shared_efficiency_ticks()

            if self.plot_mode == "Range analysis":
                self.plot_graph()
        except Exception as exc:
            messagebox.showerror("Efficiency Map Error", str(exc))


    def delete_range_efficiency_map(self, kind="motor"):
        if kind == "motor":
            self.range_motor_eff_map_torques = None
            self.range_motor_eff_map_rpms = None
            self.range_motor_efficiency_map = None
            self.range_motor_eff_delete_button.configure(state="disabled")
        else:
            self.range_controller_eff_map_torques = None
            self.range_controller_eff_map_rpms = None
            self.range_controller_efficiency_map = None
            self.range_controller_eff_delete_button.configure(state="disabled")

        self._sync_shared_efficiency_ticks()
        if self.plot_mode == "Range analysis":
            self.plot_graph()


    def load_excel(self):
        """Opens a file dialog to select an Excel file and loads it into a DataFrame, then asks user for time/speed columns via dropdowns."""
        file_path = filedialog.askopenfilename(title="Select Excel File", filetypes=[("Excel Files", "*.xlsx;*.xls")])
        if file_path:
            try:
                df = pd.read_excel(file_path)
                columns = list(df.columns)
                if hasattr(self, "set_status"):
                    import os as _os
                    self.set_status(
                        f"Loaded {_os.path.basename(file_path)}  "
                        f"({len(df)} rows, {len(columns)} cols)", "ok")

                # Create a popup window for column selection
                popup = tk.Toplevel(self)
                popup.title("Select Columns")
                popup.geometry("350x350")
                popup.configure(bg=COLORS['background'])
                popup.grab_set()  # Make modal

                tk.Label(popup, text="Select Time Column (s):", font=("Segoe UI", 11)).pack(pady=(15, 5))
                time_combo = ctk.CTkComboBox(popup, values=columns, width=200)
                time_combo.pack(pady=5)
                time_combo.set(columns[0])

                tk.Label(popup, text="Select Speed Column(km/hr):", font=("Segoe UI", 11)).pack(pady=(15, 5))
                speed_combo = ctk.CTkComboBox(popup, values=columns, width=200)
                speed_combo.pack(pady=5)
                speed_combo.set(columns[1] if len(columns) > 1 else columns[0])

                def on_confirm():
                    time_col = time_combo.get()
                    speed_col = speed_combo.get()
                    if time_col not in columns or speed_col not in columns:
                        messagebox.showerror("Column Error", "Invalid column names selected.")
                        return
                    self.dataframe = df[[time_col, speed_col]].rename(columns={time_col: "dc_time", speed_col: "dc_speed"})
                    self.drive_cycle_indicator.configure(text="\u2705", text_color=COLORS['success'])
                    self.drive_cycle_delete_button.configure(state="normal")
                    self.plot_drive_cycle_button.configure(state="normal")
                    self.plot_torque_speed_button.configure(state="normal")
                    if hasattr(self, "plot_torque_speed_heatmap_button"):
                        self.plot_torque_speed_heatmap_button.configure(state="normal")
                    try:
                        import os as _os
                        self.drive_cycle_filename_label.configure(text=_os.path.basename(file_path))
                    except Exception:
                        pass
                    popup.destroy()
                    self.update_drive_cycle_properties()
                    self.sections['drive_cycle_props'].pack(fill="x", pady=5)
                    self.plot_graph()

                confirm_btn = ctk.CTkButton(popup, text="Confirm", command=on_confirm)
                confirm_btn.pack(pady=15)

            except Exception as e:
                logger.error("Error loading Excel file: %s", e)
                self.drive_cycle_indicator.configure(text="\u274C", text_color=COLORS['warning'])
                self.drive_cycle_delete_button.configure(state="disabled")
                self.dataframe = None
                self.plot_graph()


    def delete_drive_cycle_data(self):
        self.dataframe = None
        self.drive_cycle_indicator.configure(text="\u274C", text_color=COLORS['warning'])
        self.drive_cycle_delete_button.configure(state="disabled")
        self.plot_drive_cycle_button.configure(state="disabled")
        self.plot_torque_speed_button.configure(state="disabled")
        if hasattr(self, "plot_torque_speed_heatmap_button"):
            self.plot_torque_speed_heatmap_button.configure(state="disabled")
        try:
            self.drive_cycle_filename_label.configure(text="No drive cycle file selected")
        except Exception:
            pass
        self.update_drive_cycle_properties()
        self.plot_graph()


    def load_motor_data_excel(self):
        """Opens a file dialog to select a Motor Data Excel file and loads it into a DataFrame, then asks user for torque/speed columns via dropdowns."""
        file_path = filedialog.askopenfilename(title="Select Motor Data Excel File", filetypes=[("Excel Files", "*.xlsx;*.xls")])
        if file_path:
            try:
                df = pd.read_excel(file_path)
                columns = list(df.columns)

                # Create a popup window for column selection
                popup = tk.Toplevel(self)
                popup.title("Select Motor Data Columns")
                popup.geometry("350x350")
                popup.configure(bg=COLORS['background'])
                popup.grab_set()  # Make modal

                tk.Label(popup, text="Select Torque Column:(Nm)", font=("Segoe UI", 11)).pack(pady=(15, 5))
                torque_combo = ctk.CTkComboBox(popup, values=columns, width=200)
                torque_combo.pack(pady=5)
                torque_combo.set(columns[0])

                tk.Label(popup, text="Select Speed Column:(RPM)", font=("Segoe UI", 11)).pack(pady=(15, 5))
                speed_combo = ctk.CTkComboBox(popup, values=columns, width=200)
                speed_combo.pack(pady=5)
                speed_combo.set(columns[1] if len(columns) > 1 else columns[0])

                def on_confirm():
                    torque_col = torque_combo.get()
                    speed_col = speed_combo.get()
                    if torque_col not in columns or speed_col not in columns:
                        messagebox.showerror("Column Error", "Invalid column names selected.")
                        return
                    self.motor_dataframe = df[[torque_col, speed_col]].rename(columns={torque_col: "motor_torque", speed_col: "motor_speed"})
                    self.motor_curve_source = "uploaded_motor"
                    self.motor_data_indicator.configure(text="\u2705", text_color=COLORS['success'])
                    self.motor_data_delete_button.configure(state="normal")
                    try:
                        import os as _os
                        self.motor_data_filename_label.configure(text=_os.path.basename(file_path))
                    except Exception:
                        pass
                    # Lock the manual Motor Performance inputs: the uploaded curve
                    # now drives the calculation.
                    self.set_motor_params_enabled(False)
                    popup.destroy()
                    # Redraw immediately with the new curve so the plot and the
                    # auto x/y limits refresh without a manual "Update Plot".
                    try:
                        self.update_plot()
                    except Exception:
                        pass

                confirm_btn = ctk.CTkButton(popup, text="Confirm", command=on_confirm)
                confirm_btn.pack(pady=15)

            except Exception as e:
                logger.error("Error loading Motor Data Excel file: %s", e)
                self.motor_data_indicator.configure(text="\u274C", text_color=COLORS['warning'])
                self.motor_data_delete_button.configure(state="disabled")
                self.motor_dataframe = None


    def load_engine_data_excel(self):
        file_path = filedialog.askopenfilename(
            title="Select Engine Torque-RPM Excel File",
            filetypes=[("Excel Files", "*.xlsx;*.xls")]
        )
        if not file_path:
            return

        try:
            df = pd.read_excel(file_path)
            columns = list(df.columns)
            if len(columns) < 2:
                raise ValueError("Engine file must have at least 2 columns.")

            popup = tk.Toplevel(self)
            popup.title("Select Engine Data Columns")
            popup.geometry("360x260")
            popup.configure(bg=COLORS['background'])
            popup.grab_set()

            tk.Label(popup, text="Select Engine Torque Column (Nm):", font=("Segoe UI", 11), bg=COLORS['background']).pack(pady=(16, 5))
            torque_combo = ctk.CTkComboBox(popup, values=columns, width=220)
            torque_combo.pack(pady=5)
            torque_combo.set(columns[0])

            tk.Label(popup, text="Select Engine RPM Column:", font=("Segoe UI", 11), bg=COLORS['background']).pack(pady=(12, 5))
            rpm_combo = ctk.CTkComboBox(popup, values=columns, width=220)
            rpm_combo.pack(pady=5)
            rpm_combo.set(columns[1] if len(columns) > 1 else columns[0])

            def on_confirm():
                torque_col = torque_combo.get()
                rpm_col = rpm_combo.get()
                if torque_col not in columns or rpm_col not in columns:
                    messagebox.showerror("Column Error", "Invalid column names selected.")
                    return

                parsed = pd.DataFrame({
                    "engine_torque": pd.to_numeric(df[torque_col], errors='coerce'),
                    "engine_rpm": pd.to_numeric(df[rpm_col], errors='coerce'),
                }).dropna().sort_values("engine_rpm")

                if parsed.empty:
                    messagebox.showerror("Data Error", "No valid numeric torque/rpm values found.")
                    return

                self.engine_dataframe = parsed
                self.engine_data_indicator.configure(text="\u2705", text_color=COLORS['success'])
                self.engine_data_delete_button.configure(state="normal")
                self._sync_engine_curve_to_motor_inputs()
                popup.destroy()
                if self.plot_mode == "Engine analysis":
                    self.plot_graph()

            ctk.CTkButton(popup, text="Confirm", command=on_confirm).pack(pady=16)

        except Exception as exc:
            messagebox.showerror("Engine Data Error", str(exc))
            self.engine_dataframe = None
            self.engine_data_indicator.configure(text="\u274C", text_color=COLORS['warning'])
            self.engine_data_delete_button.configure(state="disabled")


    def delete_engine_data(self):
        self.engine_dataframe = None
        self.engine_data_indicator.configure(text="\u274C", text_color=COLORS['warning'])
        self.engine_data_delete_button.configure(state="disabled")
        self.engine_results_label.configure(text="")

        if self.motor_curve_source == "engine":
            self.motor_dataframe = None
            self.motor_curve_source = None
            self.motor_data_indicator.configure(text="\u274C", text_color=COLORS['warning'])
            self.motor_data_delete_button.configure(state="disabled")

        if self.plot_mode == "Engine analysis":
            self.plot_graph()


    def load_engine_efficiency_excel(self):
        file_path = filedialog.askopenfilename(
            title="Select Gear Efficiency Excel File",
            filetypes=[("Excel Files", "*.xlsx;*.xls")]
        )
        if not file_path:
            return

        try:
            workbook = pd.ExcelFile(file_path)
            self.engine_efficiency_curves = {}

            for gear_idx in range(1, 7):
                if (gear_idx - 1) >= len(workbook.sheet_names):
                    continue
                sheet_name = workbook.sheet_names[gear_idx - 1]
                df_sheet = pd.read_excel(file_path, sheet_name=sheet_name)
                rpm_vals, eff_vals = self._extract_rpm_eff_columns(df_sheet)
                if len(rpm_vals) < 2:
                    continue
                self.engine_efficiency_curves[gear_idx] = {
                    "rpm": rpm_vals,
                    "eff": eff_vals,
                    "sheet": sheet_name,
                }

            if not self.engine_efficiency_curves:
                raise ValueError("No valid RPM-efficiency sheets found. Use one sheet per gear (G1..G6).")

            self.engine_eff_indicator.configure(text="\u2705", text_color=COLORS['success'])
            self.engine_eff_delete_button.configure(state="normal")
            self._sync_engine_curve_to_motor_inputs()
            if self.plot_mode == "Engine analysis":
                self.plot_graph()

        except Exception as exc:
            messagebox.showerror("Gear Efficiency Error", str(exc))
            self.engine_efficiency_curves = {}
            self.engine_eff_indicator.configure(text="\u274C", text_color=COLORS['warning'])
            self.engine_eff_delete_button.configure(state="disabled")


    def delete_engine_efficiency_data(self):
        self.engine_efficiency_curves = {}
        self.engine_eff_indicator.configure(text="\u274C", text_color=COLORS['warning'])
        self.engine_eff_delete_button.configure(state="disabled")
        self._sync_engine_curve_to_motor_inputs()
        if self.plot_mode == "Engine analysis":
            self.plot_graph()


    def upload_efficiency_excel(self, motor=1):
        _role = "Motor" if motor == 1 else "Controller"
        file_path = filedialog.askopenfilename(
            title=f"Select {_role} Efficiency Map Excel File",
            filetypes=[("Excel Files", "*.xlsx;*.xls")]
        )
        if file_path:
            try:
                excel_file = pd.ExcelFile(file_path)
                sheet_names = excel_file.sheet_names

                # Popup for sheet selection
                popup = tk.Toplevel(self)
                popup.title(f"Select Sheet for Motor {motor}")
                popup.geometry("350x350")
                popup.configure(bg=COLORS['background'])
                popup.grab_set()  # Modal

                tk.Label(popup, text="Select Sheet:", font=("Segoe UI", 11), bg=COLORS['background']).pack(pady=(18, 5))
                sheet_combo = ctk.CTkComboBox(popup, values=[str(s) for s in sheet_names], width=200)
                sheet_combo.pack(pady=5)
                sheet_combo.set(sheet_names[0])

                def on_confirm():
                    selected_sheet = sheet_combo.get()
                    try:
                        df = pd.read_excel(file_path, sheet_name=selected_sheet)
                        torque_axis, rpm_axis, eff_map = self._extract_eff_map_from_dataframe(df)
                        if motor == 1:
                            self.efficiency_data_1 = df
                            self.eff1_indicator.configure(text="\u2705", text_color=COLORS['success'])
                            self.eff1_delete_button.configure(state="normal")
                            self.eff1_map_torques = np.asarray(torque_axis, dtype=float)
                            self.eff1_map_rpms = np.asarray(rpm_axis, dtype=float)
                            self.eff1_map_matrix = np.asarray(eff_map, dtype=float)
                            self._autofill_motor_params_from_map(1, torque_axis, rpm_axis)
                        else:
                            self.efficiency_data_2 = df
                            self.eff2_indicator.configure(text="\u2705", text_color=COLORS['success'])
                            self.eff2_delete_button.configure(state="normal")
                            self.eff2_map_torques = np.asarray(torque_axis, dtype=float)
                            self.eff2_map_rpms = np.asarray(rpm_axis, dtype=float)
                            self.eff2_map_matrix = np.asarray(eff_map, dtype=float)
                            self._autofill_motor_params_from_map(2, torque_axis, rpm_axis)
                        self._sync_shared_efficiency_ticks()
                        popup.destroy()
                        # Refresh efficiency numbers + the current map view without
                        # a manual button press.
                        self._refresh_efficiency_after_change()
                    except Exception as e:
                        messagebox.showerror("Sheet Error", f"Failed to load sheet:\n{e}")

                confirm_btn = ctk.CTkButton(popup, text="Confirm", command=on_confirm)
                confirm_btn.pack(pady=15)

            except Exception as e:
                messagebox.showerror("Excel Error", f"Failed to open Excel file:\n{e}")
                if motor == 1:
                    self.eff1_indicator.configure(text="\u274C", text_color=COLORS['warning'])
                    self.eff1_delete_button.configure(state="disabled")
                    self.efficiency_data_1 = None
                    self.eff1_map_torques = None
                    self.eff1_map_rpms = None
                    self.eff1_map_matrix = None
                    self.motor1_max_speed.delete(0, "end")
                    self.motor1_max_torque.delete(0, "end")
                    self.motor1_rated_speed.delete(0, "end")
                    self.motor1_max_power.delete(0, "end")
                else:
                    self.eff2_indicator.configure(text="\u274C", text_color=COLORS['warning'])
                    self.eff2_delete_button.configure(state="disabled")
                    self.efficiency_data_2 = None
                    self.eff2_map_torques = None
                    self.eff2_map_rpms = None
                    self.eff2_map_matrix = None
                    self.motor2_max_speed.delete(0, "end")
                    self.motor2_max_torque.delete(0, "end")
                    self.motor2_rated_speed.delete(0, "end")
                    self.motor2_max_power.delete(0, "end")
                self._sync_shared_efficiency_ticks()

    def delete_efficiency_data(self, motor=1):
        if motor == 1:
            self.efficiency_data_1 = None
            self.eff1_map_torques = None
            self.eff1_map_rpms = None
            self.eff1_map_matrix = None
            self.eff1_indicator.configure(text="\u274C", text_color=COLORS['warning'])
            self.eff1_delete_button.configure(state="disabled")
            self.motor1_max_speed.delete(0, "end")
            self.motor1_max_torque.delete(0, "end")
            self.motor1_rated_speed.delete(0, "end")
            self.motor1_max_power.delete(0, "end")
            self.motor1_max_speed_manual = False
            self.motor1_max_torque_manual = False
            self.motor1_rated_speed_manual = False
            self.motor1_max_power_manual = False
        else:
            self.efficiency_data_2 = None
            self.eff2_map_torques = None
            self.eff2_map_rpms = None
            self.eff2_map_matrix = None
            self.eff2_indicator.configure(text="\u274C", text_color=COLORS['warning'])
            self.eff2_delete_button.configure(state="disabled")
            self.motor2_max_speed.delete(0, "end")
            self.motor2_max_torque.delete(0, "end")
            self.motor2_rated_speed.delete(0, "end")
            self.motor2_max_power.delete(0, "end")
            # Reset manual flags for Motor 2
            self.motor2_max_speed_manual = False
            self.motor2_max_torque_manual = False
            self.motor2_rated_speed_manual = False
            self.motor2_max_power_manual = False

        self._sync_shared_efficiency_ticks()

        if hasattr(self, "efficiency_colorbar") and self.efficiency_colorbar is not None:
            self.efficiency_colorbar.remove()
            self.efficiency_colorbar = None
        self.ax.clear()
        self.ax.set_title("No Efficiency Data Loaded", fontsize=16, color=COLORS['warning'])
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw()
        # Update the drive-cycle-efficiency readout for the new map state.
        self._refresh_efficiency_after_change()
    
    # Add this to your class

    def set_motor_params_enabled(self, enabled):
        """Enable/disable the manual Motor Performance entries. They are locked
        while an uploaded motor curve (Excel) is in use, since the curve -- not
        these numbers -- then drives the torque/force calculations."""
        state = "normal" if enabled else "disabled"
        for attr in ("peak_torque", "peak_power", "continuous_power",
                     "peak_to_rated_torque_ratio"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.configure(state=state)
                except Exception:
                    pass
        try:
            if enabled:
                self.set_status("Motor Performance inputs unlocked.", "info")
            else:
                self.set_status("Motor curve loaded from Excel -- manual Motor "
                                "Performance inputs are locked.", "info")
        except Exception:
            pass

    def delete_motor_data(self):
        """Deletes the uploaded motor data and resets indicator and button state."""
        self.motor_dataframe = None
        self.motor_curve_source = None
        self.motor_data_indicator.configure(text="\u274C", text_color=COLORS['warning'])
        self.motor_data_delete_button.configure(state="disabled")
        try:
            self.motor_data_filename_label.configure(text="No motor curve file selected")
        except Exception:
            pass
        # Re-enable the manual motor inputs now that no curve is loaded.
        self.set_motor_params_enabled(True)
        # Refresh the plot/limits now that the curve is gone (mirrors load).
        try:
            self.update_plot()
        except Exception:
            pass

    def upload_drive_cycle_popup(self):
        popup = tk.Toplevel(self)
        popup.title("Upload Drive Cycle")
        popup.geometry("320x140")
        popup.configure(bg=COLORS['background'])
        popup.grab_set()  # Modal

        label = tk.Label(popup, text="Choose Drive Cycle Source:", font=("Segoe UI", 12, "bold"), bg=COLORS['background'])
        label.pack(pady=(18, 10))

        btn_standard = ctk.CTkButton(
            popup, text="Upload Standard Indian Drive Cycle",
            command=lambda: [popup.destroy(), self.upload_standard_indian_drive_cycle()]
        )
        btn_standard.pack(pady=(0, 8), padx=20, fill='x')

        btn_excel = ctk.CTkButton(
            popup, text="Upload Drive Cycle from Excel",
            command=lambda: [popup.destroy(), self.load_excel()]
        )
        btn_excel.pack(pady=(0, 8), padx=20, fill='x')


    def upload_standard_indian_drive_cycle(self):
        # Implement your logic for standard Indian drive cycle here
        messagebox.showinfo("Standard Drive Cycle", "Standard Indian Drive Cycle upload not implemented yet.")

