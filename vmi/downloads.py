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



class DownloadsMixin:

    def download_torque_plot(self):
        # Torque and Force are one analysis now; select the Torque output.
        self.plot_mode = "Powertrain Sizing"
        try:
            self.output_combo.set("Torque")
        except Exception:
            pass
        self.plot_graph()
        self.save_current_figure("torque_plot.png")


    def download_force_plot(self):
        self.plot_mode = "Powertrain Sizing"
        try:
            self.output_combo.set("Force")
        except Exception:
            pass
        self.plot_graph()
        self.save_current_figure("force_plot.png")


    def download_velocity_time_plot(self):
        self.plot_mode = "Acceleration"
        self.plot_graph()
        self.save_current_figure("velocity_time_plot.png")


    def download_drive_cycle_plot(self):
        self.plot_mode = "Drive Cycle"
        self.plot_graph()
        self.save_current_figure("drive_cycle_plot.png")


    def download_torque_speed_plot(self):
        # This assumes you want the torque-speed plot from drive cycle data
        self.plot_mode = "Drive Cycle"
        self.plot_torque_speed_drive_cycle()
        self.save_current_figure("torque_speed_plot.png")
    

    def download_torque_speed_data_excel(self):
        if hasattr(self, "torque_speed_df") and self.torque_speed_df is not None:
            file_path = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
                initialfile="torque_speed_data.xlsx",
                title="Save Torque Speed Data As"
            )
            if file_path:
                try:
                    self.torque_speed_df.to_excel(file_path, index=False)
                    messagebox.showinfo("Download Complete", f"Torque-Speed data saved as:\n{file_path}")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save file:\n{e}")
        else:
            messagebox.showwarning("No Data", "No torque-speed data available to download. Please plot first.")


    def save_current_figure(self, default_filename):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png"), ("All Files", "*.*")],
            initialfile=default_filename,
            title="Save Plot As"
        )
        if file_path:
            self.figure.savefig(file_path, bbox_inches='tight')
            messagebox.showinfo("Download Complete", f"Plot saved as:\n{file_path}")

