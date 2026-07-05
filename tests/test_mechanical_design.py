"""Golden-value regression tests for vmi/mechanical_design.py.

These lock the handbook formulas (Shigley / Roark / Timoshenko / DIN 7190 /
ISO 281 / ISO 21940-11) as implemented — a failure means a formula changed,
which must only happen on an explicit request. Reference case is the default
UI inputs (a typical 2W traction motor).
"""

import numpy as np
import pytest

from vmi.mechanical_design import (
    rotor_disc_stresses, rotor_peak_hoop_stress, von_mises_plane,
    rotor_burst_speed,
    shaft_bend_modulus, shaft_torsion_stress, shaft_twist_rad,
    shaft_static_sf, marin_endurance_limit, de_goodman_sf,
    de_goodman_diameter, static_diameter,
    pressfit_pressure, hub_bore_hoop_stress, hub_yield_onset_pressure,
    pressfit_torque_capacity, pressfit_axial_force,
    centrifugal_interference_loss, thermal_interference_change,
    assembly_delta_t, loss_of_contact_speed,
    bearing_equivalent_load, bearing_static_equivalent, bearing_l10_hours,
    shaft_bending_stiffness, rotor_critical_speed_rpm,
    permissible_unbalance_gmm,
)

RPM_TO_RAD = 2.0 * np.pi / 60.0


# ---------------------------------------------------------------------------#
#  Rotor stress & burst speed                                                #
# ---------------------------------------------------------------------------#

class TestRotorStress:
    # OD 100 mm, bore 30 mm, M270-class steel, 12000 RPM x 1.2 overspeed.
    W_OS = 14400.0 * RPM_TO_RAD

    def test_peak_hoop_hollow(self):
        assert rotor_peak_hoop_stress(0.015, 0.05, 7650, 0.29, self.W_OS) \
            == pytest.approx(72_929_590.97, rel=1e-9)

    def test_peak_hoop_solid(self):
        assert rotor_peak_hoop_stress(0.0, 0.05, 7650, 0.29, self.W_OS) \
            == pytest.approx(35_770_051.93, rel=1e-9)

    def test_bore_boundary_conditions(self):
        # At the bore: sigma_r = 0 and sigma_t equals the peak-hoop closed form.
        st, sr = rotor_disc_stresses(0.015, 0.015, 0.05, 7650, 0.29, self.W_OS)
        assert sr == pytest.approx(0.0, abs=1.0)
        assert st == pytest.approx(
            rotor_peak_hoop_stress(0.015, 0.05, 7650, 0.29, self.W_OS), rel=1e-9)

    def test_outer_edge_radial_free(self):
        _, sr = rotor_disc_stresses(0.05, 0.015, 0.05, 7650, 0.29, self.W_OS)
        assert sr == pytest.approx(0.0, abs=1.0)

    def test_small_bore_doubles_solid_center_stress(self):
        # Handbook §1.1: r_i -> 0 hoop stress tends to 2x the solid-disc value.
        hollow = rotor_peak_hoop_stress(1e-6, 0.05, 7650, 0.29, self.W_OS)
        solid = rotor_peak_hoop_stress(0.0, 0.05, 7650, 0.29, self.W_OS)
        assert hollow / solid == pytest.approx(2.0, rel=1e-3)

    def test_von_mises_plane(self):
        assert von_mises_plane(100.0, 40.0) == pytest.approx(87.17797887, rel=1e-9)

    def test_burst_speed_hollow(self):
        rpm = rotor_burst_speed(0.015, 0.05, 7650, 0.29, 350e6) / RPM_TO_RAD
        assert rpm == pytest.approx(31_546.02, rel=1e-6)

    def test_burst_speed_solid(self):
        rpm = rotor_burst_speed(0.0, 0.05, 7650, 0.29, 350e6) / RPM_TO_RAD
        assert rpm == pytest.approx(45_043.98, rel=1e-6)

    def test_burst_speed_consistency(self):
        # Spinning at the burst speed must produce exactly the allowable stress.
        w = rotor_burst_speed(0.015, 0.05, 7650, 0.29, 350e6)
        assert rotor_peak_hoop_stress(0.015, 0.05, 7650, 0.29, w) \
            == pytest.approx(350e6, rel=1e-9)


# ---------------------------------------------------------------------------#
#  Shaft design                                                              #
# ---------------------------------------------------------------------------#

class TestShaftDesign:
    def test_bend_modulus_solid(self):
        assert shaft_bend_modulus(0.025) == pytest.approx(1.533981e-6, rel=1e-6)

    def test_torsion_stress(self):
        assert shaft_torsion_stress(120.0, 0.025) \
            == pytest.approx(39_113_918.81, rel=1e-9)

    def test_twist(self):
        assert shaft_twist_rad(120.0, 0.12, 79.3e9, 0.025) \
            == pytest.approx(4.7351024e-3, rel=1e-6)

    def test_static_sf(self):
        assert shaft_static_sf(15.0, 120.0, 0.025, 0.0, 620e6) \
            == pytest.approx(9.0578, rel=1e-4)

    def test_marin_machined_90(self):
        assert marin_endurance_limit(800e6, 0.025) \
            == pytest.approx(242_381_646.2, rel=1e-6)

    def test_marin_ground_99(self):
        assert marin_endurance_limit(800e6, 0.010, "Ground", "99%") \
            == pytest.approx(283_105_310.7, rel=1e-6)

    def test_de_goodman_sf(self):
        se = marin_endurance_limit(800e6, 0.025)
        assert de_goodman_sf(0.025, 0.0, 15.0, 0.0, 0.0, 120.0,
                             se, 800e6, 2.0, 1.7) \
            == pytest.approx(4.45138, rel=1e-5)

    def test_de_goodman_diameter(self):
        se = marin_endurance_limit(800e6, 0.025)
        d = de_goodman_diameter(15.0, 0.0, 0.0, 120.0, se, 800e6, 2.0, 1.7, 1.5)
        assert d == pytest.approx(0.0173969, rel=1e-5)
        # Round trip: SF at that diameter equals the target n.
        assert de_goodman_sf(d, 0.0, 15.0, 0.0, 0.0, 120.0,
                             se, 800e6, 2.0, 1.7) == pytest.approx(1.5, rel=1e-9)

    def test_static_diameter_round_trip(self):
        d = static_diameter(15.0, 120.0, 620e6, 1.5)
        assert d == pytest.approx(0.0137287, rel=1e-5)
        assert shaft_static_sf(15.0, 120.0, d, 0.0, 620e6) \
            == pytest.approx(1.5, rel=1e-9)


# ---------------------------------------------------------------------------#
#  Press / shrink fit                                                        #
# ---------------------------------------------------------------------------#

class TestPressFit:
    # d 30 mm, hub OD 100 mm, delta 35 um, steel-steel.
    P0 = 106_166_666.67

    def test_pressure(self):
        assert pressfit_pressure(35e-6, 0.03, 0.10) \
            == pytest.approx(self.P0, rel=1e-9)

    def test_pressure_same_material_shortcut(self):
        # For identical materials + solid shaft the general form must reduce
        # to Shigley's p = (E*delta/d) * (Do^2 - d^2) / (2 Do^2).
        expected = (200e9 * 35e-6 / 0.03) * (0.10 ** 2 - 0.03 ** 2) / (2 * 0.10 ** 2)
        assert pressfit_pressure(35e-6, 0.03, 0.10) \
            == pytest.approx(expected, rel=1e-9)

    def test_hub_hoop(self):
        assert hub_bore_hoop_stress(self.P0, 0.03, 0.10) \
            == pytest.approx(127_166_666.67, rel=1e-6)

    def test_yield_onset(self):
        assert hub_yield_onset_pressure(350e6, 0.03, 0.10) \
            == pytest.approx(159_250_000.0, rel=1e-9)

    def test_torque_capacity(self):
        assert pressfit_torque_capacity(self.P0, 0.12, 0.03, 0.06) \
            == pytest.approx(1080.645, rel=1e-5)

    def test_axial_force_relation(self):
        # T = F_axial * d / 2 for the same friction mobilisation.
        f = pressfit_axial_force(self.P0, 0.12, 0.03, 0.06)
        t = pressfit_torque_capacity(self.P0, 0.12, 0.03, 0.06)
        assert t == pytest.approx(f * 0.03 / 2.0, rel=1e-12)

    def test_centrifugal_loss(self):
        w = 12000.0 * RPM_TO_RAD
        assert centrifugal_interference_loss(
            0.03, 0.10, 0.0, 7650, 0.29, 200e9, 7850, 0.29, 200e9, w) \
            == pytest.approx(7.44831e-6, rel=1e-5)

    def test_thermal_change(self):
        assert thermal_interference_change(0.03, 12e-6, 11.7e-6, 80.0) \
            == pytest.approx(7.2e-7, rel=1e-9)

    def test_assembly_delta_t(self):
        assert assembly_delta_t(35e-6, 50e-6, 0.03, 12e-6) \
            == pytest.approx(236.111, rel=1e-5)

    def test_loss_of_contact_speed(self):
        rpm = loss_of_contact_speed(35e-6, 0.03, 0.10, 0.0,
                                    7650, 0.29, 200e9, 7850, 0.29, 200e9) / RPM_TO_RAD
        assert rpm == pytest.approx(26_012.76, rel=1e-6)

    def test_loss_of_contact_consistency(self):
        # At the loss-of-contact speed the centrifugal loss equals the
        # available interference.
        w = loss_of_contact_speed(35e-6, 0.03, 0.10, 0.0,
                                  7650, 0.29, 200e9, 7850, 0.29, 200e9)
        loss = centrifugal_interference_loss(
            0.03, 0.10, 0.0, 7650, 0.29, 200e9, 7850, 0.29, 200e9, w)
        assert loss == pytest.approx(35e-6, rel=1e-9)


# ---------------------------------------------------------------------------#
#  Bearing life                                                              #
# ---------------------------------------------------------------------------#

class TestBearingLife:
    def test_equivalent_load_above_e(self):
        assert bearing_equivalent_load(900, 400, 0.56, 1.6, 0.3) \
            == pytest.approx(1144.0)

    def test_equivalent_load_below_e(self):
        assert bearing_equivalent_load(900, 200, 0.56, 1.6, 0.3) \
            == pytest.approx(900.0)

    def test_static_equivalent_floor(self):
        # X0*Fr + Y0*Fa = 640 < Fr -> floored at Fr.
        assert bearing_static_equivalent(900, 200) == pytest.approx(900.0)
        assert bearing_static_equivalent(900, 2000) == pytest.approx(1540.0)

    def test_l10h_ball(self):
        assert bearing_l10_hours(13500, 900, 6000, 3.0) == pytest.approx(9375.0)

    def test_l10h_roller(self):
        assert bearing_l10_hours(13500, 900, 6000, 10.0 / 3.0) \
            == pytest.approx(23_120.74, rel=1e-6)


# ---------------------------------------------------------------------------#
#  Critical speed & balancing                                                #
# ---------------------------------------------------------------------------#

class TestCriticalSpeed:
    def test_shaft_stiffness(self):
        assert shaft_bending_stiffness(0.025, 0.0, 0.12, 200e9) \
            == pytest.approx(106_526_443.6, rel=1e-8)

    def test_critical_speed_rigid(self):
        assert rotor_critical_speed_rpm(6.0, 0.025, 0.0, 0.12, 200e9, 0.0) \
            == pytest.approx(40_236.90, rel=1e-6)

    def test_critical_speed_finite_bearings(self):
        # Finite bearing stiffness must always lower the critical speed.
        assert rotor_critical_speed_rpm(6.0, 0.025, 0.0, 0.12, 200e9, 120e6) \
            == pytest.approx(33_485.89, rel=1e-6)

    def test_unbalance_g63(self):
        assert permissible_unbalance_gmm(6.3, 6.0, 12000) \
            == pytest.approx(30.07935, rel=1e-9)
