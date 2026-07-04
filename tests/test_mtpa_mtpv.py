"""Tests for the MTPA/MTPV d-q solver (vmi/mtpa_mtpv.py).

Physics checks against the model in knowledge_base/scenarios/MTPA_MTPV.pdf:
Te = 1.5*p*(psi*iq + (Ld-Lq)*id*iq), current circle Imax, voltage ellipse
(Ld*id+psi)^2 + (Lq*iq)^2 <= (Vmax/w_e)^2.
"""

import numpy as np
import pytest

from vmi.mtpa_mtpv import (
    REGION_FW,
    REGION_MTPA,
    REGION_MTPV,
    solve_mtpa_mtpv,
)

# A well-behaved IPM example (also the UI defaults): 4 pole pairs,
# Ld=0.15 mH, Lq=0.35 mH, psi=0.015 Wb, Imax=200 A, Vdc=72 V.
IPM = dict(pole_pairs=4, ld_h=0.15e-3, lq_h=0.35e-3, psi_pm=0.015,
           i_max=200.0, v_max=72.0 / np.sqrt(3.0), rpm_max=8000.0)


class TestSPMSM:
    """Surface PMSM (Ld == Lq): no reluctance torque, MTPA is id = 0."""

    def test_mtpa_point_is_pure_iq(self):
        sol = solve_mtpa_mtpv(pole_pairs=4, ld_h=0.2e-3, lq_h=0.2e-3,
                              psi_pm=0.02, i_max=100.0, v_max=40.0,
                              rpm_max=6000.0)
        # Peak torque = 1.5 * p * psi * Imax (id contributes nothing).
        assert sol["t_mtpa_max"] == pytest.approx(1.5 * 4 * 0.02 * 100.0, rel=1e-3)
        # In the MTPA region the operating id should be ~0.
        m = sol["region"] == REGION_MTPA
        assert np.any(m)
        assert np.allclose(sol["id"][m], 0.0, atol=100.0 * 0.01)


class TestIPM:
    def test_reluctance_torque_beats_magnet_only(self):
        sol = solve_mtpa_mtpv(**IPM)
        magnet_only = 1.5 * 4 * 0.015 * 200.0
        assert sol["t_mtpa_max"] > magnet_only  # Ld<Lq adds reluctance torque

    def test_envelope_constant_then_falling(self):
        sol = solve_mtpa_mtpv(**IPM)
        m = sol["region"] == REGION_MTPA
        assert np.any(m), "expected a constant-torque region"
        # Constant-torque region: envelope equals peak MTPA torque.
        assert np.allclose(sol["torque"][m], sol["t_mtpa_max"], rtol=1e-6)
        # Envelope never exceeds the MTPA peak and never goes negative.
        assert np.all(sol["torque"] <= sol["t_mtpa_max"] + 1e-9)
        assert np.all(sol["torque"] >= 0.0)
        # Envelope is (numerically) non-increasing.
        assert np.all(np.diff(sol["torque"]) <= sol["t_mtpa_max"] * 1e-3)

    def test_all_regions_present_and_ordered(self):
        sol = solve_mtpa_mtpv(**IPM)
        regions = sol["region"]
        assert regions[0] == REGION_MTPA
        present = set(int(r) for r in regions)
        assert REGION_FW in present or REGION_MTPV in present
        # I_ch < Imax for these parameters, so MTPV must be reachable.
        assert sol["i_ch"] == pytest.approx(0.015 / 0.15e-3)  # 100 A
        assert sol["mtpv_reachable"]

    def test_operating_points_respect_both_limits(self):
        sol = solve_mtpa_mtpv(**IPM)
        prm = sol["params"]
        ok = np.isfinite(sol["id"])
        idv, iqv = sol["id"][ok], sol["iq"][ok]
        rpm = sol["rpm"][ok]
        # Current limit.
        assert np.all(idv ** 2 + iqv ** 2 <= prm["Imax"] ** 2 * (1 + 1e-6))
        # Voltage limit at each speed.
        w_e = prm["p"] * rpm * 2 * np.pi / 60.0
        flux = np.hypot(prm["Ld"] * idv + prm["psi"], prm["Lq"] * iqv)
        assert np.all(flux <= prm["Vmax"] / w_e + 1e-9)

    def test_power_never_exceeds_corner_power_significantly(self):
        sol = solve_mtpa_mtpv(**IPM)
        assert np.nanmax(sol["power_kw"]) == pytest.approx(sol["corner_kw"])
        assert sol["corner_kw"] > 0

    def test_base_speed_reported_and_positive(self):
        sol = solve_mtpa_mtpv(**IPM)
        assert sol["base_rpm"] is not None
        assert 0 < sol["base_rpm"] < IPM["rpm_max"]


class TestValidation:
    def test_non_positive_parameter_raises(self):
        bad = dict(IPM)
        bad["psi_pm"] = 0.0
        with pytest.raises(ValueError):
            solve_mtpa_mtpv(**bad)
