"""
Generate dummy Excel test files for the Vehicle <-> Motor Integration Suite.

Run once:  python generate_sample_data.py
Creates a `sample_data/` folder next to this script with one file per upload
slot in the app, in exactly the formats the loaders expect:

  drive_cycle.xlsx            -> Drive Cycle / Range  (Time s, Speed km/h)
  motor_data.xlsx             -> "Upload Motor Data Excel"  (Torque Nm, Speed RPM)
  engine_torque_rpm.xlsx      -> Engine analysis torque curve  (Torque Nm, RPM)
  gear_efficiency.xlsx        -> Engine gear efficiency, sheets G1..G6 (RPM, Efficiency)
  efficiency_map_motor1.xlsx  -> Drive Cycle Efficiency / Range motor map
  efficiency_map_motor2.xlsx  -> Drive Cycle Efficiency / Range controller map

The two efficiency-map files use the single layout that satisfies every reader
in the app: first column = torque axis, remaining column headers = RPM, cells =
efficiency in %.
"""

import os
import numpy as np
import pandas as pd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data")
os.makedirs(OUT_DIR, exist_ok=True)


def _path(name):
    return os.path.join(OUT_DIR, name)


# --------------------------------------------------------------------------- #
#  1. Drive cycle: a small synthetic urban cycle (accel / cruise / decel)     #
# --------------------------------------------------------------------------- #
def make_drive_cycle():
    dt = 1.0  # 1 Hz sampling
    segments = []  # (target_speed_kmh, hold_seconds)
    pattern = [
        (0, 5), (30, 12), (30, 20), (0, 8),     # short hop + stop
        (45, 15), (45, 30), (20, 10), (50, 18),
        (50, 40), (0, 12), (35, 10), (35, 25), (0, 10),
    ]
    speed = 0.0
    speeds = []
    for target, hold in pattern:
        # ramp toward target at ~1.2 (km/h)/s, then hold
        while abs(speed - target) > 1.2:
            speed += np.sign(target - speed) * 1.2
            speeds.append(speed)
        speed = target
        speeds.extend([target] * int(hold / dt))
    speeds = np.array(speeds, dtype=float)
    # gentle noise so it isn't perfectly flat
    speeds = np.clip(speeds + np.random.normal(0, 0.3, speeds.shape), 0, None)
    time_s = np.arange(len(speeds)) * dt
    df = pd.DataFrame({"Time (s)": time_s, "Speed (km/h)": np.round(speeds, 2)})
    df.to_excel(_path("drive_cycle.xlsx"), index=False)
    return len(df)


# --------------------------------------------------------------------------- #
#  2. Motor data: flat peak torque to base speed, then constant power          #
# --------------------------------------------------------------------------- #
def make_motor_data(peak_torque=120.0, base_rpm=1500.0, max_rpm=6000.0, n=120):
    rpm = np.linspace(0, max_rpm, n)
    torque = np.where(rpm <= base_rpm, peak_torque, peak_torque * base_rpm / np.maximum(rpm, 1e-6))
    df = pd.DataFrame({"Torque (Nm)": np.round(torque, 2), "Speed (RPM)": np.round(rpm, 1)})
    df.to_excel(_path("motor_data.xlsx"), index=False)
    return len(df)


# --------------------------------------------------------------------------- #
#  3. Engine torque vs RPM: a typical single-cylinder bump curve               #
# --------------------------------------------------------------------------- #
def make_engine_data(peak_torque=12.0, peak_rpm=6000.0, idle=1200.0, redline=9500.0, n=60):
    rpm = np.linspace(idle, redline, n)
    # parabola peaking at peak_rpm, scaled to peak_torque, never below ~55%
    shape = 1.0 - ((rpm - peak_rpm) / (redline - idle)) ** 2
    torque = peak_torque * np.clip(0.55 + 0.45 * shape, 0.3, 1.0)
    df = pd.DataFrame({"Torque (Nm)": np.round(torque, 2), "RPM": np.round(rpm, 0)})
    df.to_excel(_path("engine_torque_rpm.xlsx"), index=False)
    return len(df)


# --------------------------------------------------------------------------- #
#  4. Gear efficiency: one sheet per gear (G1..G6), columns RPM / Efficiency   #
# --------------------------------------------------------------------------- #
def make_gear_efficiency(idle=1200.0, redline=9500.0, n=40):
    rpm = np.linspace(idle, redline, n)
    with pd.ExcelWriter(_path("gear_efficiency.xlsx")) as writer:
        for g in range(1, 7):
            # higher gears a touch more efficient; mild rpm dependence
            base = 0.84 + 0.012 * g
            eff = base + 0.05 * np.exp(-((rpm - 5000) / 3000) ** 2)
            eff = np.clip(eff, 0.70, 0.97)
            pd.DataFrame({"RPM": np.round(rpm, 0),
                          "Efficiency": np.round(eff * 100, 2)}).to_excel(
                writer, sheet_name=f"G{g}", index=False)


# --------------------------------------------------------------------------- #
#  5/6. Efficiency maps: torque (rows) x RPM (cols), cells = efficiency %      #
# --------------------------------------------------------------------------- #
def _efficiency_grid(torque_axis, rpm_axis, peak_eff, t_peak, r_peak, t_sigma, r_sigma):
    T, R = np.meshgrid(torque_axis, rpm_axis, indexing="ij")
    eff = peak_eff * np.exp(-(((T - t_peak) / t_sigma) ** 2 + ((R - r_peak) / r_sigma) ** 2))
    eff = np.clip(eff, 0.55, peak_eff)  # floor so low-load corners aren't absurd
    return eff * 100.0  # percent


def make_efficiency_map(filename, peak_eff, t_peak, r_peak):
    torque_axis = np.arange(10, 121, 10, dtype=float)      # 10..120 Nm
    rpm_axis = np.arange(0, 6001, 500, dtype=float)         # 0..6000 RPM
    eff_pct = _efficiency_grid(torque_axis, rpm_axis, peak_eff, t_peak, r_peak,
                               t_sigma=70.0, r_sigma=2500.0)
    # First column = torque axis (named), remaining columns = RPM headers.
    df = pd.DataFrame(np.round(eff_pct, 2),
                      index=np.round(torque_axis, 1),
                      columns=np.round(rpm_axis, 1))
    df.index.name = "Torque\\RPM"
    df.to_excel(_path(filename))  # index written as the first column
    return df.shape


if __name__ == "__main__":
    np.random.seed(0)
    n_dc = make_drive_cycle()
    n_motor = make_motor_data()
    n_engine = make_engine_data()
    make_gear_efficiency()
    s1 = make_efficiency_map("efficiency_map_motor1.xlsx", peak_eff=0.95, t_peak=70, r_peak=3000)
    s2 = make_efficiency_map("efficiency_map_motor2.xlsx", peak_eff=0.93, t_peak=60, r_peak=3500)

    print(f"Sample data written to: {OUT_DIR}")
    print(f"  drive_cycle.xlsx           ({n_dc} rows)")
    print(f"  motor_data.xlsx            ({n_motor} rows)")
    print(f"  engine_torque_rpm.xlsx     ({n_engine} rows)")
    print(f"  gear_efficiency.xlsx       (sheets G1..G6)")
    print(f"  efficiency_map_motor1.xlsx (torque x rpm = {s1[0]} x {s1[1]})")
    print(f"  efficiency_map_motor2.xlsx (torque x rpm = {s2[0]} x {s2[1]})")
