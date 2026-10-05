"""Reproducible headless vehicle fixture using the application's calculation code."""
from __future__ import annotations

import numpy as np

from vmi.calc_ext import simulate_acceleration
from vmi.parametric import ParametricMixin
from vmi.physics import calculate_crr_cd_a
from vmi.torque_force import TorqueForceMixin


INPUTS = {
    "m_ref": 250.0, "rear_load_ratio": 0.5, "wheel_radius": 0.28,
    "gear_ratio": 8.0, "gear_efficiency": 0.95, "peak_torque": 30.0,
    "peak_power": 4.4, "continuous_power": 3.0,
    "peak_to_rated_torque_ratio": 2.0, "crr": 0.018, "cd_a": 0.6,
    "wheel_inertia": 0.0, "target_speed": 60.0, "max_time": 60.0,
}


class _CalculationAdapter(ParametricMixin, TorqueForceMixin):
    """Supply only UI-bound getters required by the unmodified math methods."""

    def get_gear_efficiency_value(self):
        return INPUTS["gear_efficiency"]

    def cap_torque_to_battery(self, torque, _rpm):
        return torque  # Both optional battery-cap inputs are blank in this fixture.

    def get_effective_inertial_mass(self, mass, wheel_radius=None):
        radius = wheel_radius or INPUTS["wheel_radius"]
        return mass + INPUTS["wheel_inertia"] / radius**2


def calculate_reference():
    app = _CalculationAdapter()
    p = calculate_crr_cd_a(
        INPUTS["m_ref"], INPUTS["rear_load_ratio"], crr=INPUTS["crr"], cd_a=INPUTS["cd_a"]
    )
    speeds = np.linspace(0.1, 160.0, 2000)
    force = app._compute_available_wheel_force(
        speeds, INPUTS["wheel_radius"], INPUTS["peak_torque"],
        INPUTS["peak_power"], INPUTS["gear_ratio"],
    )
    top = app._estimate_top_speed(speeds, force, p["m_i"], p["Crr"], p["CdA"])
    grade = app._estimate_max_gradability(speeds, force, p["m_i"], p["Crr"], p["CdA"], 60, 0.5)
    report_time = app._estimate_acceleration_time(
        speeds, force, p["m_i"], p["Crr"], p["CdA"],
        INPUTS["target_speed"], INPUTS["max_time"],
    )
    wheel_force_fn = app._accel_wheel_force_fn(
        INPUTS["wheel_radius"], INPUTS["gear_ratio"], INPUTS["gear_efficiency"],
        peak_torque=INPUTS["peak_torque"], peak_power_kw=INPUTS["peak_power"],
    )
    grid, net = app._accel_net_force_grid(wheel_force_fn, p)
    t, v, info = simulate_acceleration(
        grid, net, app.get_effective_inertial_mass(p["m_i"]), INPUTS["max_time"]
    )
    crossed = np.flatnonzero(v >= INPUTS["target_speed"])
    plot_time = float(t[crossed[0]]) if len(crossed) else None
    return {
        "inputs": INPUTS, "calculation_mass_kg": p["m_i"],
        "top_speed_report_kmh": float(top), "max_startable_gradient_pct": float(grade),
        "acceleration_report_0_60_s": float(report_time),
        "acceleration_plot_0_60_s": plot_time,
        "acceleration_plot_top_speed_kmh": info["top_speed_kmh"],
        "acceleration_plot_final_speed_kmh": info["final_speed_kmh"],
        "acceleration_plot_settled_at_s": info["settled_at_s"],
        "peak_motor_base_rpm": INPUTS["peak_power"] * 1000 / INPUTS["peak_torque"] * 60 / (2 * np.pi),
        "launch_wheel_torque_nm": INPUTS["peak_torque"] * INPUTS["gear_ratio"] * INPUTS["gear_efficiency"],
        "launch_wheel_force_n": float(wheel_force_fn(np.array([0.0]))[0]),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(calculate_reference(), indent=2))
