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
    dc_link_to_vmax,
    phase_peak_current,
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


class TestCurrentSpec:
    """Line/phase + RMS/peak -> peak phase current (star vs delta)."""

    def test_star_line_equals_phase(self):
        assert phase_peak_current(100, "Star (Y)", "Line", "Peak") == pytest.approx(100.0)

    def test_delta_line_to_phase(self):
        assert phase_peak_current(100, "Delta (Δ)", "Line", "Peak") == \
            pytest.approx(100.0 / np.sqrt(3.0))

    def test_delta_phase_unchanged(self):
        assert phase_peak_current(100, "Delta (Δ)", "Phase", "Peak") == pytest.approx(100.0)

    def test_rms_to_peak(self):
        assert phase_peak_current(100, "Star (Y)", "Phase", "RMS") == \
            pytest.approx(100.0 * np.sqrt(2.0))

    def test_delta_line_rms(self):
        assert phase_peak_current(100, "Delta (Δ)", "Line", "RMS") == \
            pytest.approx(100.0 * np.sqrt(2.0) / np.sqrt(3.0))


class TestVoltageLimit:
    def test_svpwm_default(self):
        assert dc_link_to_vmax(72.0) == pytest.approx(72.0 / np.sqrt(3.0))

    def test_sine_pwm(self):
        assert dc_link_to_vmax(72.0, "Sine PWM (Vdc/2)") == pytest.approx(36.0)

    def test_six_step(self):
        assert dc_link_to_vmax(72.0, "Six-step (2·Vdc/π)") == pytest.approx(2 * 72.0 / np.pi)

    def test_six_step_raises_base_speed(self):
        lo = solve_mtpa_mtpv(**IPM)
        hi = solve_mtpa_mtpv(**{**IPM, "v_max": 2 * 72.0 / np.pi})
        # Same MTPA point, more voltage headroom -> proportionally higher base.
        assert hi["base_rpm"] / lo["base_rpm"] == \
            pytest.approx((2 / np.pi) / (1 / np.sqrt(3.0)), rel=1e-6)
        assert hi["t_mtpa_max"] == pytest.approx(lo["t_mtpa_max"])


class TestBaseSpeedAnalytic:
    def test_spmsm_base_speed_closed_form(self):
        """SPMSM: MTPA is id=0, so base w_e = Vmax / hypot(psi, Lq*Imax)."""
        p, lq, psi, imax, vmax = 4, 0.2e-3, 0.02, 100.0, 40.0
        sol = solve_mtpa_mtpv(pole_pairs=p, ld_h=0.2e-3, lq_h=lq, psi_pm=psi,
                              i_max=imax, v_max=vmax, rpm_max=6000.0)
        w_e = vmax / np.hypot(psi, lq * imax)
        expected = w_e / p * 60.0 / (2 * np.pi)
        assert sol["base_rpm"] == pytest.approx(expected, rel=2e-3)

    def test_base_speed_matches_region_boundary(self):
        """Analytic base speed sits at the end of the MTPA-classified region."""
        sol = solve_mtpa_mtpv(**IPM)
        m = sol["region"] == REGION_MTPA
        last_mtpa = sol["rpm"][m][-1]
        step = sol["rpm"][1] - sol["rpm"][0]
        assert last_mtpa <= sol["base_rpm"] + 1e-9
        assert sol["base_rpm"] - last_mtpa <= step * 1.5


class TestSaturationMaps:
    @staticmethod
    def _const_map(value, i_max=200.0, n=5):
        ax = np.linspace(-i_max, i_max, n)
        return {"id": ax, "iq": ax, "m": np.full((n, n), value)}

    def test_constant_maps_match_constant_solver(self):
        """Uniform maps must reproduce the analytic constant-parameter path
        (grid search vs boundary sampling -> small tolerance)."""
        ref = solve_mtpa_mtpv(**IPM)
        mapped = solve_mtpa_mtpv(
            **IPM,
            ld_map=self._const_map(IPM["ld_h"]),
            lq_map=self._const_map(IPM["lq_h"]),
            psi_map=self._const_map(IPM["psi_pm"]),
        )
        assert mapped["mapped"] and not ref["mapped"]
        assert mapped["t_mtpa_max"] == pytest.approx(ref["t_mtpa_max"], rel=1e-3)
        assert mapped["base_rpm"] == pytest.approx(ref["base_rpm"], rel=1e-2)
        assert mapped["corner_kw"] == pytest.approx(ref["corner_kw"], rel=2e-2)
        assert np.allclose(mapped["torque"], ref["torque"],
                           rtol=2e-2, atol=ref["t_mtpa_max"] * 1e-2)
        assert mapped["i_ch"] == pytest.approx(ref["i_ch"], rel=1e-6)

    def test_saturating_lq_reduces_reluctance_torque(self):
        """An Lq that saturates toward Ld kills reluctance torque -> lower peak."""
        ref = solve_mtpa_mtpv(**IPM)
        n = 9
        ax = np.linspace(-200.0, 200.0, n)
        _, IQ = np.meshgrid(ax, ax, indexing="ij")
        lq_sat = IPM["lq_h"] * (1.0 - 0.5 * np.abs(IQ) / 200.0)
        sat = solve_mtpa_mtpv(**IPM, lq_map={"id": ax, "iq": ax, "m": lq_sat})
        assert sat["t_mtpa_max"] < ref["t_mtpa_max"]
        # Still respects the current limit.
        ok = np.isfinite(sat["id"])
        assert np.all(sat["id"][ok] ** 2 + sat["iq"][ok] ** 2
                      <= 200.0 ** 2 * (1 + 1e-6))

    def test_mapped_operating_points_respect_voltage_limit(self):
        sol = solve_mtpa_mtpv(
            **IPM,
            lq_map=self._const_map(IPM["lq_h"]),
        )
        prm = sol["params"]
        ok = np.isfinite(sol["id"])
        idv, iqv = sol["id"][ok], sol["iq"][ok]
        w_e = prm["p"] * sol["rpm"][ok] * 2 * np.pi / 60.0
        flux = np.hypot(prm["Ld"] * idv + prm["psi"], prm["Lq"] * iqv)
        assert np.all(flux <= prm["Vmax"] / w_e + 1e-9)

    def test_abs_id_axis_map_supported(self):
        """A map given over |id| >= 0 is interpreted as magnitude."""
        n = 5
        pos = np.linspace(0.0, 200.0, n)
        ld = {"id": pos, "iq": pos, "m": np.full((n, n), IPM["ld_h"])}
        ref = solve_mtpa_mtpv(**IPM)
        sol = solve_mtpa_mtpv(**IPM, ld_map=ld)
        assert sol["t_mtpa_max"] == pytest.approx(ref["t_mtpa_max"], rel=1e-3)
