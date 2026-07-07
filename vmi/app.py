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

from .ui_helpers import HelpersMixin
from .limits import LimitsMixin
from .torque_force import TorqueForceMixin
from .parametric import ParametricMixin
from .drive_cycle import DriveCycleMixin
from .engine import EngineMixin
from .efficiency import EfficiencyMixin
from .range_analysis import RangeAnalysisMixin
from .compare_std import CompareStdMixin
from .data_io import DataIOMixin
from .downloads import DownloadsMixin
from .dispatch import DispatchMixin
from .enhancements import EnhancementsMixin
from .graph_settings import GraphSettingsMixin
from .assistant import AssistantMixin
from .mtpa_mtpv import MtpaMtpvMixin
from .mechanical_design import MechanicalDesignMixin
from .bom import BomMixin


class TorqueSpeedApp(
    EnhancementsMixin, GraphSettingsMixin, AssistantMixin,
    HelpersMixin, LimitsMixin, TorqueForceMixin, ParametricMixin, DriveCycleMixin, EngineMixin, EfficiencyMixin, RangeAnalysisMixin, CompareStdMixin, DataIOMixin, DownloadsMixin, DispatchMixin,
    MtpaMtpvMixin, MechanicalDesignMixin, BomMixin,
    ctk.CTk,
):
    def __init__(self):
        super().__init__()
        self.plot_mode = "Powertrain Sizing"  # <-- Set this FIRST!
        self.gs_init()  # graph-settings value store (used while building sections)
        self.configure(fg_color=COLORS['background'])
        self.title("Vehicle ↔ Motor Integration Suite")
        self.geometry("1450x900")
        self.create_menu_bar()
        self.create_header()  # Call the method to add logo and team name
        self.build_toolbar()       # new: quick-action bar
        self.build_status_bar()    # new: bottom status strip
        self.crr_manual = False
        self.cda_manual = False
        # self.speed_unit_combo.configure(command=self.on_plot_mode_change)
        # self.plot_part_combo.configure(command=self.on_plot_mode_change)
        # PanedWindow
        try:
            with open("std_motor_data_sample.json", "r") as f:
                self.std_motor_data = json.load(f)
        except Exception:
            # File is optional; start with an empty library if absent.
            self.std_motor_data = {}
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=4, bg=COLORS['accent'])
        paned.pack(fill="both", expand=True)
        container = tk.Frame(paned, bg=COLORS['background'])
        paned.add(container, minsize=350)
        plot_frame = tk.Frame(paned, bg=COLORS['background'])
        paned.add(plot_frame, minsize=400)
        # Stored on self so AssistantMixin can add/forget its sidebar pane at
        # runtime (paned.add(..., before=self.container) / paned.forget(...)).
        self.paned = paned
        self.container = container

        # Prominent, always-visible primary action pinned to the top of the input
        # panel (the one at the bottom of the scroll area was hard to find).
        top_action_bar = ctk.CTkFrame(container, fg_color=COLORS['background'])
        top_action_bar.pack(side="top", fill="x", padx=8, pady=(8, 4))
        self.top_update_button = ctk.CTkButton(
            top_action_bar,
            text="⏵  Update Plot",
            command=self.update_plot,
            height=42,
            corner_radius=10,
            font=("Segoe UI", 15, "bold"),
            fg_color=COLORS['primary'],
            hover_color=COLORS['accent'],
            text_color="white",
        )
        self.top_update_button.pack(fill="x")

        # Canvas and Scrollbar (keep only one set)
        canvas = tk.Canvas(container, width=380, bg=COLORS['background'], highlightthickness=0)
        self.input_scroll_canvas = canvas  # used by toggle_section() to refresh scroll region
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        input_frame = ctk.CTkFrame(canvas, fg_color=COLORS['background'])
        window_id = canvas.create_window((0, 0), window=input_frame, anchor="nw")

        def on_canvas_configure(event):
            canvas.itemconfig(window_id, width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))
        def on_mouse_wheel(event):
            # Only scroll the input panel when the pointer is actually over it;
            # otherwise the wheel hijacks scrolling over the plot / assistant.
            try:
                widget = self.winfo_containing(event.x_root, event.y_root)
                w = widget
                while w is not None:
                    if w is canvas:
                        canvas.yview_scroll(-1 * (event.delta // 120), "units")
                        break
                    w = w.master
            except Exception:
                pass
        canvas.bind("<Configure>", on_canvas_configure)
        input_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", on_mouse_wheel)

        # --- Analysis type selection ---
        frame = ctk.CTkFrame(input_frame, fg_color=COLORS["header_bg_soft"])
        frame.pack(fill="x", pady=8, padx=8)

        self.analysis_label = ctk.CTkLabel(
            frame, text="Analysis Type:", font=("Segoe UI", 13, "bold"), text_color=COLORS["primary"]
        )
        self.analysis_label.pack(side="left", padx=8, pady=4)

        self.plot_type = ctk.CTkComboBox(
            frame,
            values=[
                "Powertrain Sizing",
                "Acceleration",
                "Parametric Study",
                "Drive Cycle",
                "Drive Cycle Efficiency",
                "Compare Standard Motor Data",
                "Engine analysis",
                "Range analysis",
                "MTPA / MTPV (PMSM)",
                "Mechanical Design (Motor)",
                "Motor BOM (Cost & Weight)",
            ],
            command=self.update_plot,
            font=("Segoe UI", 12),
            dropdown_fg_color=COLORS["card"],
            dropdown_text_color=COLORS["text"]
        )
        self.plot_type.set("Powertrain Sizing")
        self.plot_type.pack(side="left", padx=8, pady=4)

        # Required/optional data checklist for the selected analysis (filled by
        # update_data_checklist; empty for analyses with no uploads).
        self.data_checklist_label = ctk.CTkLabel(
            input_frame, text="", font=("Segoe UI", 11),
            text_color=COLORS['text_muted'], anchor="w", justify="left",
        )
        self.data_checklist_label.pack(fill="x", padx=16, pady=(0, 4))


        # --- Store references to each section ---
        self.sections = {}
        # --- Plot Mode Section (above Vehicle Parameters) ---
        self.sections['plot_mode'] = self.create_section(input_frame, "Plot Mode", "#f1f5f9")
        plot_mode_frame = self.sections['plot_mode']

        # Speed Unit row (stacked vertically -- Plotting Part sits below it).
        speed_unit_row = ctk.CTkFrame(plot_mode_frame, fg_color="transparent")
        speed_unit_row.pack(fill="x", padx=16, pady=(10, 4))
        speed_unit_label = ctk.CTkLabel(
            speed_unit_row,
            text="Speed Unit:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary'],
            width=90,
            anchor="w",
        )
        speed_unit_label.pack(side="left", padx=(0, 8))

        self.speed_unit_combo = ctk.CTkComboBox(
            speed_unit_row,
            values=["RPM", "Km/hr"],
            font=("Segoe UI", 12),
            width=120
        )
        self.speed_unit_combo.set("Km/hr")
        self.speed_unit_combo.pack(side="left")

        # Plotting Part row (below Speed Unit).
        plot_part_row = ctk.CTkFrame(plot_mode_frame, fg_color="transparent")
        plot_part_row.pack(fill="x", padx=16, pady=(0, 10))
        plot_part_label = ctk.CTkLabel(
            plot_part_row,
            text="Plotting Part:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary'],
            width=90,
            anchor="w",
        )
        plot_part_label.pack(side="left", padx=(0, 8))

        self.plot_part_combo = ctk.CTkComboBox(
            plot_part_row,
            values=["At Wheel", "At Motor"],
            font=("Segoe UI", 12),
            width=120
        )
        self.plot_part_combo.set("At Wheel")
        self.plot_part_combo.pack(side="left")

        # Output row (Torque vs Force) -- Torque and Force share one analysis; this
        # selector decides which quantity is plotted. The Speed Unit / Plotting Part
        # controls above drive the x-axis for BOTH (Force y-axis is always wheel N).
        output_row = ctk.CTkFrame(plot_mode_frame, fg_color="transparent")
        output_row.pack(fill="x", padx=16, pady=(0, 10))
        output_label = ctk.CTkLabel(
            output_row,
            text="Output:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary'],
            width=90,
            anchor="w",
        )
        output_label.pack(side="left", padx=(0, 8))
        self.output_combo = ctk.CTkComboBox(
            output_row,
            values=["Torque", "Force"],
            font=("Segoe UI", 12),
            width=120,
        )
        self.output_combo.set("Torque")
        self.output_combo.pack(side="left")
        self.sections['vehicle'] = self.create_section(input_frame, "Vehicle Parameters", "#f1f5f9")
        self.create_labeled_entry(self.sections['vehicle'], "Reference Mass (kg)", "180", "m_ref")
        self.create_labeled_entry(self.sections['vehicle'], "Rear Load Ratio", "0.5", "rear_load_ratio")
        self.create_labeled_entry(self.sections['vehicle'], "Wheel Radius (m)", "0.266", "wheel_radius")
        # Dynamic rolling radius = factor x static (unloaded) tyre radius. The
        # tyre-spec dropdown stores the static radius; selecting a tyre fills
        # Wheel Radius with static * this factor. Typical loaded radial ~0.96-0.98.
        self.create_labeled_entry(self.sections['vehicle'], "Dynamic Radius Factor", "0.98", "dynamic_radius_factor")
        self.create_labeled_entry(self.sections['vehicle'], "Gear ratio", "1", "gear_ratio")
        self.create_labeled_entry(self.sections['vehicle'], "Gear efficiency (0-1)", "1", "gear_efficiency")
        self.gear_efficiency.bind("<FocusOut>", self.on_gear_efficiency_focus_out)
        # Velocity-dependent rolling resistance term: Crr(v) = Crr + Crr1*v.
        # Read by Range analysis / Drive Cycle Efficiency; defaults to 0 (no
        # effect) so every other analysis is unchanged unless set.
        self.create_labeled_entry(self.sections['vehicle'], "Crr speed coeff  Crr1 [per m/s]", "0", "crr_speed_coeff")
        # Total wheel rotational inertia (all wheels combined), reflected as
        # extra translational mass m_eff = m + J/r^2 in every INERTIAL (m*a)
        # term: acceleration sim, parametric accel time, drive-cycle / range
        # inertial force. 0 (default) = original behaviour; steady-state
        # results (top speed, gradability) are physically unaffected.
        self.create_labeled_entry(self.sections['vehicle'], "Wheel Inertia J (kg·m², total)", "0", "wheel_inertia")


        self.sections['dynamics'] = self.create_section(input_frame, "Vehicle Dynamics Inputs (Optional)", "#f1f5f9")
        self.create_labeled_entry(self.sections['dynamics'], "Crr (optional)", "", "crr")
        self.create_labeled_entry(self.sections['dynamics'], "CdA (m²) (optional)", "", "cd_a")
        self.create_labeled_entry(self.sections['dynamics'], "Gradients (comma separated)", "0,7,12.3,17.6", "gradients")
        # Gradient values can be entered as a slope percentage (rise/run x 100,
        # the original behaviour) or as an incline angle in degrees. Everything
        # downstream works in percent via get_gradients_pct(); plot labels are
        # rendered back in the selected unit by fmt_gradient().
        grad_unit_row = self.create_control_row(self.sections['dynamics'], "Gradient Unit")
        self.gradient_unit_combo = ctk.CTkComboBox(
            grad_unit_row,
            values=["Percent (%)", "Degrees (°)"],
            font=("Segoe UI", 12),
            width=140,
            command=lambda _c: self.update_plot(),
        )
        self.gradient_unit_combo.set("Percent (%)")
        self.gradient_unit_combo.pack(side="right")

        self.sections['motor'] = self.create_section(input_frame, "Motor Performance Parameters", "#f1f5f9")
        self.create_labeled_entry(self.sections['motor'], "Peak Torque (Nm)", "180", "peak_torque")
        self.create_labeled_entry(self.sections['motor'], "Peak Power (kW)", "2.4", "peak_power")
        self.create_labeled_entry(self.sections['motor'], "Continuous Power (kW)", "1.8", "continuous_power")
        self.create_labeled_entry(self.sections['motor'], "Peak to Rated Torque Ratio", "2", "peak_to_rated_torque_ratio")
        # Battery DC limit (optional): shaft power is capped everywhere a
        # motor capability curve is drawn (torque/force/accel, parametric
        # sweeps, Compare Std overlays, efficiency-map envelope). With the
        # Motor/Controller efficiency maps loaded the limit is evaluated
        # AFTER them (Vdc*Idc*eta_m(T,w)*eta_c(T,w), per operating point);
        # the Battery-to-Shaft Efficiency entry is the constant-chain
        # fallback used only when no maps are loaded. Leaving voltage or
        # current blank = no cap (original behaviour).
        self.create_labeled_entry(self.sections['motor'], "Battery Voltage (V) (optional)", "", "batt_voltage")
        self.create_labeled_entry(self.sections['motor'], "Battery DC Current Limit (A) (optional)", "", "batt_current_limit")
        self.create_labeled_entry(self.sections['motor'], "Battery-to-Shaft Eff (0-1, used when no maps)", "0.9", "batt_to_shaft_eff")
        # A coarse or NaN-holed uploaded efficiency map can put a hard
        # efficiency cliff right at its own coverage edge (a grid cell with
        # one NaN corner falls back to a flat constant) -- exactly where the
        # battery limit usually binds hardest, showing up as an abrupt jump
        # in the capped torque-speed curve. Off by default (no-op unless
        # both a map AND a battery limit are in play); see
        # HelpersMixin._battery_eta_fn / calc_ext.smooth_efficiency_matrix.
        battery_smooth_row = self.create_control_row(
            self.sections['motor'], "Smooth Map for Battery Limit")
        self.battery_eff_smoothing_switch = ctk.CTkSwitch(
            battery_smooth_row, text="", onvalue=True, offvalue=False,
            command=self.update_plot,
        )
        self.battery_eff_smoothing_switch.pack(side="right")

        # --- Thermal load points (duty-point overlay) ---------------------
        # Steady operating conditions (gradient, speed, time-at-condition)
        # converted to motor torque-speed points and overlaid on the Torque
        # plot and the efficiency maps, so the user can check at a glance
        # whether the motor sustains the intended duty. Fail-soft: overlay
        # off / blank / malformed points simply draw nothing.
        self.sections['thermal'] = self.create_section(input_frame, "Thermal Load Points (Optional)", "#f1f5f9")
        thermal_frame = self.sections['thermal']
        thermal_row = self.create_control_row(thermal_frame, "Show Load Points")
        self.thermal_overlay_switch = ctk.CTkSwitch(
            thermal_row, text="", onvalue=True, offvalue=False,
            command=self.update_plot,
        )
        self.thermal_overlay_switch.pack(side="right")
        thermal_unit_row = self.create_control_row(thermal_frame, "Point Speed Unit")
        self.thermal_speed_unit_combo = ctk.CTkComboBox(
            thermal_unit_row, values=["km/h", "Motor RPM"],
            font=("Segoe UI", 12), width=120,
            command=lambda _c: self.update_plot(),
        )
        self.thermal_speed_unit_combo.set("km/h")
        self.thermal_speed_unit_combo.pack(side="right")
        # One point = gradient, speed, duration-at-condition (s). Multiple
        # points: either a flat comma list grouped in 3s -- same style as the
        # Gradients field, e.g. "0,60,300,7,30,600" for two points -- or
        # ';'-separated triples, e.g. "0,60,300; 7,30,600". Gradient honors
        # the Gradient Unit selector above.
        self.create_labeled_entry(
            thermal_frame,
            "Points (grad,speed,time per point, e.g. 0,60,300,7,30,600)",
            "", "thermal_points")
        self.thermal_points.bind("<Return>", lambda _e: self.update_plot())
        self.thermal_results_label = ctk.CTkLabel(
            thermal_frame, text="", justify="left",
            font=("Segoe UI", 11), text_color=COLORS['primary'], anchor="w",
        )
        self.thermal_results_label.pack(fill="x", padx=16, pady=(2, 8))

        self.sections['sim'] = self.create_section(input_frame, "Simulation Settings", "#f1f5f9")
        self.xlim_frame=self.create_labeled_entry(self.sections['sim'], "X-axis Limit (kmh,vehicle)"," ", "xlim",return_frame=True)
        self.xlim_manual = False
        self.xlim.bind("<KeyRelease>", self.on_xlim_manual_edit)
        self.xlim_rpm_vehicle_frame=self.create_labeled_entry(self.sections['sim'], "X-axis Limit (RPM,vehicle)"," ", "xlim_rpm_vehicle",return_frame=True)
        self.xlim_rpm_vehicle_manual = False
        self.xlim_rpm_vehicle.bind("<KeyRelease>", self.on_xlim_rpm_vehicle_manual_edit)
        self.xlim_rpm_motor_frame=self.create_labeled_entry(self.sections['sim'], "X-axis Limit (RPM,motor)"," ", "xlim_rpm_motor",return_frame=True)
        self.xlim_rpm_motor_manual = False
        self.xlim_rpm_motor.bind("<KeyRelease>", self.on_xlim_rpm_motor_manual_edit)
        self.ylim_frame=self.create_labeled_entry(self.sections['sim'], "Y-axis Limit (Nm) motor"," ","ylim",return_frame=True)
        self.ylim_manual = False
        self.ylim.bind("<KeyRelease>", self.on_ylim_manual_edit)
        self.ylim_wheel_frame=self.create_labeled_entry(self.sections['sim'], "Y-axis Limit (Nm) wheel", " ", "ylim_wheel",return_frame=True)
        self.ylim_wheel_manual = False
        self.ylim_wheel.bind("<KeyRelease>", self.on_ylim_wheel_manual_edit)
        self.ylim_wheel_force_frame=self.create_labeled_entry(self.sections['sim'], "Y-axis Limit (N) wheel", " ", "ylim_wheel_force",return_frame=True)
        self.ylim_wheel_force_manual = False
        self.ylim_wheel_force.bind("<KeyRelease>", self.on_ylim_wheel_force_manual_edit)
        self.max_time_frame = self.create_labeled_entry(self.sections['sim'], "Max Simulation Time", "60", "max_time", return_frame=True)
        self.target_speed_frame = self.create_labeled_entry(self.sections['sim'], "Target Speed", "60", "target_speed", return_frame=True)

        self.sections['simforce'] = self.create_section(input_frame, "Simulation Settings-force", "#f1f5f9")
        self.create_labeled_entry(self.sections['simforce'], "X-axis Limit (kmh)","0,80 ", "xlim_force")
        self.xlim_force_manual = False
        self.xlim_force.bind("<KeyRelease>", self.on_xlim_force_manual_edit)
        self.create_labeled_entry(self.sections['simforce'], "Y-axis Limit (N)"," ", "ylim_force")
        self.ylim_force_manual = False
        self.ylim_force.bind("<KeyRelease>", self.on_ylim_force_manual_edit)

        # --- Parametric Study Section ---
        self.sections['parametric_study'] = self.create_section(input_frame, "Parametric Study Settings", "#f1f5f9")
        parametric_frame = self.sections['parametric_study']
        parametric_label = ctk.CTkLabel(
            parametric_frame,
            text="Parametric Graph Type",
            font=("Segoe UI", 12),
            text_color=COLORS['primary'],
        )
        parametric_label.pack(fill="x", padx=16, pady=(6, 2))
        self.parametric_graph_combo = ctk.CTkComboBox(
            parametric_frame,
            values=[
                "Effect of CdA on Top Speed",
                "Effect of Crr on Top Speed",
                "Effect of CdA & Crr on Top Speed",
                "Effect of CdA on Acceleration Time",
                "Effect of Crr on Acceleration Time",
                "Effect of CdA & Crr on Acceleration Time",
                "Effect of CdA on Max Gradability",
                "Effect of Crr on Max Gradability",
                "Effect of CdA & Crr on Max Gradability",
            ],
            font=("Segoe UI", 12),
            command=lambda _choice: self.plot_graph(),
        )
        self.parametric_graph_combo.set("Effect of CdA on Top Speed")
        self.parametric_graph_combo.pack(fill="x", padx=16, pady=(0, 8))
        self.create_labeled_entry(parametric_frame, "CdA Range (min,max,step)", "0.20,1.00,0.05", "param_cda_range")
        self.create_labeled_entry(parametric_frame, "Crr Range (min,max,step)", "0.005,0.030,0.001", "param_crr_range")
        self.create_labeled_entry(parametric_frame, "Speed Sweep (min,max,points) km/h", "0.1,120,2000", "param_speed_sweep")
        self.create_labeled_entry(parametric_frame, "Accel Target Speed (km/h)", "60", "param_accel_target_speed")
        self.create_labeled_entry(parametric_frame, "Accel Max Time (s)", "60", "param_accel_max_time")
        self.create_labeled_entry(parametric_frame, "Max Gradient Search (%)", "45", "param_grad_max")
        self.create_labeled_entry(parametric_frame, "Gradient Step (%)", "0.25", "param_grad_step")

        # --- Engine Analysis Section (built in its own method) ---
        self._build_engine_analysis_section(input_frame)

        # --- MTPA / MTPV Section (built in its own method) ---
        self._build_mtpa_mtpv_section(input_frame)

        # --- Mechanical Design Section (built in its own method) ---
        self._build_mech_design_section(input_frame)

        # --- Motor BOM Section (built in its own method) ---
        self._build_bom_section(input_frame)

        # --- Motor Curve Upload Section ---
        # NB: despite the key name, in Drive Cycle mode this section hosts the
        # *motor* data upload row (packed in via show_sections_for_analysis).
        # The actual drive-cycle Excel upload lives in the 'drivecycle_data'
        # section, so title this one for what it really contains.
        self.sections['drive_cycle'] = self.create_section(input_frame, "Motor Curve Upload", "#f1f5f9")
        drive_cycle_frame = self.sections['drive_cycle']
    

        # --- Row for Motor Data Excel ---
        self.motor_data_row = ctk.CTkFrame(input_frame, fg_color="transparent")

        self.motor_data_upload_button = ctk.CTkButton(
            self.motor_data_row,
            text="Upload Motor Data Excel",
            command=self.load_motor_data_excel
        )
        self.motor_data_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)

        self.motor_data_indicator = ctk.CTkLabel(
            self.motor_data_row,
            text="\u274C",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.motor_data_indicator.pack(side="left", padx=(0, 6))

        self.motor_data_delete_button = ctk.CTkButton(
            self.motor_data_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=self.delete_motor_data,
            width=60
        )
        self.motor_data_delete_button.pack(side="left")
        self.motor_data_delete_button.configure(state="disabled")

        self.motor_data_status_row = ctk.CTkFrame(input_frame, fg_color="transparent")
        self.motor_data_filename_label = ctk.CTkLabel(
            self.motor_data_status_row,
            text="No motor curve file selected",
            font=("Segoe UI", 10),
            text_color=COLORS['text_muted'],
            anchor="w"
        )
        self.motor_data_filename_label.pack(side="left", padx=(0, 6))

        # --- Plotting Data Section ---
        self.sections['plotting_data'] = self.create_section(input_frame, "Drive Cycle Plot Controls", "#f1f5f9")
        plotting_data_frame = self.sections['plotting_data']

        self.plot_drive_cycle_button = ctk.CTkButton(
            plotting_data_frame,
            text="Plot Drive Cycle",
            command=self.plot_drive_cycle_button_callback,
            state="disabled"
        )
        self.plot_drive_cycle_button.pack(side="top", pady=(8, 2), padx=8, fill='x')

        self.plot_torque_speed_button = ctk.CTkButton(
            plotting_data_frame,
            text="Plot Torque-Speed Plot",
            command=self.plot_torque_speed_button_callback,
            state="disabled"
        )
        self.plot_torque_speed_button.pack(side="top", pady=(2, 2), padx=8, fill='x')

        # Dedicated heatmap plot (parallel to the scatter torque-speed plot).
        self.plot_torque_speed_heatmap_button = ctk.CTkButton(
            plotting_data_frame,
            text="Plot Torque-Speed Heatmap",
            command=self.plot_torque_speed_heatmap_callback,
            state="disabled"
        )
        self.plot_torque_speed_heatmap_button.pack(side="top", pady=(2, 2), padx=8, fill='x')


        self.heatmap_var = ctk.BooleanVar(value=False)
        heatmap_frame = ctk.CTkFrame(plotting_data_frame, fg_color="transparent")
        heatmap_frame.pack(side="top", pady=(0, 8), padx=8, fill='x')

        self.heatmap_label = ctk.CTkLabel(
            heatmap_frame,
            text="Heat Map",
            font=("Segoe UI", 12),
            text_color=COLORS['primary']
        )
        self.heatmap_label.pack(side="left", padx=(0, 8))

        self.heatmap_switch = ctk.CTkSwitch(
            heatmap_frame,
            text="",
            variable=self.heatmap_var,
            onvalue=True,
            offvalue=False,
            command=self.on_heatmap_toggle
)
        
        self.heatmap_switch.pack(side="left")
        # Add Show Positive Torque Only toggle just under the heatmap toggle
        self.show_positive_torque_var = ctk.BooleanVar(value=False)
        self.show_positive_torque_switch = ctk.CTkSwitch(
            heatmap_frame,
            text="Show Positive Torque Only",
            variable=self.show_positive_torque_var,
            onvalue=True,
            offvalue=False,
            command=self.on_show_positive_torque_toggle
        )
        self.show_positive_torque_switch.pack(side="left", padx=(16, 0))
        # Add Bin Factor entry under the toggles
        # bin_factor_frame = ctk.CTkFrame(heatmap_frame, fg_color="transparent")
        # bin_factor_frame.pack(side="left", padx=(16, 0))

        # bin_factor_label = ctk.CTkLabel(
        #     bin_factor_frame,
        #     text="Bin Factor:",
        #     font=("Segoe UI", 12),
        #     text_color=COLORS['primary']
        # )
        # bin_factor_label.pack(side="left", padx=(0, 4))

        # self.bin_factor_entry = ctk.CTkEntry(
        #     bin_factor_frame,
        #     width=50,
        #     font=("Segoe UI", 12)
        # )
        # self.bin_factor_entry.insert(0, "10")
        # self.bin_factor_entry.pack(side="left")
        #BIN FACTOR ENTRY
        bin_factor_frame = ctk.CTkFrame(plotting_data_frame, fg_color="transparent")
        bin_factor_frame.pack(side="left", padx=(16, 0))

        bin_factor_label = ctk.CTkLabel(
            bin_factor_frame,
            text="Bin width  RPM:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary']
        )
        bin_factor_label.pack(side="left", padx=(0, 4))

        # Heatmap bin *widths*: each speed bin is this many RPM wide and each
        # torque bin this many Nm tall (was a bin-count "grid size factor").
        self.bin_factor_x_entry = ctk.CTkEntry(
            bin_factor_frame,
            width=60,
            font=("Segoe UI", 12)
        )
        self.bin_factor_x_entry.insert(0, "200")
        self.bin_factor_x_entry.pack(side="left")

        ctk.CTkLabel(
            bin_factor_frame,
            text="Nm:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary']
        ).pack(side="left", padx=(8, 4))

        self.bin_factor_y_entry = ctk.CTkEntry(
            bin_factor_frame,
            width=60,
            font=("Segoe UI", 12)
        )
        self.bin_factor_y_entry.insert(0, "5")
        self.bin_factor_y_entry.pack(side="left")

        # Back-compat alias: some code/scenarios still reference bin_factor_entry.
        self.bin_factor_entry = self.bin_factor_x_entry

        # Re-render the current drive-cycle plot when a bin width changes, so the
        # grid updates in place (staying on the heatmap/torque-speed view).
        for _e in (self.bin_factor_x_entry, self.bin_factor_y_entry):
            _e.bind("<Return>", self.on_bin_size_change)
            _e.bind("<FocusOut>", self.on_bin_size_change)
        # upload drive cycle data excel
        self.sections['drivecycle_data'] = self.create_section(input_frame, "Upload Drivecycle Data Excel", "#f1f5f9")
        drivecycle_data_frame = self.sections['drivecycle_data']

        self.drive_cycle_upload_button = ctk.CTkButton(
            drivecycle_data_frame,
            text="Upload Drive Cycle Excel",
            command=self.upload_drive_cycle_popup
        )
        self.drive_cycle_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)

        self.drive_cycle_indicator = ctk.CTkLabel(
           drivecycle_data_frame,
            text="\u274C",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.drive_cycle_indicator.pack(side="left", padx=(0, 6))

        self.drive_cycle_delete_button = ctk.CTkButton(
            drivecycle_data_frame,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=self.delete_drive_cycle_data,
            width=60
        )
        self.drive_cycle_delete_button.pack(side="left")
        self.drive_cycle_delete_button.configure(state="disabled")

        self.drive_cycle_status_row = ctk.CTkFrame(drivecycle_data_frame, fg_color="transparent")
        self.drive_cycle_status_row.pack(fill="x", pady=(4, 0), padx=8)
        self.drive_cycle_filename_label = ctk.CTkLabel(
            self.drive_cycle_status_row,
            text="No drive cycle file selected",
            font=("Segoe UI", 10),
            text_color=COLORS['text_muted'],
            anchor="w"
        )
        self.drive_cycle_filename_label.pack(side="left", padx=(0, 6))

        # --- Efficiency Data Upload Section ---    
        
        self.sections['efficiency_data'] = self.create_section(input_frame, "Efficiency Maps (Motor & Controller)", "#f1f5f9")
        efficiency_data_frame = self.sections['efficiency_data']

        # Motor 1 row
        motor1_row = ctk.CTkFrame(efficiency_data_frame, fg_color="transparent")
        motor1_row.pack(fill="x", pady=(8, 2), padx=8)

        self.eff1_upload_button = ctk.CTkButton(
            motor1_row,
            text="Upload Motor Efficiency Map",
            command=lambda: self.upload_efficiency_excel(motor=1)
        )
        self.eff1_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)

        self.eff1_indicator = ctk.CTkLabel(
            motor1_row,
            text="\u274C",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.eff1_indicator.pack(side="left", padx=(0, 6))

        self.eff1_delete_button = ctk.CTkButton(
            motor1_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=lambda: self.delete_efficiency_data(motor=1),
            width=60
        )
        self.eff1_delete_button.pack(side="left")
        self.eff1_delete_button.configure(state="disabled")

        # Motor 2 row
        motor2_row = ctk.CTkFrame(efficiency_data_frame, fg_color="transparent")
        motor2_row.pack(fill="x", pady=(2, 8), padx=8)

        self.eff2_upload_button = ctk.CTkButton(
            motor2_row,
            text="Upload Controller Efficiency Map",
            command=lambda: self.upload_efficiency_excel(motor=2)
        )
        self.eff2_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)

        self.eff2_indicator = ctk.CTkLabel(
            motor2_row,
            text="\u274C",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.eff2_indicator.pack(side="left", padx=(0, 6))

        self.eff2_delete_button = ctk.CTkButton(
            motor2_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=lambda: self.delete_efficiency_data(motor=2),
            width=60
        )
        self.eff2_delete_button.pack(side="left")
        self.eff2_delete_button.configure(state="disabled")

        # Storage for efficiency data
        self.efficiency_data_1 = None
        self.efficiency_data_2 = None
        self.eff1_map_torques = None
        self.eff1_map_rpms = None
        self.eff1_map_matrix = None
        self.eff2_map_torques = None
        self.eff2_map_rpms = None
        self.eff2_map_matrix = None

        # --- Motor Input Parameters Section ---
        self.sections['motor_input_params'] = self.create_section(input_frame, "Motor Input Parameters", "#f1f5f9")
        motor_input_frame = self.sections['motor_input_params']

        # Motor 1 subsection
        motor1_frame = ctk.CTkFrame(motor_input_frame, fg_color="transparent")
        motor1_frame.pack(fill="x", pady=(4, 2), padx=8)
        motor1_label = ctk.CTkLabel(motor1_frame, text="Motor (map axes)", font=("Segoe UI", 12, "bold"), text_color=COLORS['primary'])
        motor1_label.pack(anchor="w", padx=4)

        self.create_labeled_entry(motor1_frame, "Max Speed (RPM)", " ", "motor1_max_speed")
        self.motor1_max_speed_manual = False
        self.motor1_max_speed.bind("<KeyRelease>", lambda e: setattr(self, "motor1_max_speed_manual", bool(self.motor1_max_speed.get().strip())))
        self.create_labeled_entry(motor1_frame, "Rated Speed (RPM)", " ", "motor1_rated_speed")
        self.motor1_rated_speed_manual = False
        self.motor1_rated_speed.bind("<KeyRelease>", lambda e: setattr(self, "motor1_rated_speed_manual", bool(self.motor1_rated_speed.get().strip())))
        self.create_labeled_entry(motor1_frame, "Max Torque (Nm)", " ", "motor1_max_torque")
        self.motor1_max_torque_manual = False
        self.motor1_max_torque.bind("<KeyRelease>", lambda e: setattr(self, "motor1_max_torque_manual", bool(self.motor1_max_torque.get().strip())))
        self.create_labeled_entry(motor1_frame, "Max Power (kW)", " ", "motor1_max_power")
        self.motor1_max_power_manual = False
        self.motor1_max_power.bind("<KeyRelease>", lambda e: setattr(self, "motor1_max_power_manual", bool(self.motor1_max_power.get().strip())))
        # Motor 2 subsection
        motor2_frame = ctk.CTkFrame(motor_input_frame, fg_color="transparent")
        motor2_frame.pack(fill="x", pady=(4, 2), padx=8)
        motor2_label = ctk.CTkLabel(motor2_frame, text="Controller (map axes)", font=("Segoe UI", 12, "bold"), text_color=COLORS['primary'])
        motor2_label.pack(anchor="w", padx=4)

        self.create_labeled_entry(motor2_frame, "Max Speed (RPM)", " ", "motor2_max_speed")
        self.motor2_max_speed_manual = False
        self.motor2_max_speed.bind("<KeyRelease>", lambda e: setattr(self, "motor2_max_speed_manual", bool(self.motor2_max_speed.get().strip())))
        self.create_labeled_entry(motor2_frame, "Rated Speed (RPM)", " ", "motor2_rated_speed")
        self.motor2_rated_speed_manual = False
        self.motor2_rated_speed.bind("<KeyRelease>", lambda e: setattr(self, "motor2_rated_speed_manual", bool(self.motor2_rated_speed.get().strip())))
        self.create_labeled_entry(motor2_frame, "Max Torque (Nm)", " ", "motor2_max_torque")
        self.motor2_max_torque_manual = False
        self.motor2_max_torque.bind("<KeyRelease>", lambda e: setattr(self, "motor2_max_torque_manual", bool(self.motor2_max_torque.get().strip())))
        self.create_labeled_entry(motor2_frame, "Max Power (kW)", " ", "motor2_max_power")
        self.motor2_max_power_manual = False
        self.motor2_max_power.bind("<KeyRelease>", lambda e: setattr(self, "motor2_max_power_manual", bool(self.motor2_max_power.get().strip())))
        # --- Plot Efficiency Map Section ---
        self.sections['plot_efficiency_map'] = self.create_section(input_frame, "Plot Efficiency Map", "#f1f5f9")
        plot_eff_map_frame = self.sections['plot_efficiency_map']
        self.show_drive_cycle_toggle_var = ctk.BooleanVar(value=False)
        self.show_drive_cycle_toggle = ctk.CTkSwitch(
            self.sections['plot_efficiency_map'],  # or the correct frame for the efficiency map controls
            text="Show Drive Cycle Data",
            variable=self.show_drive_cycle_toggle_var,
            onvalue=True,
            offvalue=False,
            command=self.plot_both_efficiency_maps  # or a wrapper that updates both maps
        )
        self.show_drive_cycle_toggle.pack(pady=4, padx=16, anchor="w")
        self.plot_eff1_button = ctk.CTkButton(
            plot_eff_map_frame,
            text="Plot Motor Efficiency Map",
            command=self.plot_efficiency_map_motor1
        )
        self.plot_eff1_button.pack(fill="x", padx=16, pady=(8, 2))

        self.plot_eff2_button = ctk.CTkButton(
            plot_eff_map_frame,
            text="Plot Controller Efficiency Map",
            command=self.plot_efficiency_map_motor2
        )
        self.plot_eff2_button.pack(fill="x", padx=16, pady=(2, 2))

        self.plot_eff_combined_button = ctk.CTkButton(
            plot_eff_map_frame,
            text="Plot Combined Map (Motor × Controller)",
            command=self.plot_efficiency_map_combined
        )
        self.plot_eff_combined_button.pack(fill="x", padx=16, pady=(2, 2))

        self.plot_eff_diff_button = ctk.CTkButton(
            plot_eff_map_frame,
            text="Plot Efficiency Difference Map",
            command=self.plot_efficiency_difference_map
        )
        self.plot_eff_diff_button.pack(fill="x", padx=16, pady=(2, 2))

        self.plot_eff_regen_button = ctk.CTkButton(
            plot_eff_map_frame,
            text="Plot Regen (Braking) Efficiency Map",
            command=self.plot_efficiency_map_regen
        )
        self.plot_eff_regen_button.pack(fill="x", padx=16, pady=(2, 2))

        self.compute_dce_button = ctk.CTkButton(
            plot_eff_map_frame,
            text="Compute Drive Cycle Efficiency",
            fg_color=COLORS['primary'],
            command=self.compute_drive_cycle_efficiency_metrics
        )
        self.compute_dce_button.pack(fill="x", padx=16, pady=(2, 8))
        self.drive_cycle_efficiency_label = ctk.CTkLabel(
            plot_eff_map_frame,
            text="Drive Cycle Efficiency: Not calculated yet",
            font=("Segoe UI", 11),
            text_color=COLORS['primary'],
            justify="left",
            anchor="w",
        )
        self.drive_cycle_efficiency_label.pack(fill="x", padx=16, pady=(0, 8))


        self.sections['env'] = self.create_section(input_frame, "Environment Conditions", "#f1f5f9")
        self.create_labeled_entry(self.sections['env'], "Ambient Temperature (°C)", "25", "ambient_temp")
        self.create_labeled_entry(self.sections['env'], "Ambient Pressure (kPa)", "1.01325", "ambient_pressure")
        # Make explicit what these two fields actually influence, so users
        # don't expect the drag force itself to change with temperature.
        ctk.CTkLabel(
            self.sections['env'],
            text=("Note: temperature/pressure only adjust the CdA estimate.\n"
                  "Aero drag force uses fixed air density 1.225 kg/m³, unless\n"
                  "'Altitude-corrected air density' below is ON (Range analysis)."),
            font=("Segoe UI", 10),
            text_color=COLORS['text_muted'],
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))
        # Optional ISA altitude-corrected air density (off by default -> identical
        # to the fixed rho=1.225 used everywhere else). Read by Range analysis.
        r = self.create_control_row(self.sections['env'], "Altitude-corrected air density")
        self.alt_density_toggle = ctk.CTkSwitch(r, text="", width=44,
                                                progress_color=COLORS['primary'])
        self.alt_density_toggle.pack(side="right")
        self.create_labeled_entry(self.sections['env'], "Altitude (m)  [density model]", "0", "altitude_m")

        # --- Range Analysis sections (built in their own method) ---
        self._build_range_sections(input_frame)

        # --- Drive Cycle Properties Table Section ---
        self.sections['drive_cycle_props'] = self.create_section(input_frame, "Drive Cycle Properties", "#f1f5f9")
        self.drive_cycle_props_frame = self.sections['drive_cycle_props']
        self.drive_cycle_props_labels = {}  # Store label widgets for updating

        # --- Compare Std Motor Data Section ---
        self.sections['compare_std'] = self.create_section(input_frame, "Compare Standard Motor Data", "#f1f5f9")
        compare_frame = self.sections['compare_std']
        # --- Compare Std Motor Data Section Controls ---
        self.compare_std_plot_var = tk.StringVar(value="torque")  # "torque", "force", "acceleration"

        # Torque row: radio + dropdown
        torque_row = ctk.CTkFrame(compare_frame, fg_color="transparent")
        torque_row.pack(fill="x", pady=(8, 2), padx=8)
        self.torque_radio = ctk.CTkRadioButton(
            torque_row, text="Compare Torque Plot", variable=self.compare_std_plot_var, value="torque",
            command=self.update_plot
        )
        self.torque_radio.pack(side="left", padx=(0, 8))
        self.torque_compare_mode = ctk.CTkComboBox(
            torque_row, values=["Wheel", "Motor"], width=100, command=self.update_plot
        )
        self.torque_compare_mode.set("Wheel")
        self.torque_compare_mode.pack(side="left", padx=(0, 8))

        # Force row: radio only
        force_row = ctk.CTkFrame(compare_frame, fg_color="transparent")
        force_row.pack(fill="x", pady=(2, 2), padx=8)
        self.force_radio = ctk.CTkRadioButton(
            force_row, text="Compare Force Plot", variable=self.compare_std_plot_var, value="force",
            command=self.update_plot
        )
        self.force_radio.pack(side="left", padx=(0, 8))

        # Acceleration row: radio only
        accel_row = ctk.CTkFrame(compare_frame, fg_color="transparent")
        accel_row.pack(fill="x", pady=(2, 2), padx=8)
        self.accel_radio = ctk.CTkRadioButton(
            accel_row, text="Compare Acceleration Plot", variable=self.compare_std_plot_var, value="acceleration",
            command=self.update_plot
        )
        self.accel_radio.pack(side="left", padx=(0, 8))

        # Efficiency row: radio only -- compares the currently loaded Motor
        # efficiency map (Efficiency Maps section, below) against whichever
        # saved standard motor (below) was stored with its own map.
        eff_row = ctk.CTkFrame(compare_frame, fg_color="transparent")
        eff_row.pack(fill="x", pady=(2, 2), padx=8)
        self.compare_eff_radio = ctk.CTkRadioButton(
            eff_row, text="Compare Efficiency Map", variable=self.compare_std_plot_var, value="efficiency",
            command=self.update_plot
        )
        self.compare_eff_radio.pack(side="left", padx=(0, 8))
        # Button to choose std motors
        self.choose_std_motor_btn = ctk.CTkButton(
            compare_frame, text="Choose Std Motors", command=self.choose_std_motor_popup
        )
        self.choose_std_motor_btn.pack(pady=(8, 2), padx=8, fill="x")

        # Frame for the std motor table
        self.std_motor_table_frame = ctk.CTkFrame(compare_frame, fg_color=COLORS['section_bg'])
        self.std_motor_table_frame.pack(fill="x", pady=(8, 2), padx=8)
        self.std_motor_table_rows = []
        self.selected_std_motors = []  # List of dicts: {name, gear_ratio, wheel_radius, eff_map}
        
        self.save_std_motor_btn = ctk.CTkButton(
            compare_frame, text="Save Motor Data", command=self.save_std_motor_data_popup
        )
        self.save_std_motor_btn.pack(pady=(2, 8), padx=8, fill="x")
        # --- Graph Settings (per-analysis appearance controls) ---
        self.sections['graph_settings'] = self.create_section(input_frame, "Graph Settings", "#f1f5f9")
        self.attach_graph_settings_body(self.sections['graph_settings'])

        # --- Mapping: analysis type -> sections to show ---
        # 'graph_settings' is appended so each analysis carries its own panel;
        # populate_graph_settings() fills it with the controls for that view.
        self.analysis_sections = {
            "Powertrain Sizing": ['plot_mode','vehicle', 'dynamics', 'motor', 'thermal', 'sim', 'env', 'graph_settings'],
            "Acceleration":['vehicle', 'dynamics', 'motor', 'sim', 'env', 'graph_settings'],
            "Parametric Study": ['parametric_study', 'vehicle', 'dynamics', 'motor', 'env', 'graph_settings'],
            "Engine analysis": ['vehicle', 'dynamics', 'motor', 'env', 'engine_analysis', 'graph_settings'],
            "Drive Cycle Efficiency": ['drivecycle_data','efficiency_data','motor_input_params','plot_efficiency_map', 'thermal', 'graph_settings'],
            "Drive Cycle": ['drivecycle_data','drive_cycle','plotting_data', 'vehicle', 'dynamics', 'motor', 'graph_settings'],
            "Compare Standard Motor Data": ['compare_std', 'vehicle', 'dynamics', 'motor', 'sim', 'env', 'graph_settings'],
            # Flow: Analysis Type (above) -> Input Data (drive cycle, then the
            # Motor/Controller efficiency maps) -> Range plot view (foldable list)
            # -> Battery Inputs (now also holds regen cap + energy integration,
            # right below the plot view) -> remaining physics inputs.
            "Range analysis": ['drivecycle_data', 'efficiency_data', 'range_plot', 'range_battery', 'range_efficiency', 'vehicle', 'dynamics', 'motor', 'env', 'graph_settings'],
            # Pure d-q machine analysis: needs only its own motor-model inputs.
            "MTPA / MTPV (PMSM)": ['mtpa_mtpv', 'graph_settings'],
            # Pure mechanical hand-calc checks: own inputs only (see
            # mechanical_design.py; formulas from the EV motor mechanical
            # design formula handbook).
            "Mechanical Design (Motor)": ['mech_design', 'graph_settings'],
            # Hierarchical BOM with its own tree editor + sankey/pareto views.
            "Motor BOM (Cost & Weight)": ['bom', 'graph_settings'],
        }
        # The Motor/Controller efficiency maps are a single source of truth reused
        # everywhere (Drive Cycle Efficiency, Range, ...), so surface the upload
        # section in every analysis. It is collapsible and starts collapsed, so it
        # adds one header, not clutter. Inserted just before the Graph Settings
        # panel when present, else appended.
        for _name, _secs in self.analysis_sections.items():
            if _name in ("MTPA / MTPV (PMSM)", "Mechanical Design (Motor)",
                         "Motor BOM (Cost & Weight)"):
                continue  # pure model analyses; efficiency maps are irrelevant
            if 'efficiency_data' not in _secs:
                if 'graph_settings' in _secs:
                    _secs.insert(_secs.index('graph_settings'), 'efficiency_data')
                else:
                    _secs.append('efficiency_data')
        # The in-scroll Update Plot button was removed -- the pinned button at the
        # top of the panel is the single Update action. The attribute survives
        # (never packed) so existing pack_forget() calls stay harmless.
        self.update_button = ctk.CTkButton(input_frame, text="Update Plot", command=self.update_plot)
        self.params_label = ctk.CTkLabel(input_frame, text="", justify="left")
        self.params_label.pack(pady=10, anchor="w")

        self.show_sections_for_analysis(self.plot_type.get())


        # Place the plot canvas in the right panel
        self.figure, self.ax = plt.subplots(figsize=(10, 7))
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().pack(side="right", fill="both", expand=True)

        self._sync_shared_efficiency_ticks()
        self.plot_graph()


        # Bindings for manual edits
        self.crr.bind("<KeyRelease>", self.on_crr_manual_edit)
        self.cd_a.bind("<KeyRelease>", self.on_cda_manual_edit)

       

     
        
        # Tyre specification mapping. Static radii are COMPUTED from the size
        # designation (rim/2 + section height -- see tyre_static_radius_m in
        # ui_helpers.py) so every value is consistent with its spec; the old
        # hand-typed table had several entries 5-20 mm too large.
        tyre_specs = [
            # Small scooters (older IC + entry EV)
            "3.00-10", "3.50-10",
            # 12-inch scooters (MOST COMMON: TVS, Ola, Bajaj, Ather)
            "90/90-12", "90/100-12", "100/80-12", "110/70-12", "110/80-12",
            # 10-inch wider tyres (rare but used)
            "120/70-10",
            # Commuter bikes (IC engines - Splendor, Shine, Raider, etc.)
            "2.75-17", "3.00-17", "80/100-17", "90/90-17", "100/80-17",
            # Premium commuters / sporty (Apache, Pulsar, etc.)
            "110/70-17", "120/70-17", "130/70-17", "140/70-17",
            # Off-road / ADV / dual sport
            "90/90-19", "100/90-19", "110/90-19",
            "120/90-17",   # rear dual sport
            # Larger bikes / cruisers
            "150/80-16", "170/80-15",
        ]
        self.tyre_radius_map = {s: self.tyre_static_radius_m(s) for s in tyre_specs}

        # Track if wheel radius was manually edited
        self.wheel_radius_user_modified = False
        self.wheel_radius_entry = self.wheel_radius  # Already set by create_labeled_entry

        def mark_wheel_radius_manual(event):
            self.wheel_radius_user_modified = True
        self.wheel_radius_entry.bind("<KeyRelease>", mark_wheel_radius_manual)

        # Tyre specification label and dropdown
        tyre_label = ctk.CTkLabel(self.sections['vehicle'], text="Select Tire Specification (static radius × factor)", font=("Segoe UI", 12))
        tyre_label.pack(fill="x", padx=16, pady=(0, 2))

        self.tyre_spec_combo = ctk.CTkComboBox(
            self.sections['vehicle'],
            values=list(self.tyre_radius_map.keys()),
            command=self.set_radius_from_tyre_spec
        )
        self.tyre_spec_combo.set("Select Tyre Type")
        self.tyre_spec_combo.pack(fill="x", padx=16, pady=(0, 8))
        # Editing the factor re-applies the selected tyre's radius immediately
        # (previously the factor only took effect when re-picking a tyre).
        self.dynamic_radius_factor.bind("<KeyRelease>", self.on_dynamic_radius_factor_change)
        self.dynamic_radius_factor.bind("<FocusOut>", self.on_dynamic_radius_factor_change)
        self.speed_unit_combo.configure(command=self.on_plot_mode_change)
        self.plot_part_combo.configure(command=self.on_plot_mode_change)
        # Torque<->Force switches which sim fields are relevant (Nm vs N y-limit),
        # so go through update_plot (re-shows sections) rather than a bare replot.
        self.output_combo.configure(command=self.update_plot)
        # Reflect the initial Output in the Plotting Part lock state.
        self._sync_plot_part_lock()

        # Wire up the new cross-cutting features now that the canvas exists.
        self.enh_setup()

        # Local-LLM + RAG chat sidebar (closed by default; toggled from the toolbar).
        self.build_assistant_panel()

        # Start with every section collapsed for a compact, less-cluttered panel.
        self.collapse_all_sections()

        # Log uncaught Tk-callback errors to vmi_app.log instead of dying
        # silently on stderr; surface them in the status bar too.
        from . import applog
        self._log = applog.setup()

        def _tk_callback_error(exc, val, tb):
            import traceback
            self._log.error("Uncaught error in Tk callback:\n%s",
                            "".join(traceback.format_exception(exc, val, tb)))
            try:
                self.set_status(f"Error: {val}", "error")
            except Exception:
                pass
        self.report_callback_exception = _tk_callback_error

        # Session persistence: autosave the full UI state on close and restore
        # it on the next launch (silent no-op when no previous session exists).
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        restored = self.restore_last_session()
        if not restored:
            self.set_status("Ready. Choose an analysis and press Enter (or Update Plot).", "info")

    # ------------------------------------------------------------------ #
    #  Section builders (extracted verbatim from __init__ -- same widgets,
    #  same attribute names, same defaults; only the housing changed).
    # ------------------------------------------------------------------ #

    def _build_engine_analysis_section(self, input_frame):
        self.sections['engine_analysis'] = self.create_section(input_frame, "Engine Analysis", "#f1f5f9")
        engine_frame = self.sections['engine_analysis']

        self.engine_data_row = ctk.CTkFrame(engine_frame, fg_color="transparent")
        self.engine_data_row.pack(fill="x", pady=(8, 2), padx=8)
        self.engine_data_upload_button = ctk.CTkButton(
            self.engine_data_row,
            text="Upload Engine Torque-RPM Excel",
            command=self.load_engine_data_excel
        )
        self.engine_data_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)
        self.engine_data_indicator = ctk.CTkLabel(
            self.engine_data_row,
            text="❌",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.engine_data_indicator.pack(side="left", padx=(0, 6))
        self.engine_data_delete_button = ctk.CTkButton(
            self.engine_data_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=self.delete_engine_data,
            width=60
        )
        self.engine_data_delete_button.pack(side="left")
        self.engine_data_delete_button.configure(state="disabled")

        self.engine_eff_row = ctk.CTkFrame(engine_frame, fg_color="transparent")
        self.engine_eff_row.pack(fill="x", pady=(2, 8), padx=8)
        self.engine_eff_upload_button = ctk.CTkButton(
            self.engine_eff_row,
            text="Upload Gear Efficiency Excel (Sheets: G1..G6)",
            command=self.load_engine_efficiency_excel
        )
        self.engine_eff_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)
        self.engine_eff_indicator = ctk.CTkLabel(
            self.engine_eff_row,
            text="❌",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.engine_eff_indicator.pack(side="left", padx=(0, 6))
        self.engine_eff_delete_button = ctk.CTkButton(
            self.engine_eff_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=self.delete_engine_efficiency_data,
            width=60
        )
        self.engine_eff_delete_button.pack(side="left")
        self.engine_eff_delete_button.configure(state="disabled")

        engine_mode_row = ctk.CTkFrame(engine_frame, fg_color="transparent")
        engine_mode_row.pack(fill="x", pady=(0, 6), padx=8)
        ctk.CTkLabel(
            engine_mode_row,
            text="Plot Output:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary']
        ).pack(side="left", padx=(0, 8))
        self.engine_output_combo = ctk.CTkComboBox(
            engine_mode_row,
            values=["Wheel Torque (Nm)", "Wheel Force (N)"],
            font=("Segoe UI", 12),
            width=180,
            command=lambda _choice: self.plot_graph(),
        )
        self.engine_output_combo.set("Wheel Torque (Nm)")
        self.engine_output_combo.pack(side="left")

        self.create_labeled_entry(engine_frame, "Gear 1 Ratio", "31.15", "engine_gear_ratio_1")
        self.create_labeled_entry(engine_frame, "Gear 2 Ratio", "19.83", "engine_gear_ratio_2")
        self.create_labeled_entry(engine_frame, "Gear 3 Ratio", "14.24", "engine_gear_ratio_3")
        self.create_labeled_entry(engine_frame, "Gear 4 Ratio", "11.21", "engine_gear_ratio_4")
        self.create_labeled_entry(engine_frame, "Gear 5 Ratio", "9.40", "engine_gear_ratio_5")
        self.create_labeled_entry(engine_frame, "Gear 6 Ratio", "0", "engine_gear_ratio_6")

        self.engine_results_label = ctk.CTkLabel(
            engine_frame,
            text="",
            justify="left",
            font=("Segoe UI", 11),
            text_color=COLORS['primary']
        )
        self.engine_results_label.pack(anchor="w", padx=16, pady=(2, 8))

        self.engine_dataframe = None
        self.engine_efficiency_curves = {}
        self.engine_secondary_ax = None
        self.motor_curve_source = None

    def _build_range_sections(self, input_frame):
        # --- Range Analysis: Battery Inputs ---
        self.sections['range_battery'] = self.create_section(input_frame, "Range Analysis - Battery Inputs", "#f1f5f9")
        range_battery_frame = self.sections['range_battery']
        self.create_labeled_entry(range_battery_frame, "Cells Parallel", "14", "cells_parallel")
        self.create_labeled_entry(range_battery_frame, "Cells Series", "7", "cells_series")
        self.create_labeled_entry(range_battery_frame, "Cell Capacity (Ah)", "4.8", "cell_capacity")
        self.create_labeled_entry(range_battery_frame, "Cell Voltage (V)", "3.7", "cell_voltage")
        self.create_labeled_entry(range_battery_frame, "Cell Efficiency (%)", "100", "cell_efficiency")
        self.create_labeled_entry(range_battery_frame, "Depth of Discharge - DoD (%)", "95", "dod")
        self.create_labeled_entry(range_battery_frame, "Auxiliary Loss (W)", "25", "aux_loss")
        r = self.create_control_row(range_battery_frame, "Regen power cap (W)  [blank = none]")
        self.regen_cap_w = ctk.CTkEntry(r, width=110, placeholder_text="no cap")
        self.regen_cap_w.pack(side="right")
        r = self.create_control_row(range_battery_frame, "Energy integration")
        self.integration_method = ctk.CTkSegmentedButton(
            r, values=["Rectangular", "Trapezoidal"])
        self.integration_method.set("Rectangular")
        self.integration_method.pack(side="right")

        # --- Range Analysis: Efficiency Inputs ---
        self.sections['range_efficiency'] = self.create_section(input_frame, "Range Analysis - Motor/Controller Efficiency", "#f1f5f9")
        range_eff_frame = self.sections['range_efficiency']
        self.range_eff_auto_note = ctk.CTkLabel(
            range_eff_frame,
            text="Auto-linked: Motor 1 map -> Range Motor, Motor 2 map -> Range Controller (from Drive Cycle Efficiency section)",
            font=("Segoe UI", 10),
            text_color=COLORS['text'],
            justify="left",
            anchor="w",
        )
        self.range_eff_auto_note.pack(fill="x", padx=16, pady=(4, 2))

        self.range_motor_eff_row = ctk.CTkFrame(range_eff_frame, fg_color="transparent")
        self.range_motor_eff_row.pack(fill="x", pady=(8, 2), padx=8)
        self.range_motor_eff_upload_button = ctk.CTkButton(
            self.range_motor_eff_row,
            text="Upload Motor Efficiency Map Excel",
            command=lambda: self.upload_range_efficiency_map(kind="motor")
        )
        self.range_motor_eff_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)
        self.range_motor_eff_indicator = ctk.CTkLabel(
            self.range_motor_eff_row,
            text="❌",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.range_motor_eff_indicator.pack(side="left", padx=(0, 6))
        self.range_motor_eff_delete_button = ctk.CTkButton(
            self.range_motor_eff_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=lambda: self.delete_range_efficiency_map(kind="motor"),
            width=60
        )
        self.range_motor_eff_delete_button.pack(side="left")
        self.range_motor_eff_delete_button.configure(state="disabled")

        self.range_controller_eff_row = ctk.CTkFrame(range_eff_frame, fg_color="transparent")
        self.range_controller_eff_row.pack(fill="x", pady=(2, 8), padx=8)
        self.range_controller_eff_upload_button = ctk.CTkButton(
            self.range_controller_eff_row,
            text="Upload Controller Efficiency Map Excel",
            command=lambda: self.upload_range_efficiency_map(kind="controller")
        )
        self.range_controller_eff_upload_button.pack(side="left", padx=(0, 6), fill='x', expand=True)
        self.range_controller_eff_indicator = ctk.CTkLabel(
            self.range_controller_eff_row,
            text="❌",
            text_color=COLORS['warning'],
            font=("Segoe UI", 18)
        )
        self.range_controller_eff_indicator.pack(side="left", padx=(0, 6))
        self.range_controller_eff_delete_button = ctk.CTkButton(
            self.range_controller_eff_row,
            text="Delete",
            fg_color=COLORS['warning'],
            text_color="white",
            command=lambda: self.delete_range_efficiency_map(kind="controller"),
            width=60
        )
        self.range_controller_eff_delete_button.pack(side="left")
        self.range_controller_eff_delete_button.configure(state="disabled")

        self.create_labeled_entry(range_eff_frame, "Motor Efficiency Constant (0-1)", "0.90", "motor_eff_const")
        self.create_labeled_entry(range_eff_frame, "Controller Efficiency Constant (0-1)", "0.95", "controller_eff_const")

        self.range_motor_efficiency_map = None
        self.range_motor_eff_map_torques = None
        self.range_motor_eff_map_rpms = None
        self.range_controller_efficiency_map = None
        self.range_controller_eff_map_torques = None
        self.range_controller_eff_map_rpms = None

        # --- Range Analysis: Plot View ---
        self.sections['range_plot'] = self.create_section(input_frame, "Range Analysis - Plot View", "#f1f5f9")
        range_plot_frame = self.sections['range_plot']
        range_plot_row = ctk.CTkFrame(range_plot_frame, fg_color="transparent")
        range_plot_row.pack(fill="x", padx=8, pady=(4, 0))
        ctk.CTkLabel(
            range_plot_row,
            text="Select Plot:",
            font=("Segoe UI", 12),
            text_color=COLORS['primary']
        ).pack(side="left", padx=(16, 8), pady=10)
        # Foldable (dropdown) list rather than an always-expanded segmented
        # button -- same 9 views, far less horizontal space.
        self.range_plot_toggle = ctk.CTkComboBox(
            range_plot_row,
            values=[
                "All",
                "Power",
                "Energy",
                "C-rate",
                "Loss",
                "Waterfall",
                "Drive",
                "M Eff",
                "C Eff",
            ],
            width=160,
            command=lambda _choice: self.plot_graph()
        )
        self.range_plot_toggle.set("All")
        self.range_plot_toggle.pack(side="left", padx=(0, 8), pady=10)
        self.range_toggle_hint = ctk.CTkLabel(
            range_plot_frame,
            text="Drive=Drive Cycle, M Eff=Motor Map, C Eff=Controller Map",
            font=("Segoe UI", 10),
            text_color=COLORS['text']
        )
        self.range_toggle_hint.pack(fill="x", padx=16, pady=(0, 4))

        self.range_results_frame = ctk.CTkFrame(range_plot_frame, fg_color="transparent")
        self.range_results_frame.pack(fill="x", padx=8, pady=(2, 8))
        self.range_results_label = ctk.CTkLabel(
            self.range_results_frame,
            text="Range summary will appear here after calculation.",
            justify="left",
            font=("Segoe UI", 12),
            text_color=COLORS['primary'],
            anchor="w",
        )
        self.range_results_label.pack(fill="x", padx=8)


