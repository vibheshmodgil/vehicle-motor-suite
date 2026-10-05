"""Structured inventory traced to the two app tabs and shared calculations."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "capability_map.json"
rows = []


def add(feature, parameter, description, unit, inputs, output, source, location, dependencies=(), reference="docs/VMI_User_Guide.html"):
    rows.append(dict(feature=feature, parameter=parameter, description=description,
                     unit=unit, inputs=list(inputs), output=output,
                     calculation_source=source, code_location=location,
                     dependencies=list(dependencies), relevant_documentation_or_reference=reference))


V = "vmi/app.py:Vehicle Parameters"
M = "vmi/app.py:Motor Performance Parameters"
D = "vmi/app.py:Vehicle Dynamics Inputs"
E = "vmi/app.py:Environment Conditions"
T = "vmi/torque_force.py"
P = "vmi/parametric.py"
A = "vmi/calc_ext.py"
for parameter, description, unit, location in [
    ("m_ref", "Reference vehicle mass entered by user; lookup may map it to m_i.", "kg", V),
    ("rear_load_ratio", "Used in automatic Crr lookup.", "ratio", V),
    ("wheel_radius", "Dynamic wheel radius for speed/RPM and torque/force conversion.", "m", V),
    ("dynamic_radius_factor", "Scales tyre-spec static radius when tyre picker is used.", "ratio", V),
    ("gear_ratio", "Motor-to-wheel reduction ratio.", "ratio", V),
    ("gear_efficiency", "Torque transfer efficiency in the reduction.", "ratio", V),
    ("wheel_inertia", "Total wheel inertia reflected into acceleration inertial mass.", "kg m²", V),
    ("crr", "Optional rolling-resistance coefficient; auto estimated if blank.", "ratio", D),
    ("cd_a", "Optional drag area; auto estimated if blank.", "m²", D),
    ("gradients", "Road gradients drawn as resistance curves; percent or degrees selector.", "%", D),
    ("peak_torque", "Peak motor torque for theoretical envelope.", "N m", M),
    ("peak_power", "Peak motor power for theoretical envelope.", "kW", M),
    ("continuous_power", "Power ceiling for continuous torque curve.", "kW", M),
    ("peak_to_rated_torque_ratio", "Divides peak torque to make rated torque plateau.", "ratio", M),
    ("batt_voltage", "Optional voltage for DC shaft-power cap.", "V", M),
    ("batt_current_limit", "Optional battery DC current for shaft-power cap.", "A", M),
    ("batt_to_shaft_eff", "Constant cap efficiency fallback without maps.", "ratio", M),
    ("ambient_temp", "Used in automatic CdA estimate; does not directly alter drag density here.", "°C", E),
    ("ambient_pressure", "Used in automatic CdA estimate; does not directly alter drag density here.", "bar-like input", E),
]:
    add("shared_input", parameter, description, unit, [], False, "UI input", location)

for parameter, description, unit, location in [
    ("gradient_unit_combo", "Interpret road gradients as percent grade or incline degrees.", "selector", D),
    ("motor_curve_upload", "Optional RPM-torque curve replaces the theoretical peak envelope.", "RPM and N m table", "vmi/data_io.py:load_motor_data_excel"),
    ("motor_efficiency_map_upload", "Optional motor efficiency grid used by the battery power cap.", "speed/torque/efficiency table", "vmi/data_io.py"),
    ("controller_efficiency_map_upload", "Optional controller efficiency grid used by the battery power cap.", "speed/torque/efficiency table", "vmi/data_io.py"),
    ("battery_eff_smoothing_switch", "Optional smoothing of efficiency maps for the battery limit; off by default.", "boolean", M),
    ("thermal_overlay_switch", "Show optional thermal duty points on the powertrain plot.", "boolean", "vmi/app.py:Thermal Load Points"),
    ("thermal_speed_unit_combo", "Interpret thermal point speed as vehicle km/h or motor RPM.", "selector", "vmi/app.py:Thermal Load Points"),
    ("thermal_points", "Optional gradient, speed and duration duty points for capability overlay.", "%, km/h or rpm, s", "vmi/app.py:Thermal Load Points"),
    ("output_combo", "Select Torque or Force display in Powertrain Sizing.", "selector", "vmi/app.py:Analysis Type"),
    ("plot_part_combo", "Select motor or wheel side where applicable; force is at wheel.", "selector", "vmi/app.py:Simulation Settings"),
    ("speed_unit_combo", "Select vehicle km/h, vehicle RPM or motor RPM x-axis.", "selector", "vmi/app.py:Simulation Settings"),
    ("xlim/ylim", "Optional manual axis bounds; these do not cap acceleration simulation physics.", "display bounds", "vmi/app.py:Simulation Settings"),
]:
    add("shared_control_or_dataset", parameter, description, unit, [], False, "UI control or uploaded data", location)

for parameter, description, unit, location in [
    ("crr_speed_coeff", "Crr1 is entered in the shared vehicle section but is not applied in these two views.", "per m/s", V),
    ("altitude_m", "Altitude density input is not used by these views' fixed 1.225 kg/m^3 drag expression.", "m", E),
]:
    add("shared_ui_not_applied_here", parameter, description, unit, [], False, "UI input", location)

for parameter, description, unit, inputs, source, location in [
    ("m_i", "Model calculation mass from mass lookup, or entered mass outside table with manual road coefficients.", "kg", ["m_ref"], "mass table lookup", "vmi/physics.py:calculate_crr_cd_a"),
    ("Crr", "Effective rolling coefficient.", "ratio", ["m_ref", "rear_load_ratio", "crr"], "manual or lookup a/(m_i*rear_load_ratio*g)", "vmi/physics.py:calculate_crr_cd_a"),
    ("CdA", "Effective drag area.", "m²", ["m_ref", "ambient_temp", "ambient_pressure", "cd_a"], "manual or mass-table estimate", "vmi/physics.py:calculate_crr_cd_a"),
    ("motor_rpm", "Motor speed corresponding to vehicle speed.", "rpm", ["vehicle_speed", "wheel_radius", "gear_ratio"], "v/(2πr)*60*G", T+":_accel_wheel_force_fn"),
    ("base_speed_rpm", "Theoretical peak torque to constant-power transition.", "rpm", ["peak_power", "peak_torque"], "(P/T)*60/(2π)", T+":plot_torque_graph"),
    ("peak_motor_torque_curve", "Peak motor torque against RPM, from theoretical envelope or uploaded curve, then optional battery cap.", "N m", ["peak_torque", "peak_power", "motor_rpm", "motor_curve", "batt_voltage", "batt_current_limit"], "T_peak below base RPM else P/ω; uploaded curve interpolated", T+":plot_torque_graph"),
    ("continuous_motor_torque_curve", "Continuous torque plateau and power cap (uploaded curve can be scaled by ratio).", "N m", ["peak_torque", "peak_to_rated_torque_ratio", "continuous_power", "motor_rpm", "motor_curve"], "min(T_peak/ratio, P_cont/ω)", T+":plot_force_graph"),
    ("wheel_torque", "Motor shaft torque after gearing.", "N m", ["motor_torque", "gear_ratio", "gear_efficiency"], "T_m*G*eta_gear", T+":plot_torque_graph"),
    ("tractive_force", "Available wheel force.", "N", ["wheel_torque", "wheel_radius"], "T_w/r", T+":plot_force_graph"),
    ("road_load_force", "Rolling, aerodynamic and grade resistance.", "N", ["m_i", "Crr", "CdA", "vehicle_speed", "gradient"], "m g Crr cos(theta)+0.5*1.225*CdA*v²+m g sin(theta)", P+":_compute_resistive_force"),
    ("force_intersections", "Peak and continuous capability intersections with each configured gradient road-load curve.", "speed and N", ["tractive_force", "road_load_force", "gradients"], "linear interpolation of curve crossings", T+":plot_force_graph"),
    ("top_speed_report", "Flat-road first available-force/road-load crossing estimated for assistant/report on 0.1–160 km/h sweep.", "km/h", ["tractive_force", "road_load_force"], "first net-force zero crossing; 160 km/h sweep ceiling", "vmi/enhancements.py:_report_vehicle_capability; "+P+":_estimate_top_speed"),
    ("max_startable_gradient_report", "Largest 0–60% grade (0.5% steps) with any feasible sampled speed in report/assistant.", "%", ["tractive_force", "road_load_force", "gradient"], "grade search; max(force-road load)>=0", "vmi/enhancements.py:_report_vehicle_capability; "+P+":_estimate_max_gradability"),
]:
    add("powertrain", parameter, description, unit, inputs, True, source, location,
        dependencies=("numpy", "uploaded motor curve optional", "motor/controller efficiency maps optional"))

add("powertrain", "battery_shaft_power_cap",
    "Optional DC limit bounds motor shaft power after motor/controller efficiency or constant fallback.",
    "W", ["batt_voltage", "batt_current_limit", "batt_to_shaft_eff",
          "motor_efficiency_map_upload", "controller_efficiency_map_upload"], True,
    "Pdc=Vdc*Idc; Pshaft<=Pdc*eta",
    "vmi/ui_helpers.py:cap_torque_to_battery; vmi/calc_ext.py:battery_power_cap_w")
add("powertrain", "thermal_duty_overlay",
    "Optional duty-point motor torque/RPM and peak-capability utilization for entered speed, grade and duration.",
    "N m, rpm, %", ["thermal_points", "thermal_speed_unit_combo", "thermal_overlay_switch"], True,
    "steady road load to motor operating point", "vmi/ui_helpers.py:compute_thermal_load_points")

for parameter, description, unit, inputs, source, location in [
    ("target_speed", "User-chosen acceleration target.", "km/h", [], "UI input", "vmi/app.py:Simulation Settings"),
    ("max_time", "Simulation duration.", "s", [], "UI input", "vmi/app.py:Simulation Settings"),
    ("net_force_grid", "Available wheel force minus rolling, aerodynamic and grade forces on expanding speed grid.", "N", ["tractive_force", "road_load_force"], "F_available-F_res", T+":_accel_net_force_grid"),
    ("effective_inertial_mass", "Mass plus wheel rotational inertia divided by radius squared.", "kg", ["m_i", "wheel_inertia", "wheel_radius"], "m+J/r²", A+":effective_mass"),
    ("speed_time_curve", "Vehicle speed integrated from rest for full max_time.", "km/h vs s", ["net_force_grid", "effective_inertial_mass", "max_time"], "forward Euler at 0.001 s; a=Fnet/m_eff", A+":simulate_acceleration"),
    ("acceleration_plot_top_speed", "First positive-to-nonpositive net-force crossing on expanding speed grid.", "km/h", ["net_force_grid"], "linear interpolation of first crossing", A+":top_speed_from_net_force"),
    ("acceleration_plot_target_time", "First plotted time at or above configured target speed, if reached.", "s", ["speed_time_curve", "target_speed"], "first sampled crossing", T+":plot_vehicle_max_speed_vs_time"),
    ("acceleration_plot_final_speed", "Speed at configured simulation time.", "km/h", ["speed_time_curve", "max_time"], "last integrated speed", A+":simulate_acceleration"),
    ("acceleration_plot_settled_at", "First time acceleration drops below 0.01 m/s².", "s", ["speed_time_curve", "net_force_grid"], "threshold on a=Fnet/m_eff", A+":simulate_acceleration"),
    ("acceleration_report_target_time", "Flat-road assistant/report estimate using a separate 0.05 s integrator.", "s", ["target_speed", "tractive_force", "road_load_force", "effective_inertial_mass"], "time stepping at 0.05 s", P+":_estimate_acceleration_time"),
]:
    add("acceleration", parameter, description, unit, inputs, parameter not in ("target_speed", "max_time"), source, location,
        dependencies=("numpy", "uploaded motor curve optional", "battery cap optional"))

if __name__ == "__main__":
    OUT.write_text(json.dumps({"schema_version": 1, "scope": ["Powertrain Sizing", "Acceleration"],
        "capabilities": rows, "explicitly_unavailable_in_these_tabs": [
            "independent controller phase-current limit", "motor manufacturer maximum-RPM cutoff",
            "traction-limited acceleration", "distance versus time output",
            "controller thermal derating simulation", "certified speed or test pass/fail status"
        ]}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(rows)} capabilities")
