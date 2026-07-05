"""Mechanical Design (Motor) analysis.

Implements the first-pass sizing checks from
EV_Motor_Mechanical_Design_Formula_Handbook (Shigley / Roark / Timoshenko /
DIN 7190 / ISO 281 / ISO 21940-11 based) as an interactive analysis mode.
A "Design Check" selector picks one of five checks, each with its own
inputs, numeric results, and a design graph:

  1. Rotor Stress & Burst Speed   - rotating-disc hoop/radial stress profile,
     von Mises, yield-onset burst speed and overspeed margin (handbook §1.1-1.3).
  2. Shaft Design (Static + Fatigue) - torsion + bending von Mises static SF,
     Marin-corrected endurance limit, DE-Goodman fatigue SF, required minimum
     diameter, angle of twist (§2.1-2.5).
  3. Press / Shrink Fit           - interference contact pressure (Shigley/
     DIN 7190 form), hub-bore hoop stress and yield-onset pressure, friction
     torque capacity, press force, thermal + centrifugal interference loss,
     loss-of-contact speed, shrink-fit assembly temperature (§3.1-3.6, §1.5, §8.3).
  4. Bearing Life (L10)           - ISO 281 equivalent dynamic load, L10/L10h,
     reliability-adjusted Lnm, ISO 76 static safety factor (§4.1-4.2).
  5. Critical Speed & Balancing   - single-mass lateral critical speed with
     finite bearing stiffness in series, separation margin, ISO 21940-11
     permissible residual unbalance (§7.1, §7.3, §7.6).

All module-level functions are pure (SI units: m, Pa, kg, rad/s, N·m) and
tested in tests/test_mechanical_design.py; unit conversion (mm, MPa, RPM, µm)
happens only in the mixin's input readers. These are first-pass analytical
checks — the handbook's §11 FEA guidance still applies for production sign-off.
"""

import numpy as np
import customtkinter as ctk

from .theme import COLORS
from .validation import parse_float

ANALYSIS_NAME = "Mechanical Design (Motor)"

MECH_CHECKS = [
    "Rotor Stress & Burst Speed",
    "Shaft Design (Static + Fatigue)",
    "Press / Shrink Fit",
    "Bearing Life (L10)",
    "Critical Speed & Balancing",
]

# ---------------------------------------------------------------------------#
#  1. Rotor stress & burst speed (rotating disc, plane stress)               #
# ---------------------------------------------------------------------------#

def rotor_disc_stresses(r, r_i, r_o, rho, nu, omega):
    """Radial and hoop stress (Pa) at radius r (array or scalar, m) of a
    rotating disc. r_i = 0 -> solid disc; r_i > 0 -> hollow disc (Timoshenko/
    Roark plane-stress closed form)."""
    r = np.asarray(r, dtype=float)
    c = rho * omega ** 2 / 4.0
    if r_i <= 0.0:
        sig_t = c * ((3.0 + nu) * r_o ** 2 - (1.0 + 3.0 * nu) * r ** 2)
        sig_r = c * (3.0 + nu) * (r_o ** 2 - r ** 2)
    else:
        rr = np.maximum(r, r_i)  # guard r=0 for the 1/r² term
        sig_t = c * ((3.0 + nu) * (r_o ** 2 + r_i ** 2 + (r_o * r_i / rr) ** 2)
                     - (1.0 + 3.0 * nu) * rr ** 2)
        sig_r = c * (3.0 + nu) * (r_o ** 2 + r_i ** 2
                                  - (r_o * r_i / rr) ** 2 - rr ** 2)
    return sig_t, sig_r


def rotor_peak_hoop_stress(r_i, r_o, rho, nu, omega):
    """Peak hoop stress (Pa): at the bore for a hollow disc, center for solid."""
    if r_i <= 0.0:
        return (3.0 + nu) * rho * omega ** 2 * r_o ** 2 / 4.0
    return rho * omega ** 2 / 2.0 * ((3.0 + nu) * r_o ** 2
                                     + (1.0 - nu) * r_i ** 2)


def von_mises_plane(sig_1, sig_2):
    """Plane-stress von Mises from two principal stresses (third = 0)."""
    s1 = np.asarray(sig_1, dtype=float)
    s2 = np.asarray(sig_2, dtype=float)
    return np.sqrt(s1 ** 2 + s2 ** 2 - s1 * s2)


def rotor_burst_speed(r_i, r_o, rho, nu, sigma_allow):
    """Angular speed (rad/s) at which peak hoop stress reaches sigma_allow
    (yield-onset burst margin). Closed forms from handbook §1.3."""
    if r_i <= 0.0:
        return np.sqrt(4.0 * sigma_allow / (rho * (3.0 + nu) * r_o ** 2))
    return np.sqrt(2.0 * sigma_allow
                   / (rho * ((3.0 + nu) * r_o ** 2 + (1.0 - nu) * r_i ** 2)))


# ---------------------------------------------------------------------------#
#  2. Shaft design (torsion, combined static, Marin, DE-Goodman)             #
# ---------------------------------------------------------------------------#

def shaft_bend_modulus(d_o, d_i=0.0):
    """Bending section modulus Z = I/c (m³) of a circular (hollow) shaft."""
    return np.pi * (d_o ** 4 - d_i ** 4) / (32.0 * d_o)


def shaft_torsion_stress(torque, d_o, d_i=0.0):
    """Max torsional shear stress (Pa)."""
    return 16.0 * torque * d_o / (np.pi * (d_o ** 4 - d_i ** 4))


def shaft_twist_rad(torque, length, shear_mod, d_o, d_i=0.0):
    """Angle of twist (rad) over `length`."""
    return 32.0 * torque * length / (np.pi * (d_o ** 4 - d_i ** 4) * shear_mod)


def shaft_static_sf(m_total, t_total, d_o, d_i, sy):
    """Static safety factor on yield: distortion-energy combined bending +
    torsion, sigma_vm = sqrt(M² + 0.75 T²)/Z (handbook §2.2)."""
    z = shaft_bend_modulus(d_o, d_i)
    sig_vm = np.sqrt(m_total ** 2 + 0.75 * t_total ** 2) / z
    return sy / sig_vm if np.any(sig_vm > 0) else np.inf


# Marin factor tables (Shigley Ch. 6). Surface: k_a = a * Sut(MPa)^b.
SURFACE_FINISH_FACTORS = {
    "Ground": (1.58, -0.085),
    "Machined / Cold-drawn": (4.51, -0.265),
    "Hot-rolled": (57.7, -0.718),
    "As-forged": (272.0, -0.995),
}
RELIABILITY_KE = {
    "50%": 1.0, "90%": 0.897, "95%": 0.868, "99%": 0.814, "99.9%": 0.753,
}


def marin_endurance_limit(sut, d_o, surface="Machined / Cold-drawn",
                          reliability="90%"):
    """Corrected endurance limit S_e (Pa) for a steel shaft in rotating
    bending: S_e = k_a·k_b·k_e·S_e' with S_e' = 0.5·Sut (≤1400 MPa) else
    700 MPa. k_c = 1 (von Mises combined loading), k_d = k_f = 1."""
    sut_mpa = sut / 1e6
    se_prime_mpa = 0.5 * sut_mpa if sut_mpa <= 1400.0 else 700.0
    a, b = SURFACE_FINISH_FACTORS.get(surface,
                                      SURFACE_FINISH_FACTORS["Machined / Cold-drawn"])
    ka = a * sut_mpa ** b
    d_mm = d_o * 1000.0
    if d_mm < 2.79:
        kb = 1.0
    elif d_mm <= 51.0:
        kb = (d_mm / 7.62) ** -0.107
    else:
        kb = 1.51 * d_mm ** -0.157
    ke = RELIABILITY_KE.get(reliability, 1.0)
    return ka * kb * ke * se_prime_mpa * 1e6


def de_goodman_sf(d_o, d_i, m_a, m_m, t_a, t_m, se, sut, kf, kfs):
    """DE-Goodman fatigue safety factor at a given diameter (handbook §2.5,
    generalised to hollow shafts via the section modulus)."""
    z = shaft_bend_modulus(d_o, d_i)
    sig_a = np.sqrt((kf * m_a) ** 2 + 0.75 * (kfs * t_a) ** 2) / z
    sig_m = np.sqrt((kf * m_m) ** 2 + 0.75 * (kfs * t_m) ** 2) / z
    denom = sig_a / se + sig_m / sut
    return 1.0 / denom if denom > 0 else np.inf


def de_goodman_diameter(m_a, m_m, t_a, t_m, se, sut, kf, kfs, n):
    """Closed-form DE-Goodman minimum diameter (m), solid shaft (Shigley
    Eq. 7-8: 16n/pi with sqrt(4(Kf·M)² + 3(Kfs·T)²) terms — exactly the
    inverse of de_goodman_sf at that diameter)."""
    a = np.sqrt(4.0 * (kf * m_a) ** 2 + 3.0 * (kfs * t_a) ** 2)
    b = np.sqrt(4.0 * (kf * m_m) ** 2 + 3.0 * (kfs * t_m) ** 2)
    if a / se + b / sut <= 0:
        return 0.0
    return ((16.0 * n / np.pi) * (a / se + b / sut)) ** (1.0 / 3.0)


def static_diameter(m_total, t_total, sy, n):
    """Closed-form distortion-energy static minimum diameter (m), solid."""
    val = np.sqrt(m_total ** 2 + 0.75 * t_total ** 2)
    if val <= 0:
        return 0.0
    return (32.0 * n * val / (np.pi * sy)) ** (1.0 / 3.0)


# ---------------------------------------------------------------------------#
#  3. Press / shrink fit                                                     #
# ---------------------------------------------------------------------------#

def pressfit_pressure(delta, d, d_hub_o, d_shaft_i=0.0,
                      e_o=200e9, nu_o=0.29, e_i=200e9, nu_i=0.29):
    """Contact pressure (Pa) from diametral interference `delta` (m) at joint
    diameter d, hub OD d_hub_o, shaft bore d_shaft_i (0 = solid). Shigley /
    DIN 7190 elastic two-cylinder form (handbook §3.2)."""
    if delta <= 0:
        return 0.0
    term_o = ((d_hub_o ** 2 + d ** 2) / (d_hub_o ** 2 - d ** 2) + nu_o) / e_o
    term_i = ((d ** 2 + d_shaft_i ** 2) / (d ** 2 - d_shaft_i ** 2) - nu_i) / e_i
    return delta / (d * (term_o + term_i))


def hub_bore_hoop_stress(p, d, d_hub_o):
    """Lamé peak hoop stress (Pa) at the hub bore under contact pressure p."""
    return p * (d_hub_o ** 2 + d ** 2) / (d_hub_o ** 2 - d ** 2)


def hub_yield_onset_pressure(sy, d, d_hub_o):
    """Contact pressure at which the hub bore starts to yield (Tresca,
    handbook §3.3)."""
    return sy * (d_hub_o ** 2 - d ** 2) / (2.0 * d_hub_o ** 2)


def pressfit_torque_capacity(p, mu, d, length):
    """Friction torque capacity (N·m) of the joint (handbook §3.6)."""
    return mu * p * np.pi * d ** 2 * length / 2.0


def pressfit_axial_force(p, mu, d, length):
    """Friction axial capacity == press-in force estimate (N) (§3.5/§3.6)."""
    return mu * p * np.pi * d * length


def centrifugal_interference_loss(d, d_hub_o, d_shaft_i, rho_o, nu_o, e_o,
                                  rho_i, nu_i, e_i, omega):
    """Diametral interference lost to differential centrifugal growth of the
    hub bore vs the shaft OD at angular speed omega (m). First-order rotating-
    disc growth: u(r) = r·sigma_t(r)/E with sigma_r = 0 at the free interface
    radius (handbook §1.5 approximation)."""
    a = d / 2.0
    b = d_hub_o / 2.0
    c = d_shaft_i / 2.0
    # Hub = hollow disc bore radius a, outer b: sigma_t at bore.
    sig_hub = rho_o * omega ** 2 / 2.0 * ((3.0 + nu_o) * b ** 2
                                          + (1.0 - nu_o) * a ** 2)
    u_hub = a * sig_hub / e_o
    # Shaft = disc of outer radius a (bore c): sigma_t at its outer surface.
    sig_shaft = rho_i * omega ** 2 / 2.0 * ((1.0 - nu_i) * a ** 2
                                            + (3.0 + nu_i) * c ** 2)
    u_shaft = a * sig_shaft / e_i
    return 2.0 * max(u_hub - u_shaft, 0.0)


def thermal_interference_change(d, alpha_o, alpha_i, delta_t):
    """Diametral interference LOST when the joint is delta_t hotter than at
    assembly (negative = gained). Positive when the hub out-expands the shaft
    (handbook §8.3)."""
    return (alpha_o - alpha_i) * d * delta_t


def assembly_delta_t(delta, clearance, d, alpha_o):
    """Hub temperature rise (K) above ambient needed for shrink-fit assembly
    with `clearance` extra diametral clearance (handbook §3.4)."""
    return (delta + clearance) / (alpha_o * d)


def loss_of_contact_speed(delta_eff, d, d_hub_o, d_shaft_i, rho_o, nu_o, e_o,
                          rho_i, nu_i, e_i):
    """Angular speed (rad/s) where the (already thermally corrected) effective
    interference is fully consumed by centrifugal growth. Loss scales with
    omega², so solve directly. Returns inf if the joint never lets go."""
    if delta_eff <= 0:
        return 0.0
    k = centrifugal_interference_loss(d, d_hub_o, d_shaft_i, rho_o, nu_o, e_o,
                                      rho_i, nu_i, e_i, 1.0)
    if k <= 0:
        return np.inf
    return np.sqrt(delta_eff / k)


# ---------------------------------------------------------------------------#
#  4. Bearing life (ISO 281 / ISO 76)                                        #
# ---------------------------------------------------------------------------#

BEARING_EXPONENTS = {"Ball (p = 3)": 3.0, "Roller (p = 10/3)": 10.0 / 3.0}
A1_FACTORS = {"90% (L10)": 1.0, "95% (L5)": 0.62,
              "96% (L4)": 0.53, "99% (L1)": 0.21}


def bearing_equivalent_load(f_r, f_a, x, y, e):
    """ISO 281 equivalent dynamic load P (N): P = X·Fr + Y·Fa when Fa/Fr > e,
    else P = Fr."""
    if f_r > 0 and f_a / f_r <= e:
        return f_r
    return x * f_r + y * f_a


def bearing_static_equivalent(f_r, f_a, x0=0.6, y0=0.5):
    """ISO 76 equivalent static load P0 (N), never less than Fr."""
    return max(x0 * f_r + y0 * f_a, f_r)


def bearing_l10_hours(c, p, n_rpm, exponent=3.0):
    """Basic rating life in operating hours."""
    if p <= 0 or n_rpm <= 0:
        return np.inf
    return (c / p) ** exponent * 1e6 / (60.0 * n_rpm)


# ---------------------------------------------------------------------------#
#  5. Critical speed & balancing                                             #
# ---------------------------------------------------------------------------#

BALANCE_GRADES = {
    "G16 (crankshaft drives)": 16.0,
    "G6.3 (electric motor default)": 6.3,
    "G2.5 (high speed / NVH)": 2.5,
    "G1.0 (precision)": 1.0,
}


def shaft_bending_stiffness(d_o, d_i, length, e_mod):
    """Midspan bending stiffness 48EI/L³ (N/m) of a simply-supported shaft."""
    i = np.pi * (d_o ** 4 - d_i ** 4) / 64.0
    return 48.0 * e_mod * i / length ** 3


def rotor_critical_speed_rpm(mass, d_o, d_i, length, e_mod,
                             k_bearing_each=0.0):
    """First lateral critical speed (RPM) for a single midspan rotor mass.
    Bearings (2, in parallel) act in series with the shaft bending stiffness;
    k_bearing_each <= 0 means rigid supports (handbook §7.1/§7.3)."""
    k_shaft = shaft_bending_stiffness(d_o, d_i, length, e_mod)
    if k_bearing_each > 0:
        k_total = 1.0 / (1.0 / k_shaft + 1.0 / (2.0 * k_bearing_each))
    else:
        k_total = k_shaft
    omega_n = np.sqrt(k_total / mass)
    return omega_n * 60.0 / (2.0 * np.pi)


def permissible_unbalance_gmm(grade, mass_kg, n_rpm):
    """ISO 21940-11 permissible residual unbalance (g·mm) at max service
    speed: U_per = 9549·G·m/n."""
    if n_rpm <= 0:
        return np.inf
    return 9549.0 * grade * mass_kg / n_rpm


# ---------------------------------------------------------------------------#
#  Mixin: input section + plots                                              #
# ---------------------------------------------------------------------------#

class MechanicalDesignMixin:

    # ------------------------------------------------------------------ #
    #  Input section                                                     #
    # ------------------------------------------------------------------ #
    def _build_mech_design_section(self, input_frame):
        self.sections['mech_design'] = self.create_section(
            input_frame, "Mechanical Design (Motor)", "#f1f5f9")
        frame = self.sections['mech_design']

        ctk.CTkLabel(
            frame,
            text=("First-pass analytical sizing checks (Shigley / Roark /\n"
                  "DIN 7190 / ISO 281 / ISO 21940-11). SI-derived units as\n"
                  "labelled. Hand-calc screening, not FEA sign-off."),
            font=("Segoe UI", 10), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(6, 2))

        row = self.create_control_row(frame, "Design Check")
        self.mech_check_combo = ctk.CTkComboBox(
            row, values=MECH_CHECKS, width=230,
            command=self._on_mech_check_change)
        self.mech_check_combo.set(MECH_CHECKS[0])
        self.mech_check_combo.pack(side="right")

        # One transparent subframe per check; only the active one is packed.
        self._mech_subframes = {}
        for name in MECH_CHECKS:
            self._mech_subframes[name] = ctk.CTkFrame(frame,
                                                      fg_color="transparent")
        self._mech_build_rotor_inputs(self._mech_subframes[MECH_CHECKS[0]])
        self._mech_build_shaft_inputs(self._mech_subframes[MECH_CHECKS[1]])
        self._mech_build_pressfit_inputs(self._mech_subframes[MECH_CHECKS[2]])
        self._mech_build_bearing_inputs(self._mech_subframes[MECH_CHECKS[3]])
        self._mech_build_critspeed_inputs(self._mech_subframes[MECH_CHECKS[4]])

        self.mech_results_label = ctk.CTkLabel(
            frame, text="Results appear here after plotting.",
            justify="left", font=("Segoe UI", 12),
            text_color=COLORS['primary'], anchor="w",
        )
        self.mech_results_label.pack(fill="x", padx=16, pady=(4, 8))

        self._mech_subframes[MECH_CHECKS[0]].pack(
            fill="x", before=self.mech_results_label)

    def _mech_sync_subframe(self):
        """Show only the selected check's input subframe. Called on combobox
        change and from plot_mechanical_design, so a session-restored combo
        value (set() fires no command) still shows the matching inputs."""
        active = self.mech_check_combo.get()
        sub = self._mech_subframes.get(active)
        if sub is None:
            return
        frame = self.sections.get('mech_design')
        if frame is not None and getattr(frame, "_vmi_collapsed", False):
            # Section collapsed: nothing is packed, so edit the collapsed
            # section's saved re-pack list (toggle_section restores it on
            # expand) so expanding shows the newly selected check's inputs.
            subframes = set(self._mech_subframes.values())
            saved = [(c, info) for c, info in getattr(frame, "_vmi_saved", [])
                     if c not in subframes]
            idx = next((i for i, (c, _) in enumerate(saved)
                        if c is self.mech_results_label), len(saved))
            saved.insert(idx, (sub, {"fill": "x"}))
            frame._vmi_saved = saved
            return
        for s in self._mech_subframes.values():
            s.pack_forget()
        if self.mech_results_label.winfo_manager() == "pack":
            sub.pack(fill="x", before=self.mech_results_label)
        else:
            sub.pack(fill="x")
        try:
            self._refresh_scrollregion()
        except Exception:
            pass

    def _on_mech_check_change(self, _choice=None):
        self._mech_sync_subframe()
        if getattr(self, "plot_mode", None) == ANALYSIS_NAME:
            self.plot_graph()

    # --- per-check input builders (defaults ~ a 2W traction motor) --- #
    def _mech_build_rotor_inputs(self, f):
        self.create_labeled_entry(f, "Rotor OD (mm)", "100", "mech_rotor_od")
        self.create_labeled_entry(f, "Rotor Bore Dia (mm, 0 = solid)", "30", "mech_rotor_bore")
        self.create_labeled_entry(f, "Density ρ (kg/m³)", "7650", "mech_rotor_rho")
        self.create_labeled_entry(f, "Poisson's Ratio ν", "0.29", "mech_rotor_nu")
        self.create_labeled_entry(f, "Max Operating Speed (RPM)", "12000", "mech_rotor_rpm")
        self.create_labeled_entry(f, "Overspeed Factor (design check)", "1.2", "mech_rotor_os_factor")
        self.create_labeled_entry(f, "Yield Strength (MPa, at hot temp)", "350", "mech_rotor_sy")

    def _mech_build_shaft_inputs(self, f):
        self.create_labeled_entry(f, "Shaft OD d (mm)", "25", "mech_shaft_d")
        self.create_labeled_entry(f, "Shaft Bore (mm, 0 = solid)", "0", "mech_shaft_di")
        self.create_labeled_entry(f, "Bending Moment M (N·m, alternating)", "15", "mech_shaft_m")
        self.create_labeled_entry(f, "Mean Torque Tm (N·m)", "120", "mech_shaft_tm")
        self.create_labeled_entry(f, "Alternating Torque Ta (N·m)", "0", "mech_shaft_ta")
        self.create_labeled_entry(f, "Sut - Ultimate Strength (MPa)", "800", "mech_shaft_sut")
        self.create_labeled_entry(f, "Sy - Yield Strength (MPa)", "620", "mech_shaft_sy")
        self.create_labeled_entry(f, "Kf (bending, e.g. keyway ≈ 2.0)", "2.0", "mech_shaft_kf")
        self.create_labeled_entry(f, "Kfs (torsion, e.g. keyway ≈ 1.7)", "1.7", "mech_shaft_kfs")
        row = self.create_control_row(f, "Surface Finish")
        self.mech_shaft_surface = ctk.CTkComboBox(
            row, values=list(SURFACE_FINISH_FACTORS.keys()), width=190,
            command=lambda _c: self.plot_graph())
        self.mech_shaft_surface.set("Machined / Cold-drawn")
        self.mech_shaft_surface.pack(side="right")
        row = self.create_control_row(f, "Reliability")
        self.mech_shaft_reliability = ctk.CTkComboBox(
            row, values=list(RELIABILITY_KE.keys()), width=190,
            command=lambda _c: self.plot_graph())
        self.mech_shaft_reliability.set("99%")
        self.mech_shaft_reliability.pack(side="right")
        self.create_labeled_entry(f, "Target Safety Factor n", "1.5", "mech_shaft_n")
        self.create_labeled_entry(f, "Length Between Bearings (mm)", "120", "mech_shaft_len")
        self.create_labeled_entry(f, "Shear Modulus G (GPa)", "79.3", "mech_shaft_g")

    def _mech_build_pressfit_inputs(self, f):
        self.create_labeled_entry(f, "Joint Diameter d (mm)", "30", "mech_pf_d")
        self.create_labeled_entry(f, "Hub (Rotor Core) OD (mm)", "100", "mech_pf_hub_od")
        self.create_labeled_entry(f, "Shaft Bore (mm, 0 = solid)", "0", "mech_pf_shaft_bore")
        self.create_labeled_entry(f, "Diametral Interference δ (µm)", "35", "mech_pf_delta")
        self.create_labeled_entry(f, "Engagement Length L (mm)", "60", "mech_pf_len")
        self.create_labeled_entry(f, "Friction Coefficient µ", "0.12", "mech_pf_mu")
        self.create_labeled_entry(f, "Hub E (GPa)", "200", "mech_pf_e_hub")
        self.create_labeled_entry(f, "Hub ν", "0.29", "mech_pf_nu_hub")
        self.create_labeled_entry(f, "Hub Density (kg/m³)", "7650", "mech_pf_rho_hub")
        self.create_labeled_entry(f, "Hub CTE α (µm/m·K)", "12", "mech_pf_alpha_hub")
        self.create_labeled_entry(f, "Shaft E (GPa)", "200", "mech_pf_e_shaft")
        self.create_labeled_entry(f, "Shaft ν", "0.29", "mech_pf_nu_shaft")
        self.create_labeled_entry(f, "Shaft Density (kg/m³)", "7850", "mech_pf_rho_shaft")
        self.create_labeled_entry(f, "Shaft CTE α (µm/m·K)", "11.7", "mech_pf_alpha_shaft")
        self.create_labeled_entry(f, "Max Speed (RPM)", "12000", "mech_pf_rpm")
        self.create_labeled_entry(f, "Operating ΔT Above Assembly (K)", "80", "mech_pf_dt")
        self.create_labeled_entry(f, "Required Torque (N·m)", "120", "mech_pf_torque")
        self.create_labeled_entry(f, "Torque Safety Factor Target", "2", "mech_pf_sf")
        self.create_labeled_entry(f, "Hub Yield Strength (MPa)", "350", "mech_pf_sy")
        self.create_labeled_entry(f, "Assembly Clearance (µm)", "50", "mech_pf_clearance")

    def _mech_build_bearing_inputs(self, f):
        row = self.create_control_row(f, "Bearing Type")
        self.mech_brg_type = ctk.CTkComboBox(
            row, values=list(BEARING_EXPONENTS.keys()), width=190,
            command=lambda _c: self.plot_graph())
        self.mech_brg_type.set("Ball (p = 3)")
        self.mech_brg_type.pack(side="right")
        self.create_labeled_entry(f, "Dynamic Rating C (N)", "13500", "mech_brg_c")
        self.create_labeled_entry(f, "Static Rating C0 (N)", "6550", "mech_brg_c0")
        self.create_labeled_entry(f, "Radial Load Fr (N)", "900", "mech_brg_fr")
        self.create_labeled_entry(f, "Axial Load Fa (N)", "200", "mech_brg_fa")
        self.create_labeled_entry(f, "Dynamic Factor X", "0.56", "mech_brg_x")
        self.create_labeled_entry(f, "Dynamic Factor Y", "1.6", "mech_brg_y")
        self.create_labeled_entry(f, "Threshold e (Fa/Fr)", "0.3", "mech_brg_e")
        self.create_labeled_entry(f, "Static Factor X0", "0.6", "mech_brg_x0")
        self.create_labeled_entry(f, "Static Factor Y0", "0.5", "mech_brg_y0")
        self.create_labeled_entry(f, "Speed (RPM)", "6000", "mech_brg_rpm")
        row = self.create_control_row(f, "Reliability")
        self.mech_brg_reliability = ctk.CTkComboBox(
            row, values=list(A1_FACTORS.keys()), width=190,
            command=lambda _c: self.plot_graph())
        self.mech_brg_reliability.set("90% (L10)")
        self.mech_brg_reliability.pack(side="right")

    def _mech_build_critspeed_inputs(self, f):
        self.create_labeled_entry(f, "Rotor Mass (kg)", "6", "mech_cs_mass")
        self.create_labeled_entry(f, "Shaft OD d (mm)", "25", "mech_cs_d")
        self.create_labeled_entry(f, "Shaft Bore (mm, 0 = solid)", "0", "mech_cs_di")
        self.create_labeled_entry(f, "Bearing Span L (mm)", "120", "mech_cs_len")
        self.create_labeled_entry(f, "Young's Modulus E (GPa)", "200", "mech_cs_e")
        self.create_labeled_entry(f, "Bearing Stiffness Each (N/µm, 0 = rigid)", "120", "mech_cs_kb")
        self.create_labeled_entry(f, "Max Operating Speed (RPM)", "12000", "mech_cs_rpm")
        row = self.create_control_row(f, "Balance Grade")
        self.mech_cs_grade = ctk.CTkComboBox(
            row, values=list(BALANCE_GRADES.keys()), width=230,
            command=lambda _c: self.plot_graph())
        self.mech_cs_grade.set("G6.3 (electric motor default)")
        self.mech_cs_grade.pack(side="right")

    def _mech_report_input_rows(self):
        """(label, value) pairs for the active check's inputs, read straight
        from the widgets, for the HTML report's analysis-inputs table."""
        active = (self.mech_check_combo.get()
                  if hasattr(self, "mech_check_combo") else MECH_CHECKS[0])
        rows = [("Design Check", active)]
        sub = getattr(self, "_mech_subframes", {}).get(active)
        if sub is None:
            return rows
        for row in sub.winfo_children():
            label = value = None
            try:
                for c in row.winfo_children():
                    if isinstance(c, ctk.CTkLabel) and label is None:
                        label = c.cget("text")
                    elif isinstance(c, (ctk.CTkEntry, ctk.CTkComboBox)):
                        value = c.get()
            except Exception:
                continue
            if label and value is not None:
                rows.append((label, value))
        return rows

    # ------------------------------------------------------------------ #
    #  Plot dispatcher                                                    #
    # ------------------------------------------------------------------ #
    def plot_mechanical_design(self):
        """Route to the active design check. Called from dispatch.plot_graph
        (which has already cleared the figure to a single fresh self.ax)."""
        check = (self.mech_check_combo.get()
                 if hasattr(self, "mech_check_combo") else MECH_CHECKS[0])
        self._mech_sync_subframe()
        plotters = {
            MECH_CHECKS[0]: self._mech_plot_rotor,
            MECH_CHECKS[1]: self._mech_plot_shaft,
            MECH_CHECKS[2]: self._mech_plot_pressfit,
            MECH_CHECKS[3]: self._mech_plot_bearing,
            MECH_CHECKS[4]: self._mech_plot_critspeed,
        }
        ok = plotters.get(check, self._mech_plot_rotor)()
        if ok:
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
            try:
                self.figure.tight_layout()
            except Exception:
                pass
            self.set_status(f"Mechanical design: {check} done.", "ok")
        self.canvas.draw()

    def _mech_fail(self, errors):
        self.set_status("Fix inputs: " + "; ".join(errors), "error")
        self.show_placeholder_message("Invalid inputs — fields marked in red")
        return False

    def _mech_results(self, lines):
        try:
            self.mech_results_label.configure(text="\n".join(lines))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  1. Rotor stress & burst speed                                     #
    # ------------------------------------------------------------------ #
    def _mech_plot_rotor(self):
        errors = []
        od = parse_float(self.mech_rotor_od, "Rotor OD", minimum=1e-3, errors=errors)
        bore = parse_float(self.mech_rotor_bore, "Rotor Bore", minimum=0, errors=errors)
        rho = parse_float(self.mech_rotor_rho, "Density", minimum=1, errors=errors)
        nu = parse_float(self.mech_rotor_nu, "Poisson's Ratio", minimum=0, maximum=0.5, errors=errors)
        rpm = parse_float(self.mech_rotor_rpm, "Max Speed", minimum=1, errors=errors)
        osf = parse_float(self.mech_rotor_os_factor, "Overspeed Factor", minimum=1, errors=errors)
        sy = parse_float(self.mech_rotor_sy, "Yield Strength", minimum=1e-3, errors=errors)
        if not errors and bore >= od:
            errors.append("Rotor bore must be smaller than the OD.")
        if errors:
            return self._mech_fail(errors)

        r_o = od / 2000.0
        r_i = bore / 2000.0
        sy_pa = sy * 1e6
        omega_max = rpm * 2.0 * np.pi / 60.0
        omega_os = omega_max * osf

        r = np.linspace(r_i, r_o, 400)
        sig_t, sig_r = rotor_disc_stresses(r, r_i, r_o, rho, nu, omega_os)
        sig_vm = von_mises_plane(sig_t, sig_r)

        lw = self.gs_float("line_width", 2.0)
        self.ax.plot(r * 1000.0, sig_t / 1e6, color="#dc2626", linewidth=lw,
                     label="Hoop stress σ_t")
        self.ax.plot(r * 1000.0, sig_r / 1e6, color="#2563eb", linewidth=lw,
                     label="Radial stress σ_r")
        self.ax.plot(r * 1000.0, sig_vm / 1e6, color="black", linewidth=lw,
                     linestyle="--", label="von Mises σ_vm")
        self.ax.axhline(sy, color="purple", linestyle="-.", linewidth=1.4,
                        label=f"Yield {sy:.0f} MPa")
        self.ax.axhline(0.65 * sy, color="green", linestyle=":", linewidth=1.4,
                        label=f"Allowable 0.65·Sy = {0.65 * sy:.0f} MPa")
        self.ax.set_xlabel("Radius (mm)")
        self.ax.set_ylabel("Stress (MPa)")
        self.ax.set_title(f"Rotor Disc Stresses at {osf:.2f}× Overspeed "
                          f"({rpm * osf:.0f} RPM)", fontsize=14, weight="bold")
        self.ax.legend(fontsize=9)

        peak = rotor_peak_hoop_stress(r_i, r_o, rho, nu, omega_os)
        vm_peak = float(np.max(sig_vm))
        w_burst = rotor_burst_speed(r_i, r_o, rho, nu, sy_pa)
        rpm_burst = w_burst * 60.0 / (2.0 * np.pi)
        margin = rpm_burst / rpm if rpm > 0 else np.inf
        where = "bore" if r_i > 0 else "center"
        self._mech_results([
            f"Peak hoop stress at {where} @ {osf:.2f}× overspeed: {peak / 1e6:.1f} MPa",
            f"Peak von Mises: {vm_peak / 1e6:.1f} MPa "
            f"(SF vs yield = {sy_pa / vm_peak:.2f})" if vm_peak > 0 else "Peak von Mises: 0",
            f"Yield-onset burst speed: {rpm_burst:.0f} RPM "
            f"({margin:.2f}× max operating speed)",
            "Guidance: burst margin ≥ 1.5×, lamination stress ≤ 60-70% Sy (§1.3).",
        ])
        return True

    # ------------------------------------------------------------------ #
    #  2. Shaft design                                                   #
    # ------------------------------------------------------------------ #
    def _mech_plot_shaft(self):
        errors = []
        d = parse_float(self.mech_shaft_d, "Shaft OD", minimum=1e-3, errors=errors)
        di = parse_float(self.mech_shaft_di, "Shaft Bore", minimum=0, errors=errors)
        m_a = parse_float(self.mech_shaft_m, "Bending Moment", minimum=0, errors=errors)
        t_m = parse_float(self.mech_shaft_tm, "Mean Torque", minimum=0, errors=errors)
        t_a = parse_float(self.mech_shaft_ta, "Alternating Torque", minimum=0, errors=errors)
        sut = parse_float(self.mech_shaft_sut, "Sut", minimum=1, errors=errors)
        sy = parse_float(self.mech_shaft_sy, "Sy", minimum=1, errors=errors)
        kf = parse_float(self.mech_shaft_kf, "Kf", minimum=1, errors=errors)
        kfs = parse_float(self.mech_shaft_kfs, "Kfs", minimum=1, errors=errors)
        n_target = parse_float(self.mech_shaft_n, "Target SF", minimum=0.1, errors=errors)
        length = parse_float(self.mech_shaft_len, "Length", minimum=1e-3, errors=errors)
        g_mod = parse_float(self.mech_shaft_g, "Shear Modulus", minimum=1, errors=errors)
        if not errors and di >= d:
            errors.append("Shaft bore must be smaller than the OD.")
        if errors:
            return self._mech_fail(errors)

        d_m, di_m = d / 1000.0, di / 1000.0
        sut_pa, sy_pa = sut * 1e6, sy * 1e6
        surface = self.mech_shaft_surface.get()
        reliability = self.mech_shaft_reliability.get()
        m_m = 0.0  # rotating-shaft bending is fully alternating

        # SF vs diameter sweep (Marin kb re-evaluated at every diameter).
        d_sweep = np.linspace(max(d_m * 0.4, 2e-3), d_m * 1.6, 250)
        n_stat = np.array([shaft_static_sf(m_a + m_m, t_m + t_a, dd, min(di_m, dd * 0.9), sy_pa)
                           for dd in d_sweep])
        n_fat = np.array([de_goodman_sf(dd, min(di_m, dd * 0.9), m_a, m_m, t_a, t_m,
                                        marin_endurance_limit(sut_pa, dd, surface, reliability),
                                        sut_pa, kf, kfs)
                          for dd in d_sweep])

        lw = self.gs_float("line_width", 2.0)
        self.ax.plot(d_sweep * 1000.0, n_stat, color="#2563eb", linewidth=lw,
                     label="Static SF (yield, DE)")
        self.ax.plot(d_sweep * 1000.0, n_fat, color="#dc2626", linewidth=lw,
                     label="Fatigue SF (DE-Goodman)")
        self.ax.axhline(n_target, color="green", linestyle=":", linewidth=1.4,
                        label=f"Target n = {n_target:g}")
        self.ax.axvline(d, color="black", linestyle="-.", linewidth=1.2,
                        label=f"Chosen d = {d:g} mm")
        self.ax.set_xlabel("Shaft diameter (mm)")
        self.ax.set_ylabel("Safety factor")
        self.ax.set_ylim(0, min(float(np.nanmax(n_fat)) * 1.2, 12.0))
        self.ax.set_title("Shaft Safety Factor vs Diameter",
                          fontsize=14, weight="bold")
        self.ax.legend(fontsize=9)

        se = marin_endurance_limit(sut_pa, d_m, surface, reliability)
        n_static_d = shaft_static_sf(m_a + m_m, t_m + t_a, d_m, di_m, sy_pa)
        n_fatigue_d = de_goodman_sf(d_m, di_m, m_a, m_m, t_a, t_m, se, sut_pa, kf, kfs)
        # Required solid-shaft diameters, iterating the d-dependent kb.
        d_req_fat = d_m
        for _ in range(6):
            se_i = marin_endurance_limit(sut_pa, d_req_fat, surface, reliability)
            d_req_fat = de_goodman_diameter(m_a, m_m, t_a, t_m, se_i, sut_pa, kf, kfs, n_target)
        d_req_stat = static_diameter(m_a + m_m, t_m + t_a, sy_pa, n_target)
        tau = shaft_torsion_stress(t_m + t_a, d_m, di_m)
        twist = shaft_twist_rad(t_m + t_a, length / 1000.0, g_mod * 1e9, d_m, di_m)
        self._mech_results([
            f"Endurance limit S_e (Marin, {surface}, {reliability}): {se / 1e6:.0f} MPa",
            f"At d = {d:g} mm:  static SF = {n_static_d:.2f},  "
            f"DE-Goodman SF = {n_fatigue_d:.2f}",
            f"Required min d (solid) @ n = {n_target:g}:  "
            f"static {d_req_stat * 1000.0:.1f} mm,  fatigue {d_req_fat * 1000.0:.1f} mm "
            f"-> governing {max(d_req_stat, d_req_fat) * 1000.0:.1f} mm",
            f"Peak torsional stress: {tau / 1e6:.1f} MPa;  "
            f"twist over {length:g} mm: {np.degrees(twist):.3f}°",
            "Guidance: fatigue n ≥ 1.5-2.5 governs EV traction shafts (§2.4/2.5).",
        ])
        return True

    # ------------------------------------------------------------------ #
    #  3. Press / shrink fit                                             #
    # ------------------------------------------------------------------ #
    def _mech_plot_pressfit(self):
        errors = []
        d = parse_float(self.mech_pf_d, "Joint Diameter", minimum=1e-3, errors=errors)
        hub_od = parse_float(self.mech_pf_hub_od, "Hub OD", minimum=1e-3, errors=errors)
        bore = parse_float(self.mech_pf_shaft_bore, "Shaft Bore", minimum=0, errors=errors)
        delta_um = parse_float(self.mech_pf_delta, "Interference", minimum=0.01, errors=errors)
        length = parse_float(self.mech_pf_len, "Engagement Length", minimum=1e-3, errors=errors)
        mu = parse_float(self.mech_pf_mu, "Friction Coefficient", minimum=0.01, errors=errors)
        e_hub = parse_float(self.mech_pf_e_hub, "Hub E", minimum=1, errors=errors)
        nu_hub = parse_float(self.mech_pf_nu_hub, "Hub ν", minimum=0, maximum=0.5, errors=errors)
        rho_hub = parse_float(self.mech_pf_rho_hub, "Hub Density", minimum=1, errors=errors)
        a_hub = parse_float(self.mech_pf_alpha_hub, "Hub CTE", minimum=0.1, errors=errors)
        e_sh = parse_float(self.mech_pf_e_shaft, "Shaft E", minimum=1, errors=errors)
        nu_sh = parse_float(self.mech_pf_nu_shaft, "Shaft ν", minimum=0, maximum=0.5, errors=errors)
        rho_sh = parse_float(self.mech_pf_rho_shaft, "Shaft Density", minimum=1, errors=errors)
        a_sh = parse_float(self.mech_pf_alpha_shaft, "Shaft CTE", minimum=0.1, errors=errors)
        rpm = parse_float(self.mech_pf_rpm, "Max Speed", minimum=1, errors=errors)
        dt = parse_float(self.mech_pf_dt, "Operating ΔT", minimum=0, errors=errors)
        t_req = parse_float(self.mech_pf_torque, "Required Torque", minimum=1e-6, errors=errors)
        sf_req = parse_float(self.mech_pf_sf, "Torque Safety Factor", minimum=0.1, errors=errors)
        sy_hub = parse_float(self.mech_pf_sy, "Hub Yield", minimum=1, errors=errors)
        clr_um = parse_float(self.mech_pf_clearance, "Assembly Clearance", minimum=0, errors=errors)
        if not errors and d >= hub_od:
            errors.append("Joint diameter must be smaller than the hub OD.")
        if not errors and bore >= d:
            errors.append("Shaft bore must be smaller than the joint diameter.")
        if errors:
            return self._mech_fail(errors)

        d_m, hub_m, bore_m, len_m = (d / 1000.0, hub_od / 1000.0,
                                     bore / 1000.0, length / 1000.0)
        delta = delta_um * 1e-6
        e_o, e_i = e_hub * 1e9, e_sh * 1e9
        alpha_o, alpha_i = a_hub * 1e-6, a_sh * 1e-6

        def pressure(omega, delta_t):
            d_eff = (delta
                     - thermal_interference_change(d_m, alpha_o, alpha_i, delta_t)
                     - centrifugal_interference_loss(d_m, hub_m, bore_m,
                                                     rho_hub, nu_hub, e_o,
                                                     rho_sh, nu_sh, e_i, omega))
            return pressfit_pressure(max(d_eff, 0.0), d_m, hub_m, bore_m,
                                     e_o, nu_hub, e_i, nu_sh)

        rpm_sweep = np.linspace(0.0, rpm * 1.5, 300)
        w_sweep = rpm_sweep * 2.0 * np.pi / 60.0
        p_cold = np.array([pressure(w, 0.0) for w in w_sweep])
        p_hot = np.array([pressure(w, dt) for w in w_sweep])
        p_min = 2.0 * t_req * sf_req / (mu * np.pi * d_m ** 2 * len_m)

        lw = self.gs_float("line_width", 2.0)
        self.ax.plot(rpm_sweep, p_cold / 1e6, color="#2563eb", linewidth=lw,
                     label="Contact pressure (cold)")
        self.ax.plot(rpm_sweep, p_hot / 1e6, color="#dc2626", linewidth=lw,
                     label=f"Contact pressure (hot, ΔT = {dt:g} K)")
        self.ax.axhline(p_min / 1e6, color="green", linestyle=":", linewidth=1.4,
                        label=f"Required for {t_req:g} N·m × SF {sf_req:g}")
        self.ax.axvline(rpm, color="black", linestyle="-.", linewidth=1.2,
                        label=f"Max speed {rpm:.0f} RPM")
        self.ax.set_xlabel("Speed (RPM)")
        self.ax.set_ylabel("Contact pressure (MPa)")
        self.ax.set_title("Interference-Fit Contact Pressure vs Speed",
                          fontsize=14, weight="bold")
        self.ax.legend(fontsize=9)

        p0 = pressure(0.0, 0.0)
        w_max = rpm * 2.0 * np.pi / 60.0
        p_worst = pressure(w_max, dt)
        t_cap_worst = pressfit_torque_capacity(p_worst, mu, d_m, len_m)
        hoop = hub_bore_hoop_stress(p0, d_m, hub_m)
        p_yield = hub_yield_onset_pressure(sy_hub * 1e6, d_m, hub_m)
        d_eff_hot = (delta - thermal_interference_change(d_m, alpha_o, alpha_i, dt))
        w_loc = loss_of_contact_speed(d_eff_hot, d_m, hub_m, bore_m,
                                      rho_hub, nu_hub, e_o, rho_sh, nu_sh, e_i)
        rpm_loc = w_loc * 60.0 / (2.0 * np.pi)
        if np.isfinite(rpm_loc) and rpm_loc <= rpm_sweep[-1]:
            self.ax.annotate(f"loss of contact\n{rpm_loc:.0f} RPM",
                             xy=(rpm_loc, 0.0), fontsize=9, ha="center",
                             va="bottom", color="#dc2626")
        dT_asm = assembly_delta_t(delta, clr_um * 1e-6, d_m, alpha_o)
        self._mech_results([
            f"Contact pressure p₀ (cold, rest): {p0 / 1e6:.1f} MPa;  "
            f"worst case (hot, max speed): {p_worst / 1e6:.1f} MPa",
            f"Torque capacity worst-case: {t_cap_worst:.1f} N·m  "
            f"(SF vs required = {t_cap_worst / t_req:.2f}, target ≥ {sf_req:g})",
            f"Hub bore hoop stress: {hoop / 1e6:.1f} MPa;  "
            f"yield-onset pressure: {p_yield / 1e6:.1f} MPa "
            f"(SF = {p_yield / p0:.2f})" if p0 > 0 else "No residual pressure at rest.",
            f"Loss-of-contact speed (hot): "
            + (f"{rpm_loc:.0f} RPM ({rpm_loc / rpm:.2f}× max)" if np.isfinite(rpm_loc)
               else "not reached"),
            f"Shrink-fit assembly: heat hub ΔT ≈ {dT_asm:.0f} K above ambient "
            f"(check magnet/bearing temperature limits, §3.4)",
            f"Press-in force (cold): {pressfit_axial_force(p0, mu, d_m, len_m) / 1000.0:.1f} kN",
            "Guidance: capacity SF ≥ 1.5-2.0 at worst case; loss-of-contact ≥ 1.3-1.5× (§3.6/§1.5).",
        ])
        return True

    # ------------------------------------------------------------------ #
    #  4. Bearing life                                                   #
    # ------------------------------------------------------------------ #
    def _mech_plot_bearing(self):
        errors = []
        c = parse_float(self.mech_brg_c, "Dynamic Rating C", minimum=1, errors=errors)
        c0 = parse_float(self.mech_brg_c0, "Static Rating C0", minimum=1, errors=errors)
        fr = parse_float(self.mech_brg_fr, "Radial Load", minimum=0, errors=errors)
        fa = parse_float(self.mech_brg_fa, "Axial Load", minimum=0, errors=errors)
        x = parse_float(self.mech_brg_x, "Factor X", minimum=0, errors=errors)
        y = parse_float(self.mech_brg_y, "Factor Y", minimum=0, errors=errors)
        e = parse_float(self.mech_brg_e, "Threshold e", minimum=1e-3, errors=errors)
        x0 = parse_float(self.mech_brg_x0, "Factor X0", minimum=0, errors=errors)
        y0 = parse_float(self.mech_brg_y0, "Factor Y0", minimum=0, errors=errors)
        rpm = parse_float(self.mech_brg_rpm, "Speed", minimum=1, errors=errors)
        if not errors and fr <= 0 and fa <= 0:
            errors.append("At least one of Fr / Fa must be positive.")
        if errors:
            return self._mech_fail(errors)

        exponent = BEARING_EXPONENTS.get(self.mech_brg_type.get(), 3.0)
        rel = self.mech_brg_reliability.get()
        a1 = A1_FACTORS.get(rel, 1.0)

        p_op = bearing_equivalent_load(fr, fa, x, y, e)
        p0 = bearing_static_equivalent(fr, fa, x0, y0)
        l10h = bearing_l10_hours(c, p_op, rpm, exponent)
        lnmh = a1 * l10h
        s0 = c0 / p0 if p0 > 0 else np.inf

        p_sweep = np.linspace(max(c / 50.0, p_op * 0.2), c / 2.0, 300)
        lw = self.gs_float("line_width", 2.0)
        self.ax.plot(p_sweep, [bearing_l10_hours(c, p, rpm, exponent) for p in p_sweep],
                     color="#2563eb", linewidth=lw, label="L10h (90% reliability)")
        if a1 != 1.0:
            self.ax.plot(p_sweep,
                         [a1 * bearing_l10_hours(c, p, rpm, exponent) for p in p_sweep],
                         color="#dc2626", linewidth=lw, linestyle="--",
                         label=f"Lnm ({rel}, a1 = {a1:g})")
        self.ax.plot([p_op], [lnmh], "o", color="black", markersize=8,
                     label=f"Operating point P = {p_op:.0f} N")
        self.ax.set_yscale("log")
        self.ax.set_xlabel("Equivalent dynamic load P (N)")
        self.ax.set_ylabel("Rating life (hours)")
        self.ax.set_title(f"Bearing Rating Life vs Load @ {rpm:.0f} RPM",
                          fontsize=14, weight="bold")
        self.ax.legend(fontsize=9)

        self._mech_results([
            f"Equivalent dynamic load P: {p_op:.0f} N  "
            f"(Fa/Fr = {fa / fr:.2f} {'> e -> X·Fr + Y·Fa' if fr > 0 and fa / fr > e else '≤ e -> P = Fr'})"
            if fr > 0 else f"Equivalent dynamic load P: {p_op:.0f} N",
            f"L10 = {(c / p_op) ** exponent:.1f} million rev;  "
            f"L10h = {l10h:,.0f} h at {rpm:.0f} RPM",
            f"Adjusted life Lnm ({rel}): {lnmh:,.0f} h",
            f"Static: P0 = {p0:.0f} N, S0 = C0/P0 = {s0:.2f} "
            f"(target ≥ 1.5-2, ≥ 2-3 for shock/NVH, §4.1)",
            "Note: lubrication/contamination factor aISO not applied (ISO 281 Annex).",
        ])
        return True

    # ------------------------------------------------------------------ #
    #  5. Critical speed & balancing                                     #
    # ------------------------------------------------------------------ #
    def _mech_plot_critspeed(self):
        errors = []
        mass = parse_float(self.mech_cs_mass, "Rotor Mass", minimum=1e-3, errors=errors)
        d = parse_float(self.mech_cs_d, "Shaft OD", minimum=1e-3, errors=errors)
        di = parse_float(self.mech_cs_di, "Shaft Bore", minimum=0, errors=errors)
        length = parse_float(self.mech_cs_len, "Bearing Span", minimum=1e-3, errors=errors)
        e_mod = parse_float(self.mech_cs_e, "Young's Modulus", minimum=1, errors=errors)
        kb = parse_float(self.mech_cs_kb, "Bearing Stiffness", minimum=0, errors=errors)
        rpm = parse_float(self.mech_cs_rpm, "Max Speed", minimum=1, errors=errors)
        if not errors and di >= d:
            errors.append("Shaft bore must be smaller than the OD.")
        if errors:
            return self._mech_fail(errors)

        d_m, di_m, len_m = d / 1000.0, di / 1000.0, length / 1000.0
        e_pa = e_mod * 1e9
        kb_si = kb * 1e6  # N/µm -> N/m

        n_crit = rotor_critical_speed_rpm(mass, d_m, di_m, len_m, e_pa, kb_si)
        d_sweep = np.linspace(max(d_m * 0.5, 2e-3), d_m * 2.0, 250)
        n_sweep = [rotor_critical_speed_rpm(mass, dd, min(di_m, dd * 0.9),
                                            len_m, e_pa, kb_si) for dd in d_sweep]

        lw = self.gs_float("line_width", 2.0)
        self.ax.plot(d_sweep * 1000.0, n_sweep, color="#2563eb", linewidth=lw,
                     label="1st critical speed")
        self.ax.axhline(rpm, color="#dc2626", linestyle="-.", linewidth=1.4,
                        label=f"Max operating {rpm:.0f} RPM")
        self.ax.axhline(rpm * 1.2, color="orange", linestyle=":", linewidth=1.4,
                        label="1.2× (separation margin)")
        self.ax.axvline(d, color="black", linestyle="--", linewidth=1.2,
                        label=f"Chosen d = {d:g} mm")
        self.ax.set_xlabel("Shaft diameter (mm)")
        self.ax.set_ylabel("Critical speed (RPM)")
        self.ax.set_title("Critical Speed vs Shaft Diameter "
                          f"(m = {mass:g} kg, L = {length:g} mm)",
                          fontsize=14, weight="bold")
        self.ax.legend(fontsize=9)

        grade_name = self.mech_cs_grade.get()
        grade = BALANCE_GRADES.get(grade_name, 6.3)
        u_per = permissible_unbalance_gmm(grade, mass, rpm)
        k_shaft = shaft_bending_stiffness(d_m, di_m, len_m, e_pa)
        margin = (n_crit - rpm) / rpm * 100.0
        self._mech_results([
            f"Shaft bending stiffness 48EI/L³: {k_shaft / 1e6:.2f} N/µm"
            + ("" if kb <= 0 else f";  bearing pair: {2 * kb:.0f} N/µm"),
            f"1st critical speed: {n_crit:.0f} RPM "
            f"({n_crit / 60.0:.1f} Hz natural frequency)",
            f"Separation margin vs max speed: {margin:+.1f}% "
            f"({'sub-critical, OK' if margin >= 20 else 'below the ≥15-20% guideline — stiffen shaft / shorten span'})",
            f"Balance ({grade_name}): U_per = {u_per:.1f} g·mm total "
            f"≈ {u_per / 2.0:.1f} g·mm per correction plane (ISO 21940-11)",
            "Note: single-mass Rayleigh model — final sign-off needs a Campbell diagram (§7.4).",
        ])
        return True
