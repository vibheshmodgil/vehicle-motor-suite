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



class DriveCycleMixin:

    def drive_cycle_efficiency(self, params):
        """
        Generates an efficiency contour plot overlaid with drive cycle data.
        """
        # Load efficiency map data from Excel
        df_efficiency = pd.read_excel("check.xlsx", index_col=0)
        Speed, Torque, Efficiency = potting_efficieny(df_efficiency)

        # Convert data to NumPy arrays
        Speed = np.asarray(Speed, dtype=np.float64)
        Torque = np.asarray(Torque, dtype=np.float64)
        Efficiency = np.asarray(Efficiency, dtype=np.float64)

        # Validate efficiency matrix dimensions
        if Efficiency.shape != Speed.shape:
            raise ValueError(f"Efficiency matrix shape mismatch: Expected {Speed.shape}, but got {Efficiency.shape}.")

        # Load drive cycle data
        df_drive_cycle = pd.read_excel("idc.xlsx")
        elapsed_time = df_drive_cycle.iloc[:, 0].values  # Time data (first column)
        vehicle_speed = df_drive_cycle.iloc[:, -1].values  # Speed data (last column)

        # Compute motor speed and torque based on drive cycle data
        motor_rpm, motor_torque = driveCycleData(
            elapsed_time, vehicle_speed,
            wheel_radius=float(self.wheel_radius.get()), gear_ratio=1,
            vehicle_mass=params['m_i'], drag_coeff=params['CdA'], 
            rolling_resistance=params['Crr'], air_density=1.225
        )
        
        # Convert to NumPy arrays for efficient processing
        motor_rpm = np.asarray(motor_rpm, dtype=np.float64)
        motor_torque = np.asarray(motor_torque, dtype=np.float64)

        # Filter for positive torque values only
        positive_indices = motor_torque > 0
        motor_rpm = motor_rpm[positive_indices]
        motor_torque = motor_torque[positive_indices]

        # Clear previous figure
        self.ax.clear()

        # Generate efficiency contour plot
        contour = self.ax.contourf(Speed, Torque, Efficiency, cmap='viridis', levels=20)
        contour_lines = self.ax.contour(Speed, Torque, Efficiency, colors='black', linewidths=0.8, levels=20)
        self.ax.clabel(contour_lines, inline=True, fontsize=8, fmt="%.1f")
        self.figure.colorbar(contour, ax=self.ax, label="Efficiency (%)")

        # Overlay drive cycle trace
        self.ax.scatter(motor_rpm, motor_torque, color='red', s=5, alpha=0.7, label="Drive Cycle Data")

        # Set axis labels and plot title
        self.ax.set_xlabel("Speed (RPM)")
        self.ax.set_ylabel("Torque (Nm)")
        self.ax.set_title("Efficiency Map with Drive Cycle Trace")
        self.ax.legend(loc="upper right", fontsize=10, frameon=True)
        self.ax.grid(True, linestyle='--', alpha=0.6)

        # Render the updated plot
        self.canvas.draw()


    def update_drive_cycle_properties(self):
        """Calculate and display drive cycle properties in the input section."""
        # Clear previous labels
        for label in self.drive_cycle_props_labels.values():
            label.destroy()
        self.drive_cycle_props_labels.clear()

        if hasattr(self, "dataframe") and self.dataframe is not None:
            df = self.dataframe
            t = df['dc_time'].to_numpy(dtype=float)
            v_kmh = df['dc_speed'].to_numpy(dtype=float)
            v = v_kmh / 3.6  # m/s

            duration = t[-1] - t[0]
            dist_m = float(np.trapz(v, t))                 # total distance (m)
            distance_km = dist_m / 1000.0
            avg_speed = (dist_m / duration * 3.6) if duration > 0 else 0.0
            max_speed = float(v_kmh.max())

            # Per-sample time step and acceleration (m/s^2).
            dt = np.gradient(t)
            accel = np.gradient(v, t)
            pos_mask = accel > 0
            neg_mask = accel < 0

            max_acc = float(accel[pos_mask].max()) if pos_mask.any() else 0.0
            avg_acc = float(accel[pos_mask].mean()) if pos_mask.any() else 0.0
            max_dec = float(accel[neg_mask].min()) if neg_mask.any() else 0.0   # negative
            avg_dec = float(accel[neg_mask].mean()) if neg_mask.any() else 0.0  # negative

            # Idle / running split (running = moving faster than 0.5 km/h).
            running = v_kmh > 0.5
            idle = ~running
            idle_time = float(dt[idle].sum()) if idle.any() else 0.0
            idle_pct = (100.0 * idle_time / duration) if duration > 0 else 0.0
            avg_run_speed = float(v_kmh[running].mean()) if running.any() else 0.0
            # A "stop" = a running sample followed by a stopped one.
            stops = int(np.sum(idle[1:] & running[:-1]))

            # Positive Kinetic Energy: sum of positive jumps in v^2 per metre of
            # travel (m/s^2). A standard measure of how aggressive a cycle is.
            dv2 = np.diff(v ** 2)
            pke = float(dv2[dv2 > 0].sum() / dist_m) if dist_m > 0 else 0.0
            # Relative Positive Acceleration: (1/x) * integral of v*a over the
            # accelerating phases (m/s^2) -- correlates with tractive energy use.
            rpa = float(np.sum((v * accel * dt)[pos_mask]) / dist_m) if dist_m > 0 else 0.0

            props = {
                "Duration (s)": f"{duration:.1f}",
                "Distance (km)": f"{distance_km:.3f}",
                "Avg Speed (km/h)": f"{avg_speed:.2f}",
                "Avg Running Speed (km/h)": f"{avg_run_speed:.2f}",
                "Max Speed (km/h)": f"{max_speed:.2f}",
                "Max Accel (m/s²)": f"{max_acc:.3f}",
                "Max Decel (m/s²)": f"{max_dec:.3f}",
                "Avg Accel (m/s²)": f"{avg_acc:.3f}",
                "Avg Decel (m/s²)": f"{avg_dec:.3f}",
                "Idle Time (%)": f"{idle_pct:.1f}",
                "Number of Stops": f"{stops}",
                "PKE (m/s²)": f"{pke:.3f}",
                "RPA (m/s²)": f"{rpa:.3f}",
            }

            for i, (k, v) in enumerate(props.items()):
                lbl = ctk.CTkLabel(self.drive_cycle_props_frame, text=f"{k}: {v}", font=("Segoe UI", 12), text_color=COLORS['primary'])
                lbl.pack(anchor="w", padx=16)
                self.drive_cycle_props_labels[k] = lbl


    def plot_drive_cycle(self):
        """Plot the drive cycle (speed vs time) from uploaded data."""
        self._last_dc_plot = self.plot_drive_cycle  # for live graph-settings updates
        if hasattr(self, "heatmap_colorbar") and self.heatmap_colorbar is not None:
            self.heatmap_colorbar.remove()
            self.heatmap_colorbar = None
        self._remove_engine_secondary_axis()
        self.ax.clear()
        if not hasattr(self, "dataframe") or self.dataframe is None:
            self.show_placeholder_message("Please upload a drive cycle file first.")
            self.canvas.draw()
            return
        self.ax.clear()
        self.ax.plot(self.dataframe['dc_time'], self.dataframe['dc_speed'], '-', color=COLORS['primary'], label="Drive Cycle Data")
        self.ax.set_title("Drive Cycle: Vehicle Speed vs Time", fontsize=16, weight='bold')
        self.ax.set_xlabel("Time (s)", fontsize=14)
        self.ax.set_ylabel("Vehicle Speed (Km/hr)", fontsize=14)
        self.ax.grid(True, linestyle='--', alpha=0.7)
        self.ax.legend()
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()
        self.canvas.draw()


    def plot_torque_speed_drive_cycle(self, show_popup=False):
        """Plot Torque vs Speed using the uploaded drive cycle data and your algorithm.
        Also overlays motor data if uploaded, or theoretical peak torque curve if not.
        Shows a popup with input parameters used for calculation only when the user
        presses the button (show_popup=True); live graph-settings replots skip it."""
        self._last_dc_plot = self.plot_torque_speed_drive_cycle  # for live graph-settings updates
        if hasattr(self, "heatmap_colorbar") and self.heatmap_colorbar is not None:
            self.heatmap_colorbar.remove()
            self.heatmap_colorbar = None
        self._remove_engine_secondary_axis()
        self.ax.clear()
        if not hasattr(self, "dataframe") or self.dataframe is None:
            self.show_placeholder_message("Please upload a drive cycle file first.")
            self.canvas.draw()
            return

        # Gather parameters. Parse defensively: a stray non-numeric character in
        # any field must not crash the plot (this is wired to live toggles).
        def _num(entry, label, default=None, positive=False):
            raw = entry.get().strip()
            if raw == "" and default is not None:
                return default
            try:
                val = float(raw)
            except Exception:
                raise ValueError(f"{label} must be a number (got '{raw}').")
            if positive and val <= 0:
                raise ValueError(f"{label} must be greater than 0 (got {val}).")
            return val

        try:
            m = _num(self.m_ref, "Reference Mass", positive=True)
            cr = _num(self.crr, "Crr", default=0.01)
            cda = _num(self.cd_a, "CdA", default=0.5)
            radius_m = _num(self.wheel_radius, "Wheel Radius", positive=True)
            peak_torque = _num(self.peak_torque, "Peak Torque", positive=True)
            peak_power = _num(self.peak_power, "Peak Power", positive=True)
            gear_ratio = _num(self.gear_ratio, "Gear ratio", positive=True)
        except ValueError as exc:
            self.show_placeholder_message(f"Fix input: {exc}")
            try:
                self.set_status(str(exc), "error")
            except Exception:
                pass
            self.canvas.draw()
            return
        density = 1.225
        angle = 0
        gear_eff = self.get_gear_efficiency_value()
        # --- POPUP FRAME FOR INPUT PARAMETERS (only on explicit button press) ---
        if show_popup:
            popup = tk.Toplevel(self)
            popup.title("Input Parameters Used")
            popup.geometry("350x320")
            popup.configure(bg=COLORS['background'])
            popup.grab_set()  # Modal

            param_text = (
                f"Reference Mass (kg): {m}\n"
                f"Crr: {cr}\n"
                f"CdA (m²): {cda}\n"
                f"Wheel Radius (m): {radius_m}\n"
                f"Peak Torque (Nm): {peak_torque}\n"
                f"Peak Power (kW): {peak_power}\n"
                f"Air Density (kg/m³): {density}\n"
                f"Gradient Angle (deg): {angle}\n"
            )
            label = tk.Label(popup, text="Parameters used for Torque-Speed Calculation:", font=("Segoe UI", 11, "bold"), bg=COLORS['background'])
            label.pack(pady=(15, 5))
            param_label = tk.Label(popup, text=param_text, font=("Segoe UI", 11), justify="left", bg=COLORS['background'])
            param_label.pack(pady=(0, 10))

            close_btn = ctk.CTkButton(popup, text="Close", command=popup.destroy)
            close_btn.pack(pady=10)

        # --- REST OF YOUR FUNCTION (unchanged) ---
        df = self.dataframe.copy()
        self.torque_speed_df = df  # <-- Add this line to store for download

        time_col = 'dc_time'
        velocity_col = 'dc_speed'

        def rolling_force(cr, m, angle):
            return cr *  m * g * np.cos(np.radians(angle))

        def aerodynamics_force(cda, density, v):
            return 0.5 * cda * density * v**2
        def gradient_force(m, angle):
            return m * g * np.sin(np.radians(angle))

        df['velocity_m_s'] = df[velocity_col] * (5/18)
        df['acceleration_m_s2'] = df['velocity_m_s'].diff() / df[time_col].diff()
        df['acceleration_m_s2'] = df['acceleration_m_s2'].fillna(0)
        # Wheel rotational inertia adds J/r^2 of translational-equivalent mass
        # to the inertial term only (rolling/gradient keep the actual mass).
        df['accelerating_force'] = self.get_effective_inertial_mass(m, radius_m) * df['acceleration_m_s2']
        df['rolling_resistance_force'] = rolling_force(cr, m, angle)
        df['aerodynamic_force'] = [aerodynamics_force(cda, density, v) for v in df['velocity_m_s']]
        df['gradient_force'] = gradient_force(m, angle)
        df['netforce'] = (
            df['accelerating_force']
            + df['rolling_resistance_force']
            + df['aerodynamic_force']
            + df['gradient_force']
        )
        df['net_torque'] = df['netforce'] * radius_m
        df['net_motor_torque'] = df['net_torque'] / max(gear_ratio * gear_eff, 1e-9)  # Adjust for gear ratio and efficiency
        df['velocity_rpm'] = (df['velocity_m_s'] / (2 * np.pi * radius_m)) * 60  # Convert to RPM
        df['speed_rpm_motor'] = df['velocity_rpm']*gear_ratio # Adjust for gear ratio
        # Per-point tractive mechanical energy at the motor (Wh). Braking points
        # (negative torque) contribute 0: regen returns energy rather than
        # draining range, so these are the bins that actually control range.
        dt = df[time_col].diff().fillna(0.0)
        omega_motor = df['speed_rpm_motor'] * (2 * np.pi / 60.0)
        df['energy_wh'] = np.clip(df['net_motor_torque'], 0, None) * omega_motor * dt / 3600.0
        self.ax.clear()

        x = df['speed_rpm_motor']  # Adjust for gear ratio
        y = df['net_motor_torque']  # Torque (Nm)
        w = df['energy_wh']         # per-point tractive energy (Wh)
        # Filter for positive torque if toggle is ON
        if hasattr(self, "show_positive_torque_var") and self.show_positive_torque_var.get():
            mask = y > 0
            x = x[mask]
            y = y[mask]
            w = w[mask]
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        w = np.asarray(w, dtype=float)

        if self.heatmap_var.get() and len(x) > 1:
            # --- Plot heatmap version (bins defined by *width*, not count) ---
            def _bin_edges(entry, lo, hi, default_w):
                try:
                    width = float(entry.get())
                    if width <= 0:
                        raise ValueError
                except Exception:
                    width = default_w
                span = hi - lo
                if span <= 0:
                    return np.array([lo, lo + width])
                # Cap the bin count so a tiny width can't create a huge grid.
                n = min(int(np.ceil(span / width)), 500)
                return lo + np.arange(n + 1) * width

            # RPM bin width on X, Nm bin width on Y.
            x_edges = _bin_edges(getattr(self, "bin_factor_x_entry", None), 0.0, float(x.max()), 200.0)
            y_edges = _bin_edges(getattr(self, "bin_factor_y_entry", None), float(y.min()), float(y.max()), 5.0)

            # Weight mode: point count or summed tractive energy per bin.
            weight_mode = self.gs_str("hm_weight", "Point Count") if hasattr(self, "gs_str") else "Point Count"
            use_energy = weight_mode.startswith("Energy")
            weights = w if use_energy else None
            hist, xedges, yedges = np.histogram2d(x, y, bins=[x_edges, y_edges], weights=weights)

            total = hist.sum()
            hist_masked = np.ma.masked_where(hist == 0, hist)
            X, Y = np.meshgrid(xedges, yedges)

            cmap_name = self.gs_str("hm_cmap", "YlOrRd") if hasattr(self, "gs_str") else "YlOrRd"
            try:
                cmap = plt.get_cmap(cmap_name)
            except Exception:
                cmap = plt.cm.YlOrRd
            alpha = self.gs_float("hm_alpha", 0.85) if hasattr(self, "gs_float") else 0.85

            cbar_label = "Tractive Energy (Wh) per bin" if use_energy else "Point Count per bin"
            c = self.ax.pcolormesh(X, Y, hist_masked.T, cmap=cmap, shading='auto', alpha=alpha)
            self.heatmap_colorbar = self.figure.colorbar(c, ax=self.ax, label=cbar_label, pad=0.01)

            if not hasattr(self, "gs_bool") or self.gs_bool("hm_show_scatter", True):
                self.ax.scatter(x, y, color=COLORS['primary'], s=8, alpha=0.3, label="Drive Cycle Torque-Speed")

            # Rank and highlight the busiest / most energy-intensive bins.
            self._annotate_top_bins(hist, xedges, yedges, total, use_energy)
        else:
            # --- Plot classic scatter version ---
            self.ax.scatter(x, y, color=COLORS['primary'], s=10, alpha=0.7, label="Drive Cycle Torque-Speed")

        # Overlay: Motor Data Excel or Theoretical Peak Torque Curve
        if hasattr(self, "motor_dataframe") and self.motor_dataframe is not None:
            motor_df = self.motor_dataframe
            self.ax.plot(
                motor_df["motor_speed"], motor_df["motor_torque"],
                color=COLORS['accent'], linewidth=2, alpha=0.9, label="Motor Data (Excel)"
            )
        else:
            try:
                wheel_radius = radius_m
                speeds_rpm = np.linspace(1, 1000, 1000)
                peak_power_w = peak_power * 1000
                base_speed_rpm_peak = (peak_power_w / peak_torque) * 60 / (2 * np.pi)
                peak_torque_curve = np.where(
                    speeds_rpm <= base_speed_rpm_peak,
                    peak_torque,
                    peak_power_w / ((speeds_rpm * 2 * np.pi) / 60)
                )
                vehicle_speeds_kmh = (speeds_rpm * 2 * np.pi * wheel_radius) * 3.6 / 60
                self.ax.plot(speeds_rpm, peak_torque_curve, '--', color='black', label="Peak Torque (Theoretical)")
            except Exception as e:
                logger.error("Error plotting theoretical peak torque curve: %s", e)

        self.ax.set_xlabel("motor Speed (RPM)")
        self.ax.set_ylabel("Net Torque (Nm)")
        self.ax.set_title("Motor Operating Points: Torque vs Speed")
        self.ax.legend()
        if hasattr(self, "apply_graph_style"):
            self.apply_graph_style()
        self.canvas.draw()


    def _annotate_top_bins(self, hist, xedges, yedges, total, use_energy):
        """Highlight the busiest bins that together make up the top X% of the
        cycle's count/energy (X = the 'Highlight top %' setting). Whatever number
        of bins fall in that top slice are ranked, marked with numbered circles,
        and listed in a table placed beside the colormap (top-right, clear of the
        data). These are the operating regions that dominate the cycle / range."""
        if not hasattr(self, "gs_bool") or not self.gs_bool("hm_show_top", True):
            return
        if total <= 0:
            return
        pct = self.gs_float("hm_top_pct", 70.0) if hasattr(self, "gs_float") else 70.0
        pct = max(1.0, min(100.0, pct))

        flat = np.asarray(hist).ravel()
        nonzero = int(np.count_nonzero(flat))
        if nonzero == 0:
            return
        order = np.argsort(flat)[::-1]
        cumsum = np.cumsum(flat[order])
        # Smallest number of top bins whose cumulative weight reaches the target.
        k = int(np.searchsorted(cumsum, (pct / 100.0) * total, side="left")) + 1
        k = min(k, nonzero)
        sel = order[:k]

        xcenters = (xedges[:-1] + xedges[1:]) / 2.0
        ycenters = (yedges[:-1] + yedges[1:]) / 2.0
        unit = "Wh" if use_energy else "pts"
        metric = "Energy" if use_energy else "Count"

        # Mark the selected bins (cap the numbered markers so a large slice
        # doesn't bury the plot in circles).
        marker_cap = 25
        for rank, idx in enumerate(sel[:marker_cap], start=1):
            i, j = np.unravel_index(idx, hist.shape)  # i -> RPM bin, j -> torque bin
            self.ax.annotate(
                str(rank), (xcenters[i], ycenters[j]),
                color='black', fontsize=8, weight='bold', ha='center', va='center',
                bbox=dict(boxstyle='circle,pad=0.15', fc='white', ec='black', alpha=0.85),
                zorder=6,
            )

        # Compact table beside the colormap (top-right, right-aligned so it never
        # clips off the right edge).
        row_cap = 15
        lines = [f"Top {pct:.0f}% by {metric} = {k} bins"]
        for rank, idx in enumerate(sel[:row_cap], start=1):
            i, j = np.unravel_index(idx, hist.shape)
            val = hist[i, j]
            share = 100.0 * val / total
            valstr = f"{val:.1f}" if use_energy else f"{int(val)}"
            lines.append(
                f"{rank:>2} {xedges[i]:.0f}-{xedges[i+1]:.0f}r "
                f"{yedges[j]:.0f}-{yedges[j+1]:.0f}N {valstr}{unit} {share:.1f}%"
            )
        if k > row_cap:
            lines.append(f"  …+{k - row_cap} more bins")

        self.ax.text(
            0.985, 0.985, "\n".join(lines), transform=self.ax.transAxes,
            fontsize=7.0, family='monospace', va='top', ha='right', zorder=6,
            bbox=dict(boxstyle='round', fc='white', ec=COLORS['border'], alpha=0.9),
        )
        try:
            self.set_status(
                f"Heatmap: top {pct:.0f}% by {metric.lower()} = {k} bins", "info"
            )
        except Exception:
            pass

    def plot_drive_cycle_button_callback(self):
        self.plot_mode = "Drive Cycle"
        self.plot_type.set("Drive Cycle")
        
        self.plot_drive_cycle()


    def plot_torque_speed_button_callback(self):

        self.plot_mode = "Drive Cycle"
        self.plot_type.set("Drive Cycle")
        # Scatter view: make sure the heatmap toggle is off.
        if hasattr(self, "heatmap_var"):
            self.heatmap_var.set(False)
        self.plot_torque_speed_drive_cycle(show_popup=True)

    def plot_torque_speed_heatmap_callback(self):
        """Dedicated heatmap button: enables the heatmap and draws the
        torque-speed operating-point map."""
        self.plot_mode = "Drive Cycle"
        self.plot_type.set("Drive Cycle")
        if hasattr(self, "heatmap_var"):
            self.heatmap_var.set(True)
        self.plot_torque_speed_drive_cycle(show_popup=True)

    def on_bin_size_change(self, event=None):
        """Re-render the current drive-cycle plot when a bin width is edited, so
        the heatmap grid updates in place instead of resetting the view."""
        if getattr(self, "plot_mode", None) != "Drive Cycle":
            return
        last = getattr(self, "_last_dc_plot", None)
        if callable(last):
            try:
                last()
            except Exception:
                pass
