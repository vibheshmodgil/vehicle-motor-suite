"""Auto-generated module (method bodies copied verbatim from the original app)."""
import json
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import numpy as np
import pandas as pd
import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from PIL import Image
from scipy.interpolate import RegularGridInterpolator, UnivariateSpline, NearestNDInterpolator
from scipy.ndimage import gaussian_filter

from .theme import COLORS, FONTS
from .physics import calculate_crr_cd_a, df, g
from .applog import logger



class EfficiencyMixin:

    def plot_both_efficiency_maps(self):
        self.plot_efficiency_map_motor1()
        self.plot_efficiency_map_motor2()

    def _normalize_efficiency_map_data(self, torque_raw, rpm_raw, eff_raw):
        torque_raw = np.asarray(torque_raw, dtype=float)
        rpm_raw = np.asarray(rpm_raw, dtype=float)
        eff_raw = np.asarray(eff_raw, dtype=float)

        col_mask = np.isfinite(rpm_raw)
        row_mask = np.isfinite(torque_raw)
        rpm_vals = rpm_raw[col_mask]
        torque_vals = torque_raw[row_mask]
        eff_vals = eff_raw[np.ix_(row_mask, col_mask)]

        if rpm_vals.size < 2 or torque_vals.size < 2:
            raise ValueError("Could not detect enough numeric RPM/Torque headers in the map.")

        rpm_order = np.argsort(rpm_vals)
        torque_order = np.argsort(torque_vals)
        rpm_vals = rpm_vals[rpm_order]
        torque_vals = torque_vals[torque_order]
        eff_vals = eff_vals[np.ix_(torque_order, rpm_order)]

        finite_mask = np.isfinite(eff_vals)
        if not np.any(finite_mask):
            raise ValueError("Efficiency map contains no numeric efficiency values.")
        # Empty/NaN cells are kept as NaN: datasheet maps leave the region the
        # motor can't reach blank, and back-filling them (the old median fill)
        # painted "valid" efficiency above the torque-speed curve. NaN cells
        # render blank in contourf and fall back to the constant-efficiency
        # default when a drive-cycle point lands on one.

        # If values look like %, convert to 0-1.
        if np.nanmax(np.abs(eff_vals)) > 1.5:
            eff_vals = eff_vals / 100.0
        eff_vals = np.clip(eff_vals, 0.01, 1.0)   # clip preserves NaN

        return torque_vals, rpm_vals, eff_vals


    def _read_efficiency_map_excel(self, file_path):
        """
        Read an efficiency map in either of these simple formats:
        1) Easy format: first column = torque index, first row headers = RPM.
        2) Raw matrix: top row has RPM, first column has torque.
        """
        # Preferred simple format (same style as pd.read_excel(..., index_col=0)).
        try:
            df_easy = pd.read_excel(file_path, index_col=0)
            df_easy = df_easy.dropna(how='all').dropna(axis=1, how='all')
            if df_easy.shape[0] >= 2 and df_easy.shape[1] >= 2:
                rpm_raw = pd.to_numeric(pd.Index(df_easy.columns), errors='coerce').to_numpy(dtype=float)
                torque_raw = pd.to_numeric(pd.Index(df_easy.index), errors='coerce').to_numpy(dtype=float)
                eff_raw = df_easy.apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
                return self._normalize_efficiency_map_data(torque_raw, rpm_raw, eff_raw)
        except Exception:
            pass

        # Fallback generic matrix parser.
        df_map = pd.read_excel(file_path, header=None)
        df_map = df_map.dropna(how='all').dropna(axis=1, how='all')
        if df_map.shape[0] < 3 or df_map.shape[1] < 3:
            raise ValueError("Efficiency map needs at least 3x3 data including headers.")

        rpm_raw = pd.to_numeric(df_map.iloc[0, 1:], errors='coerce').to_numpy(dtype=float)
        torque_raw = pd.to_numeric(df_map.iloc[1:, 0], errors='coerce').to_numpy(dtype=float)
        eff_raw = df_map.iloc[1:, 1:].apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
        return self._normalize_efficiency_map_data(torque_raw, rpm_raw, eff_raw)


    def _extract_eff_map_from_dataframe(self, df_raw):
        """
        Parse an efficiency map from an in-memory DataFrame.
        Supported styles:
        1) torque as index, rpm as columns
        2) first column torque, remaining columns rpm
        """
        if df_raw is None:
            raise ValueError("Efficiency data is empty.")

        df = df_raw.copy()
        df = df.dropna(how='all').dropna(axis=1, how='all')
        if df.shape[0] < 2 or df.shape[1] < 2:
            raise ValueError("Efficiency map needs at least 2x2 numeric grid.")

        # Style 1: first column = torque axis (common for pd.read_excel default header=0)
        if df.shape[1] >= 3:
            try:
                rpm_raw = pd.to_numeric(pd.Index(df.columns[1:]), errors='coerce').to_numpy(dtype=float)
                torque_raw = pd.to_numeric(df.iloc[:, 0], errors='coerce').to_numpy(dtype=float)
                eff_raw = df.iloc[:, 1:].apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
                tq_vals, rpm_vals, eff_vals = self._normalize_efficiency_map_data(torque_raw, rpm_raw, eff_raw)
                return tq_vals, rpm_vals, eff_vals
            except Exception:
                pass

        # Style 2: index=torque, columns=rpm
        try:
            rpm_raw = pd.to_numeric(pd.Index(df.columns), errors='coerce').to_numpy(dtype=float)
            torque_raw = pd.to_numeric(pd.Index(df.index), errors='coerce').to_numpy(dtype=float)
            eff_raw = df.apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
            tq_vals, rpm_vals, eff_vals = self._normalize_efficiency_map_data(torque_raw, rpm_raw, eff_raw)
            return tq_vals, rpm_vals, eff_vals
        except Exception:
            pass

        raise ValueError("Could not parse torque/rpm axes from uploaded efficiency map.")


    def _autofill_motor_params_from_map(self, motor, torque_axis, rpm_axis, eff_matrix=None):
        """Autofill motor input parameters from an uploaded map when fields are not manual."""
        tq = np.asarray(torque_axis, dtype=float)
        rpm = np.asarray(rpm_axis, dtype=float)
        tq = tq[np.isfinite(tq)]
        rpm = rpm[np.isfinite(rpm)]
        if tq.size == 0 or rpm.size == 0:
            return

        max_speed_val = float(np.nanmax(rpm))
        max_torque_val = float(np.nanmax(tq))
        # Map axes only tell us the grid extent, not the real base speed or
        # power rating -- so by default autofill an envelope equal to the full
        # map rectangle (rated speed = max speed, power = corner power). The old
        # derivation used the torque AXIS MINIMUM as "torque at max speed",
        # which gave absurdly low rated speed/power and masked off most of the
        # map. Enter the motor's true rated speed / max power manually (the
        # fields stay editable) to get real power-limited masking.
        rated_speed_val = max_speed_val
        max_power_val = (max_torque_val * max_speed_val * 2 * np.pi / 60.0) / 1000.0
        # Datasheet maps often leave the unreachable region (above the
        # torque-speed curve) as blank/NaN cells. When such holes exist, the
        # populated cells trace the real envelope: peak power = the largest
        # |T|*omega among cells that actually have data, and peak torque = the
        # largest |T| with any data. A fully populated map has no holes and
        # keeps the rectangle values above (behavior unchanged).
        if eff_matrix is not None:
            try:
                mat = np.asarray(eff_matrix, dtype=float)
                if mat.shape == (tq.size, rpm.size) and np.isnan(mat).any():
                    valid = np.isfinite(mat)
                    if np.any(valid):
                        T, R = np.meshgrid(np.abs(tq), rpm, indexing="ij")
                        omega = R * 2.0 * np.pi / 60.0
                        max_power_val = float(np.max((T * omega)[valid])) / 1000.0
                        max_torque_val = float(np.max(T[valid]))
            except Exception:
                pass

        if motor == 1:
            if not self.motor1_max_speed_manual:
                self.motor1_max_speed.delete(0, "end")
                self.motor1_max_speed.insert(0, f"{max_speed_val:.2f}")
            if not self.motor1_max_torque_manual:
                self.motor1_max_torque.delete(0, "end")
                self.motor1_max_torque.insert(0, f"{max_torque_val:.2f}")
            if not self.motor1_rated_speed_manual:
                self.motor1_rated_speed.delete(0, "end")
                self.motor1_rated_speed.insert(0, f"{rated_speed_val:.2f}")
            if not self.motor1_max_power_manual:
                self.motor1_max_power.delete(0, "end")
                self.motor1_max_power.insert(0, f"{max_power_val:.2f}")
        else:
            if not self.motor2_max_speed_manual:
                self.motor2_max_speed.delete(0, "end")
                self.motor2_max_speed.insert(0, f"{max_speed_val:.2f}")
            if not self.motor2_max_torque_manual:
                self.motor2_max_torque.delete(0, "end")
                self.motor2_max_torque.insert(0, f"{max_torque_val:.2f}")
            if not self.motor2_rated_speed_manual:
                self.motor2_rated_speed.delete(0, "end")
                self.motor2_rated_speed.insert(0, f"{rated_speed_val:.2f}")
            if not self.motor2_max_power_manual:
                self.motor2_max_power.delete(0, "end")
                self.motor2_max_power.insert(0, f"{max_power_val:.2f}")


    def _get_eff_constant(self, entry_widget, default_value):
        try:
            eff_val = float(entry_widget.get())
        except Exception:
            eff_val = default_value
        if eff_val > 1.5:
            eff_val = eff_val / 100.0
        return float(np.clip(eff_val, 0.01, 1.0))


    def _interpolate_efficiency_or_constant(self, torque_vals, rpm_vals, map_matrix, map_torque_axis, map_rpm_axis, default_eff):
        torque_vals = np.asarray(torque_vals, dtype=float)
        rpm_vals = np.asarray(rpm_vals, dtype=float)

        if map_matrix is None or map_torque_axis is None or map_rpm_axis is None:
            return np.full_like(torque_vals, float(default_eff), dtype=float)

        try:
            tq_abs = np.abs(torque_vals)
            rpm_abs = np.abs(rpm_vals)
            tq_clipped = np.clip(tq_abs, np.min(map_torque_axis), np.max(map_torque_axis))
            rpm_clipped = np.clip(rpm_abs, np.min(map_rpm_axis), np.max(map_rpm_axis))
            interp = RegularGridInterpolator(
                (map_torque_axis, map_rpm_axis),
                map_matrix,
                bounds_error=False,
                fill_value=None
            )
            points = np.column_stack((tq_clipped, rpm_clipped))
            eff_interp = interp(points)
            eff_interp = np.asarray(eff_interp, dtype=float)
            eff_interp[~np.isfinite(eff_interp)] = float(default_eff)
            return np.clip(eff_interp, 0.01, 1.0)
        except Exception:
            return np.full_like(torque_vals, float(default_eff), dtype=float)


    def _motor_capability_mask(self, torque_grid, rpm_grid, motor=1):
        """Boolean mask (same shape as the grids): True where (torque, rpm) is
        an acceptable operating point of the motor's torque-speed curve,
        False elsewhere (those cells are blanked before contouring).

        Acceptance rule (exactly the torque-speed-curve check):
          base RPM = peak power / peak torque, in proper units:
                     omega_base = P_peak[W] / T_peak[Nm]  (rad/s) -> RPM
          * RPM <= base RPM: acceptable iff |T| <= peak torque
          * RPM  > base RPM: compute the point's power P = |T| * omega;
                             acceptable iff P <= peak power
        Peak torque / peak power come from the Motor (map axes) or Controller
        input fields via get_motor_params(). Symmetric in torque so regen is
        treated the same as motoring.

        Returns None (meaning "don't mask") if peak torque / peak power aren't
        filled in or aren't valid yet, so a map still displays in full before
        those fields are populated.
        """
        # Only peak torque and peak power are needed -- read them directly so
        # a blank Max/Rated Speed field can't disable the mask.
        try:
            if motor == 1:
                peak_torque = float(self.motor1_max_torque.get())
                peak_power_kw = float(self.motor1_max_power.get())
            else:
                peak_torque = float(self.motor2_max_torque.get())
                peak_power_kw = float(self.motor2_max_power.get())
        except Exception:
            return None
        if peak_torque <= 0 or peak_power_kw <= 0:
            return None

        rpm_abs = np.abs(np.asarray(rpm_grid, dtype=float))
        tq_abs = np.abs(np.asarray(torque_grid, dtype=float))

        peak_power_w = peak_power_kw * 1000.0
        # Battery DC limit (optional). With NO efficiency maps loaded, the
        # shaft can never see more than the constant Vdc*Idc*eta, so the
        # power-limited region of the acceptance rule uses the smaller of the
        # two (original behaviour, golden-locked; blank battery fields ->
        # cap None -> rule unchanged). With maps loaded, the battery limit is
        # instead evaluated AFTER the motor x controller efficiency at each
        # grid point -- see the extra AND below.
        _cap_fn = getattr(self, "get_battery_power_cap_w", None)
        batt_cap_w = _cap_fn() if callable(_cap_fn) else None
        _pdc_fn = getattr(self, "get_battery_dc_power_w", None)
        _eta_get = getattr(self, "_battery_eta_fn", None)
        p_dc = _pdc_fn() if callable(_pdc_fn) else None
        batt_eta_fn = (_eta_get() if (p_dc is not None and callable(_eta_get))
                       else None)
        if batt_cap_w is not None and batt_eta_fn is None:
            peak_power_w = min(peak_power_w, float(batt_cap_w))
        omega = rpm_abs * 2.0 * np.pi / 60.0                      # rad/s
        base_rpm = (peak_power_w / peak_torque) * 60.0 / (2.0 * np.pi)

        point_power_w = tq_abs * omega
        acceptable = np.where(
            rpm_abs <= base_rpm,
            tq_abs <= peak_torque + 1e-9,          # constant-torque region
            point_power_w <= peak_power_w + 1e-6,  # power-limited region
        )
        if batt_eta_fn is not None:
            # Map-aware battery feasibility: the point must also satisfy
            # |T|*omega <= Vdc*Idc * eta_m(T,w) * eta_c(T,w).
            try:
                eta = np.asarray(batt_eta_fn(tq_abs.ravel(), rpm_abs.ravel()),
                                 dtype=float).reshape(tq_abs.shape)
                acceptable = acceptable & (point_power_w <= float(p_dc) * eta + 1e-6)
            except Exception:
                pass
        return acceptable

    def _draw_motor_capability_curve(self, ax=None, motor=1,
                                     label="Motor torque-speed curve"):
        """Draw the motor's torque-speed capability curve on top of an
        efficiency map: flat peak torque up to base RPM = peak power / peak
        torque, then the P/omega hyperbola. This is exactly the boundary of
        _motor_capability_mask, so anything colored above the line means the
        mask isn't active. Mirrored into negative torque when the axes show
        regen. Silently does nothing when peak torque/power don't parse (the
        same condition that disables masking). Call AFTER the contours are
        drawn -- it reads the axis limits and restores them."""
        ax = ax if ax is not None else self.ax
        try:
            if motor == 1:
                peak_torque = float(self.motor1_max_torque.get())
                peak_power_kw = float(self.motor1_max_power.get())
            else:
                peak_torque = float(self.motor2_max_torque.get())
                peak_power_kw = float(self.motor2_max_power.get())
        except Exception:
            return
        if peak_torque <= 0 or peak_power_kw <= 0:
            return
        try:
            xlims, ylims = ax.get_xlim(), ax.get_ylim()
            peak_power_w = peak_power_kw * 1000.0
            # Same battery-DC-limit treatment as _motor_capability_mask, so
            # the drawn envelope stays the boundary of the mask: constant
            # Vdc*Idc*eta substitution when no efficiency maps are loaded,
            # map-aware per-point solve when they are.
            _cap_fn = getattr(self, "get_battery_power_cap_w", None)
            batt_cap_w = _cap_fn() if callable(_cap_fn) else None
            _pdc_fn = getattr(self, "get_battery_dc_power_w", None)
            _eta_get = getattr(self, "_battery_eta_fn", None)
            p_dc = _pdc_fn() if callable(_pdc_fn) else None
            batt_eta_fn = (_eta_get() if (p_dc is not None and callable(_eta_get))
                           else None)
            if batt_cap_w is not None and batt_eta_fn is None:
                peak_power_w = min(peak_power_w, float(batt_cap_w))
            base_rpm = (peak_power_w / peak_torque) * 60.0 / (2.0 * np.pi)
            rpm_end = max(xlims[1], base_rpm)
            rpm_flat = np.array([max(xlims[0], 0.0), min(base_rpm, rpm_end)])
            rpm_hyp = np.linspace(min(base_rpm, rpm_end), rpm_end, 200)
            tq_hyp = peak_power_w / np.maximum(rpm_hyp * 2.0 * np.pi / 60.0, 1e-9)
            tq_hyp = np.minimum(tq_hyp, peak_torque)
            rpm_curve = np.concatenate([rpm_flat, rpm_hyp])
            tq_curve = np.concatenate([np.full(rpm_flat.shape, peak_torque), tq_hyp])
            if batt_eta_fn is not None:
                # Battery limit evaluated after the efficiency maps: clip the
                # motor envelope to T <= Vdc*Idc*eta(T,w)/w, solved per point.
                from .calc_ext import cap_torque_to_power_via_eff
                omega_curve = np.maximum(rpm_curve * 2.0 * np.pi / 60.0, 1e-9)
                tq_curve = cap_torque_to_power_via_eff(
                    tq_curve, omega_curve, float(p_dc),
                    lambda t, om: batt_eta_fn(
                        t, np.asarray(om, dtype=float) * 60.0 / (2.0 * np.pi)))
            ax.plot(rpm_curve, tq_curve, color="black", linewidth=2.0,
                    linestyle="-", zorder=6, label=label)
            if ylims[0] < 0:   # map shows regen torque: mirror the envelope
                ax.plot(rpm_curve, -tq_curve, color="black", linewidth=2.0,
                        linestyle="-", zorder=6)
            # Keep the map's own extent; the curve is clipped at the edges.
            ax.set_xlim(xlims)
            ax.set_ylim(ylims)
        except Exception:
            pass

    def _extrapolate_eff_gaps(self, eff_percent, torque_grid, speed_grid):
        """Fill NaN cells (points the datasheet map didn't measure) with the
        nearest known value on the same (torque, rpm) grid, so a hole inside
        the motor's reachable envelope doesn't leave a blank patch next to the
        capability curve. Nearest-neighbor only -- no extrapolated trend, so
        it can't invent an efficiency the data doesn't support. This runs
        BEFORE `_motor_capability_mask` is applied, so points genuinely
        outside the envelope are still blanked afterward regardless of this
        toggle; it only ever fills gaps, never uncovers new territory."""
        vals = np.asarray(eff_percent, dtype=float)
        valid = np.isfinite(vals)
        if not np.any(valid) or np.all(valid):
            return vals
        tq = np.asarray(torque_grid, dtype=float)
        rpm = np.asarray(speed_grid, dtype=float)
        interp = NearestNDInterpolator(
            np.column_stack([tq[valid], rpm[valid]]), vals[valid])
        missing = ~valid
        filled = vals.copy()
        filled[missing] = interp(np.column_stack([tq[missing], rpm[missing]]))
        return filled

    def _dense_regrid_eff(self, torque_axis, rpm_axis, eff_matrix, n=200):
        """Resample a (NaN-free, post-extrapolation) map onto a dense, evenly
        spaced (torque, rpm) grid via bilinear interpolation -- the same
        fine-grid technique the Combined/Difference maps already use
        unconditionally. Without this, contourf on the map's own coarse,
        unevenly-spaced axis kills an entire quad the moment ANY one of its
        four corners is NaN, so a single rejected node just outside the
        capability curve can erase a much larger swath of *accepted*,
        already-filled cells next to it -- the real source of the big blank
        wedge next to the curve, not just the literal missing cells.
        Returns (speed_grid, torque_grid, eff_grid) ready for contourf."""
        tq = np.asarray(torque_axis, dtype=float)
        rpm = np.asarray(rpm_axis, dtype=float)
        rpm_d = np.linspace(rpm.min(), rpm.max(), n)
        tq_d = np.linspace(tq.min(), tq.max(), n)
        speed_grid, torque_grid = np.meshgrid(rpm_d, tq_d)
        interp = RegularGridInterpolator((tq, rpm), eff_matrix,
                                         bounds_error=False, fill_value=np.nan)
        pts = np.column_stack((torque_grid.ravel(), speed_grid.ravel()))
        eff_grid = interp(pts).reshape(torque_grid.shape)
        return speed_grid, torque_grid, eff_grid

    def _plot_range_efficiency_map_panel(
        self,
        ax,
        torque_axis,
        rpm_axis,
        eff_map,
        title,
        colorbar_label,
        default_eff=0.9,
        overlay_rpm=None,
        overlay_torque=None,
    ):
        ax.set_xlabel("Speed (RPM)")
        ax.set_ylabel("Torque (Nm)")
        # 'line': grid above the contourf fill, below plotted lines/markers --
        # this panel is multi-axis (Range's Graph Settings apply per-panel via
        # _range_apply_gs, not the single-axis apply_graph_style), so the
        # axisbelow fix has to live here too.
        ax.set_axisbelow('line')
        ax.grid(True, linestyle='--', alpha=0.5)

        if eff_map is None or torque_axis is None or rpm_axis is None:
            ax.set_title(f"{title} (Constant {default_eff * 100.0:.1f}%)", fontsize=14, weight='bold', color='blue')
            ax.text(
                0.5,
                0.5,
                f"No efficiency map uploaded.\nUsing constant efficiency = {default_eff * 100.0:.1f}%",
                transform=ax.transAxes,
                ha='center',
                va='center',
                fontsize=11,
                color=COLORS['text'],
            )
            if overlay_rpm is not None and overlay_torque is not None:
                ov_rpm = np.asarray(overlay_rpm, dtype=float)
                ov_torque = np.asarray(overlay_torque, dtype=float)
                mask = np.isfinite(ov_rpm) & np.isfinite(ov_torque)
                if np.any(mask):
                    ax.scatter(ov_rpm[mask], ov_torque[mask], s=8, alpha=0.5, color='black', label="Operating points")
                    ax.legend(loc="upper right", fontsize=9)
            return

        rpm_vals = np.asarray(rpm_axis, dtype=float)
        torque_vals = np.asarray(torque_axis, dtype=float)
        eff_vals = np.asarray(eff_map, dtype=float) * 100.0
        speed_grid, torque_grid = np.meshgrid(rpm_vals, torque_vals)

        # Optional smoothing (off by default, same technique and contract as
        # Drive Cycle Efficiency's "Extrapolate to envelope" toggle): nearest-
        # neighbor-fill the map's own NaN holes, then bilinearly resample onto
        # a dense 200x200 grid before masking. Without this, contourf drops an
        # entire grid quad the moment any one of its four corners is NaN, so a
        # few datasheet gaps right next to the capability curve can blank out
        # a much bigger, already-valid swath than they actually cover -- this
        # is what makes the map look coarse/blocky instead of a smooth field.
        # The capability mask below still wins regardless of this toggle.
        if hasattr(self, "gs_bool") and self.gs_bool("extrapolate_gaps", False):
            eff_vals = self._extrapolate_eff_gaps(eff_vals, torque_grid, speed_grid)
            speed_grid, torque_grid, eff_vals = self._dense_regrid_eff(
                torque_vals, rpm_vals, eff_vals)

        # Blank out points the motor can't physically reach (see
        # _motor_capability_mask) instead of interpolating/extrapolating there.
        cap_mask = self._motor_capability_mask(torque_grid, speed_grid, motor=1)
        if cap_mask is not None:
            eff_vals = np.where(cap_mask, eff_vals, np.nan)

        finite = np.isfinite(eff_vals)
        if not np.any(finite):
            ax.set_title(f"{title} (invalid map data)", fontsize=14, weight='bold', color='blue')
            return

        eff_min = float(np.nanmin(eff_vals[finite]))
        eff_max = float(np.nanmax(eff_vals[finite]))
        if eff_max - eff_min < 1e-6:
            eff_min = max(0.0, eff_min - 1.0)
            eff_max = min(100.0, eff_max + 1.0)
        # Colormap / level counts / opacity from the Range Graph Settings
        # panel; the defaults reproduce the original hard-coded look exactly.
        cmap = self.gs_str('cmap', 'RdYlGn') if hasattr(self, 'gs_str') else 'RdYlGn'
        fill_levels = max(2, self.gs_int('fill_levels', 40)) if hasattr(self, 'gs_int') else 40
        line_levels = max(1, self.gs_int('line_levels', 10)) if hasattr(self, 'gs_int') else 10
        map_alpha = self.gs_float('map_alpha', 0.75) if hasattr(self, 'gs_float') else 0.75
        contour_levels = np.linspace(eff_min, eff_max, fill_levels)

        contour = ax.contourf(speed_grid, torque_grid, eff_vals, cmap=cmap, levels=contour_levels, alpha=map_alpha)
        contour_lines = ax.contour(speed_grid, torque_grid, eff_vals, colors='black', linewidths=0.2, levels=line_levels)
        ax.clabel(contour_lines, inline=True, fontsize=8, fmt="%.0f")
        self.range_eff_colorbar = self.figure.colorbar(contour, ax=ax, label=colorbar_label, ticks=range(0, 101, 10))
        self._draw_motor_capability_curve(ax=ax)

        if overlay_rpm is not None and overlay_torque is not None:
            ov_rpm = np.asarray(overlay_rpm, dtype=float)
            ov_torque = np.asarray(overlay_torque, dtype=float)
            mask = np.isfinite(ov_rpm) & np.isfinite(ov_torque)
            if np.any(mask):
                ax.scatter(ov_rpm[mask], ov_torque[mask], s=10, alpha=0.45, color='black', label="Operating points")
                ax.legend(loc="upper right", fontsize=9)

        ax.set_title(title, fontsize=14, weight='bold', color='blue')


    def _build_eff_map_from_drive_cycle_efficiency_section(self, motor=1):
        """
        Build a normalized efficiency map (torque axis, rpm axis, matrix[torque,rpm])
        from the Drive Cycle Efficiency section (Motor 1 / Motor 2 uploads).
        """
        if motor == 1:
            # Prefer cached parsed map if available.
            if self.eff1_map_matrix is not None and self.eff1_map_torques is not None and self.eff1_map_rpms is not None:
                return self.eff1_map_torques, self.eff1_map_rpms, self.eff1_map_matrix
            df_src = getattr(self, "efficiency_data_1", None)
        else:
            if self.eff2_map_matrix is not None and self.eff2_map_torques is not None and self.eff2_map_rpms is not None:
                return self.eff2_map_torques, self.eff2_map_rpms, self.eff2_map_matrix
            df_src = getattr(self, "efficiency_data_2", None)

        if df_src is None:
            return None, None, None

        try:
            tq_axis, rpm_axis, eff_map = self._extract_eff_map_from_dataframe(df_src)
            if motor == 1:
                self.eff1_map_torques = tq_axis
                self.eff1_map_rpms = rpm_axis
                self.eff1_map_matrix = eff_map
            else:
                self.eff2_map_torques = tq_axis
                self.eff2_map_rpms = rpm_axis
                self.eff2_map_matrix = eff_map
            return tq_axis, rpm_axis, eff_map
        except Exception:
            return None, None, None


    def _resolve_range_efficiency_map(self, kind="motor"):
        """
        Resolve efficiency map source for Range analysis.
        Priority:
        1) Drive Cycle Efficiency section uploads (connected workflow).
        2) Range-specific uploads (backward compatibility).
        3) None -> use constant efficiency.
        """
        if kind == "motor":
            tq_axis, rpm_axis, eff_map = self._build_eff_map_from_drive_cycle_efficiency_section(motor=1)
            if eff_map is not None:
                return tq_axis, rpm_axis, eff_map, "Drive Cycle Efficiency (Motor 1)"
            if self.range_motor_efficiency_map is not None:
                return (
                    self.range_motor_eff_map_torques,
                    self.range_motor_eff_map_rpms,
                    self.range_motor_efficiency_map,
                    "Range Upload",
                )
            return None, None, None, "Constant"

        tq_axis, rpm_axis, eff_map = self._build_eff_map_from_drive_cycle_efficiency_section(motor=2)
        if eff_map is not None:
            return tq_axis, rpm_axis, eff_map, "Drive Cycle Efficiency (Motor 2)"
        if self.range_controller_efficiency_map is not None:
            return (
                self.range_controller_eff_map_torques,
                self.range_controller_eff_map_rpms,
                self.range_controller_efficiency_map,
                "Range Upload",
            )
        return None, None, None, "Constant"


    def _update_drive_cycle_efficiency_label(
        self,
        motor_eff_pct=None,
        controller_eff_pct=None,
        overall_eff_pct=None,
        motor_source="Constant",
        controller_source="Constant",
    ):
        if not hasattr(self, "drive_cycle_efficiency_label"):
            return
        if motor_eff_pct is None or controller_eff_pct is None or overall_eff_pct is None:
            self.drive_cycle_efficiency_label.configure(
                text="Drive Cycle Efficiency: Not available (upload drive cycle and run Range analysis)."
            )
            return
        self.drive_cycle_efficiency_label.configure(
            text=(
                f"Drive Cycle Efficiency (motoring only)\n"
                f"Motor: {motor_eff_pct:.2f}% | Controller: {controller_eff_pct:.2f}% | Wheel/Controller: {overall_eff_pct:.2f}%\n"
                f"Map Source -> Motor: {motor_source} | Controller: {controller_source}"
            )
        )


    def _set_entry_csv_from_axis(self, entry_widget, axis_values):
        if entry_widget is None or axis_values is None:
            return
        vals = np.asarray(axis_values, dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            return
        csv_text = ",".join(f"{float(v):g}" for v in vals)
        entry_widget.delete(0, "end")
        entry_widget.insert(0, csv_text)


    def _sync_shared_efficiency_ticks(self):
        """
        Keep indicator ticks in sync across Drive Cycle Efficiency and Range sections.
        A green tick means map is available from at least one section.
        """
        motor_available = (getattr(self, "efficiency_data_1", None) is not None) or (getattr(self, "range_motor_efficiency_map", None) is not None)
        controller_available = (getattr(self, "efficiency_data_2", None) is not None) or (getattr(self, "range_controller_efficiency_map", None) is not None)

        if hasattr(self, "eff1_indicator"):
            self.eff1_indicator.configure(
                text="\u2705" if motor_available else "\u274C",
                text_color=COLORS['success'] if motor_available else COLORS['warning'],
            )
        if hasattr(self, "eff2_indicator"):
            self.eff2_indicator.configure(
                text="\u2705" if controller_available else "\u274C",
                text_color=COLORS['success'] if controller_available else COLORS['warning'],
            )
        if hasattr(self, "range_motor_eff_indicator"):
            self.range_motor_eff_indicator.configure(
                text="\u2705" if motor_available else "\u274C",
                text_color=COLORS['success'] if motor_available else COLORS['warning'],
            )
        if hasattr(self, "range_controller_eff_indicator"):
            self.range_controller_eff_indicator.configure(
                text="\u2705" if controller_available else "\u274C",
                text_color=COLORS['success'] if controller_available else COLORS['warning'],
            )


    def interpolate_efficiency_map_motor1(self, df, speed_grid, torque_grid, rated_speed, max_speed, max_torque):
        """
        Interpolates the efficiency map DataFrame onto a regular grid.
        Returns a DataFrame with interpolated efficiency values.
        """
        del rated_speed, max_speed, max_torque  # no envelope clipping; use uploaded map axes directly
        torque_values, speed_values, efficiency_map = self._extract_eff_map_from_dataframe(df)
        interpolator = RegularGridInterpolator(
            (torque_values, speed_values),
            efficiency_map,
            bounds_error=False,
            fill_value=np.nan
        )
        df_eff = pd.DataFrame(index=speed_grid, columns=torque_grid, dtype=float)
        for s in speed_grid:
            pts = np.column_stack((np.asarray(torque_grid, dtype=float), np.full(len(torque_grid), float(s), dtype=float)))
            df_eff.loc[s, :] = interpolator(pts)
        return df_eff.apply(pd.to_numeric, errors='coerce')


    def interpolate_efficiency_map_motor2(self, df, speed_grid, torque_grid, rated_speed, max_speed, max_torque):
        """
        Interpolates the efficiency map DataFrame onto a regular grid.
        Returns a DataFrame with interpolated efficiency values.
        """
        del rated_speed, max_speed, max_torque  # no envelope clipping; use uploaded map axes directly
        torque_values, speed_values, efficiency_map = self._extract_eff_map_from_dataframe(df)
        interpolator = RegularGridInterpolator(
            (torque_values, speed_values),
            efficiency_map,
            bounds_error=False,
            fill_value=np.nan
        )
        df_eff = pd.DataFrame(index=speed_grid, columns=torque_grid, dtype=float)
        for s in speed_grid:
            pts = np.column_stack((np.asarray(torque_grid, dtype=float), np.full(len(torque_grid), float(s), dtype=float)))
            df_eff.loc[s, :] = interpolator(pts)
        return df_eff.apply(pd.to_numeric, errors='coerce')
    

    def get_motor_params(self, motor=1):
        if motor == 1:
            max_speed = float(self.motor1_max_speed.get() )
            rated_speed = float(self.motor1_rated_speed.get() )
            max_torque = float(self.motor1_max_torque.get() )
            max_power = float(self.motor1_max_power.get() )
        else:
            max_speed = float(self.motor2_max_speed.get())
            rated_speed = float(self.motor2_rated_speed.get() )
            max_torque = float(self.motor2_max_torque.get() )
            max_power = float(self.motor2_max_power.get() )
        return max_speed, rated_speed, max_torque,max_power
    

    def _overlay_thermal_points_on_map(self, ax=None):
        """Overlay the thermal-load duty points (motor RPM, motor Nm) on an
        efficiency map so the user can see whether each sustained condition
        sits in an efficient, reachable part of the map. Fail-soft: overlay
        off / helper missing (mixin-only test host) -> draws nothing."""
        fn = getattr(self, "compute_thermal_load_points", None)
        if not callable(fn):
            return
        try:
            pts = fn() or []
        except Exception:
            return
        ax = ax if ax is not None else self.ax
        first = True
        for i, p in enumerate(pts, 1):
            ax.scatter(
                p["motor_rpm"], p["motor_torque"], marker="X", s=110,
                color="crimson", edgecolors="white", linewidths=0.8, zorder=8,
                label="Thermal load points" if first else None)
            ax.annotate(
                f"{i}: {p['duration_s']:g}s",
                xy=(p["motor_rpm"], p["motor_torque"]), xytext=(7, 7),
                textcoords="offset points", fontsize=9, color="crimson",
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="crimson",
                          alpha=0.8))
            first = False

    def _overlay_drive_cycle_on_efficiency_plot(self, regen=False):
        """Overlay the drive-cycle operating points on the current efficiency map.

        Uses the same force model as the efficiency numbers/Range (via
        `_drive_cycle_operating_points`). The Graph Settings 'Overlay style'
        chooses Scatter, a density/energy Heatmap (hexbin), or Both. Heatmap
        weight (point count vs tractive energy Wh) follows 'Overlay weight'.
        `regen=True` (used by `plot_efficiency_map_regen`) overlays the
        braking points (negative motor torque/power) instead of motoring ones.
        """
        if not (hasattr(self, "show_drive_cycle_toggle_var") and self.show_drive_cycle_toggle_var.get()):
            return
        op = self._drive_cycle_operating_points()
        if op is None:
            return

        x = np.asarray(op["motor_rpm"], dtype=float)
        y = np.asarray(op["motor_torque"], dtype=float)
        mp = np.asarray(op["motor_power"], dtype=float)
        dt_hr = np.asarray(op["dt_hr"], dtype=float)
        sign_mask = (y < 0) if regen else (y > 0)
        mask = np.isfinite(x) & np.isfinite(y) & sign_mask
        if not np.any(mask):
            return
        x, y = x[mask], y[mask]
        energy_wh = np.abs(mp[mask]) * dt_hr[mask]   # per-point tractive/regen energy magnitude

        style = self.gs_str("overlay_style", "Scatter")
        weight = self.gs_str("overlay_weight", "Point Count")
        show_hm = style in ("Heatmap", "Both")
        show_sc = style in ("Scatter", "Both")

        if show_hm and x.size > 1:
            C = energy_wh if weight.startswith("Energy") else None
            reduce_fn = np.sum if C is not None else None
            try:
                hb = self.ax.hexbin(
                    x, y, C=C, reduce_C_function=(reduce_fn if reduce_fn else np.sum),
                    gridsize=self.gs_int("overlay_gridsize", 30),
                    cmap=self.gs_str("overlay_cmap", "hot"),
                    mincnt=1, alpha=float(self.gs_float("overlay_alpha", 0.55)), zorder=3,
                )
                self.heatmap_colorbar = self.figure.colorbar(hb, ax=self.ax)
                self.heatmap_colorbar.set_label(
                    "Operating energy (Wh)" if C is not None else "Operating point count"
                )
            except Exception:
                show_sc = True  # fall back to scatter on any hexbin issue

        if show_sc:
            self.ax.scatter(x, y, color=COLORS['primary'], s=10, alpha=0.7,
                            label="Drive Cycle Torque-Speed" + (" (Regen)" if regen else ""), zorder=4)


    # ------------------------------------------------------------------ #
    #  Drive-cycle operating points + combined (motor x controller) energy #
    #  efficiency. Same force model as Range analysis, kept in one helper  #
    #  so the map overlay and the efficiency numbers stay consistent.      #
    # ------------------------------------------------------------------ #
    def _drive_cycle_operating_points(self):
        """Per-point drive-cycle operating data using the Range force model.

        Returns a dict (time, dt_hr, speed, motor_torque, motor_rpm,
        motor_omega, wheel_power, motor_power) or None if no valid drive cycle.
        """
        if not hasattr(self, "dataframe") or self.dataframe is None:
            return None
        df_dc = self.dataframe.copy()
        if "dc_time" not in df_dc.columns or "dc_speed" not in df_dc.columns:
            return None
        try:
            m_ref = float(self.m_ref.get())
            rear_load_ratio = float(self.rear_load_ratio.get())
            ambient_temp = float(self.ambient_temp.get())
            ambient_pressure = float(self.ambient_pressure.get())
            crr = float(self.crr.get()) if self.crr.get().strip() else None
            cd_a = float(self.cd_a.get()) if self.cd_a.get().strip() else None
            wheel_radius = float(self.wheel_radius.get())
            gear_ratio = float(self.gear_ratio.get())
            gear_eff = self.get_gear_efficiency_value()
        except Exception:
            return None

        params = calculate_crr_cd_a(
            m_ref, rear_load_ratio, ambient_temp, ambient_pressure,
            crr=crr if self.crr_manual else None,
            cd_a=cd_a if self.cda_manual else None,
        )

        time = pd.to_numeric(df_dc["dc_time"], errors='coerce').to_numpy(dtype=float)
        speed = pd.to_numeric(df_dc["dc_speed"], errors='coerce').to_numpy(dtype=float)
        valid = np.isfinite(time) & np.isfinite(speed)
        time, speed = time[valid], speed[valid]
        if time.size < 2:
            return None
        order = np.argsort(time)
        time, speed = time[order], speed[order]
        um = np.concatenate(([True], np.diff(time) > 0))
        time, speed = time[um], speed[um]
        if time.size < 2:
            return None

        try:
            gradient = float(self.get_gradients_pct()[0])
        except Exception:
            gradient = 0.0

        speed_mps = speed / 3.6
        dt = np.clip(np.diff(time, prepend=time[0]), 0.0, None)
        dt_hr = dt / 3600.0
        acc = np.zeros_like(speed_mps)
        dtd = np.diff(time)
        with np.errstate(divide='ignore', invalid='ignore'):
            acc[1:] = np.divide(np.diff(speed_mps), dtd, out=np.zeros_like(dtd), where=dtd > 0)

        theta = np.arctan(gradient / 100.0)
        rho = 1.225
        f_roll = params['m_i'] * g * params['Crr'] * np.cos(theta) * np.ones_like(speed_mps)
        f_aero = 0.5 * rho * params['CdA'] * (speed_mps ** 2)
        f_grade = params['m_i'] * g * np.sin(theta) * np.ones_like(speed_mps)
        # Inertial term uses the wheel-inertia-corrected mass (m + J/r^2);
        # the steady-state forces above keep the actual mass. J=0 -> identical.
        f_inertia = self.get_effective_inertial_mass(params['m_i'], wheel_radius) * acc
        f_total = f_roll + f_aero + f_grade + f_inertia

        wheel_torque = f_total * wheel_radius
        wheel_omega = np.where(wheel_radius > 0, speed_mps / wheel_radius, 0.0)
        wheel_power = wheel_torque * wheel_omega
        if abs(gear_ratio) < 1e-9 or wheel_radius <= 0:
            motor_torque = np.zeros_like(wheel_torque)
            motor_rpm = np.zeros_like(speed_mps)
            motor_omega = np.zeros_like(speed_mps)
        else:
            wheel_rpm = wheel_omega * 60.0 / (2.0 * np.pi)
            motor_rpm = wheel_rpm * gear_ratio
            motor_omega = motor_rpm * 2.0 * np.pi / 60.0
            motor_torque = wheel_torque / max(abs(gear_ratio * gear_eff), 1e-9)
        motor_power = motor_torque * motor_omega

        return dict(
            time=time, dt_hr=dt_hr, speed=speed,
            motor_torque=motor_torque, motor_rpm=motor_rpm, motor_omega=motor_omega,
            wheel_power=wheel_power, motor_power=motor_power,
        )

    def compute_drive_cycle_efficiency_metrics(self):
        """Combined motor x controller drive-cycle efficiency, two ways:

        * Energy-based: sum each point's mechanical energy, divide by the battery
          energy it needs (mech / (eta_motor*eta_controller) while motoring).
        * Average-of-points: unweighted mean of the per-point combined efficiency
          over the motoring points.

        Regen (braking points) is included: energy returned to the battery is the
        braking mechanical energy * (eta_motor*eta_controller), optionally capped
        by the Range 'regen cap' field. Returns a metrics dict or None.
        """
        op = self._drive_cycle_operating_points()
        if op is None:
            self._update_drive_cycle_efficiency_label_v2(None)
            return None

        motor_eff_const = (
            self._get_eff_constant(self.motor_eff_const, 0.90)
            if getattr(self, "motor_eff_const", None) is not None else 0.90
        )
        controller_eff_const = (
            self._get_eff_constant(self.controller_eff_const, 0.95)
            if getattr(self, "controller_eff_const", None) is not None else 0.95
        )
        m_tq, m_rpm, m_mat, m_src = self._resolve_range_efficiency_map(kind="motor")
        c_tq, c_rpm, c_mat, c_src = self._resolve_range_efficiency_map(kind="controller")

        mt, mr, mp, dt_hr = op["motor_torque"], op["motor_rpm"], op["motor_power"], op["dt_hr"]
        eta_m = self._interpolate_efficiency_or_constant(mt, mr, m_mat, m_tq, m_rpm, motor_eff_const)
        eta_c = self._interpolate_efficiency_or_constant(mt, mr, c_mat, c_tq, c_rpm, controller_eff_const)
        combined = np.clip(eta_m * eta_c, 1e-6, 1.0)

        motoring = mp > 0
        braking = mp < 0
        e_mech_out = float(np.nansum(mp[motoring] * dt_hr[motoring]))                       # Wh
        e_batt_in = float(np.nansum((mp[motoring] / combined[motoring]) * dt_hr[motoring]))  # Wh

        # Regen: battery power recovered = |mech| * combined (optional W cap).
        regen_power = np.abs(mp[braking]) * combined[braking]
        cap = None
        if getattr(self, "regen_cap_w", None) is not None:
            try:
                if self.regen_cap_w.get().strip():
                    cap = float(self.regen_cap_w.get())
            except Exception:
                cap = None
        if cap is not None:
            regen_power = np.clip(regen_power, 0.0, cap)
        e_regen = float(np.nansum(regen_power * dt_hr[braking]))

        energy_eff = (e_mech_out / e_batt_in) if e_batt_in > 1e-9 else 0.0
        avg_eff = float(np.nanmean(combined[motoring])) if np.any(motoring) else 0.0
        net_batt = e_batt_in - e_regen
        net_eff = (e_mech_out / net_batt) if net_batt > 1e-9 else 0.0

        metrics = dict(
            energy_eff=energy_eff * 100.0,
            avg_eff=avg_eff * 100.0,
            net_eff=net_eff * 100.0,
            e_mech_out=e_mech_out,
            e_batt_in=e_batt_in,
            e_regen=e_regen,
            motor_source=(m_src if m_mat is not None else f"Constant ({motor_eff_const * 100.0:.1f}%)"),
            controller_source=(c_src if c_mat is not None else f"Constant ({controller_eff_const * 100.0:.1f}%)"),
        )
        self._last_dce_metrics = metrics
        self._update_drive_cycle_efficiency_label_v2(metrics)
        return metrics

    def _refresh_efficiency_after_change(self):
        """After a map is uploaded/deleted, recompute the drive-cycle efficiency
        numbers (if a drive cycle is loaded) and, when the current analysis is
        Drive Cycle Efficiency, redraw whichever map view was last shown."""
        try:
            self.compute_drive_cycle_efficiency_metrics()
        except Exception:
            pass
        try:
            if getattr(self, "plot_mode", None) == "Drive Cycle Efficiency":
                last = getattr(self, "_last_eff_plot", None)
                if callable(last):
                    last()
        except Exception:
            pass

    def _update_drive_cycle_efficiency_label_v2(self, m):
        if not hasattr(self, "drive_cycle_efficiency_label"):
            return
        if m is None:
            self.drive_cycle_efficiency_label.configure(
                text="Drive Cycle Efficiency: upload a drive cycle, then press Compute."
            )
            return
        regen_pct = (m["e_regen"] / m["e_batt_in"] * 100.0) if m["e_batt_in"] > 1e-9 else 0.0
        self.drive_cycle_efficiency_label.configure(
            text=(
                f"Drive Cycle Efficiency (Motor × Controller)\n"
                f"Energy-based: {m['energy_eff']:.2f}%   |   Average of points: {m['avg_eff']:.2f}%\n"
                f"With regen (net): {m['net_eff']:.2f}%   |   Regen recovered: {m['e_regen']:.1f} Wh "
                f"({regen_pct:.1f}% of draw)\n"
                f"Maps -> Motor: {m['motor_source']} | Controller: {m['controller_source']}"
            )
        )

    def plot_efficiency_map_combined(self):
        """Combined (motor x controller) efficiency contour over the overlapping
        torque/RPM region of the two uploaded maps."""
        self._last_eff_plot = self.plot_efficiency_map_combined
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()

        if self.efficiency_data_1 is None or self.efficiency_data_2 is None:
            messagebox.showerror("Error", "Upload BOTH the Motor and Controller efficiency maps to see the combined map.")
            return
        try:
            cmap = self.gs_str('cmap', 'viridis')
            fill_levels = max(2, self.gs_int('fill_levels', 50))
            line_levels = max(1, self.gs_int('line_levels', 20))

            tq1, rpm1, eff1 = self._extract_eff_map_from_dataframe(self.efficiency_data_1)
            tq2, rpm2, eff2 = self._extract_eff_map_from_dataframe(self.efficiency_data_2)
            self.eff1_map_torques, self.eff1_map_rpms, self.eff1_map_matrix = tq1, rpm1, eff1
            self.eff2_map_torques, self.eff2_map_rpms, self.eff2_map_matrix = tq2, rpm2, eff2

            if self.gs_bool("extrapolate_gaps", False):
                speed_grid1, torque_grid1 = np.meshgrid(rpm1, tq1)
                speed_grid2, torque_grid2 = np.meshgrid(rpm2, tq2)
                eff1 = self._extrapolate_eff_gaps(eff1, torque_grid1, speed_grid1)
                eff2 = self._extrapolate_eff_gaps(eff2, torque_grid2, speed_grid2)

            rpm_min = max(float(np.min(rpm1)), float(np.min(rpm2)))
            rpm_max = min(float(np.max(rpm1)), float(np.max(rpm2)))
            tq_min = max(float(np.min(tq1)), float(np.min(tq2)))
            tq_max = min(float(np.max(tq1)), float(np.max(tq2)))
            if rpm_max <= rpm_min or tq_max <= tq_min:
                raise ValueError("No overlapping RPM/Torque region between the Motor and Controller maps.")

            rpm_grid = np.linspace(rpm_min, rpm_max, 200)
            tq_grid = np.linspace(tq_min, tq_max, 200)
            speed_mesh, torque_mesh = np.meshgrid(rpm_grid, tq_grid)
            pts = np.column_stack((torque_mesh.ravel(), speed_mesh.ravel()))
            i1 = RegularGridInterpolator((tq1, rpm1), eff1, bounds_error=False, fill_value=np.nan)
            i2 = RegularGridInterpolator((tq2, rpm2), eff2, bounds_error=False, fill_value=np.nan)
            combined = (i1(pts) * i2(pts)).reshape(torque_mesh.shape) * 100.0

            cap_mask = self._motor_capability_mask(torque_mesh, speed_mesh, motor=1)
            if cap_mask is not None:
                combined = np.where(cap_mask, combined, np.nan)

            contour = self.ax.contourf(speed_mesh, torque_mesh, combined, cmap=cmap, levels=fill_levels)
            self.efficiency_colorbar = self.figure.colorbar(contour, ax=self.ax)
            self.efficiency_colorbar.set_label('Combined Efficiency (%)', fontsize=14, weight='bold')
            self.efficiency_colorbar.ax.tick_params(labelsize=16)
            contour_lines = self.ax.contour(speed_mesh, torque_mesh, combined, colors='black', levels=line_levels, linewidths=0.5)
            self.ax.clabel(contour_lines, inline=True, fontsize=10, fmt='%1.0f%%', rightside_up=True)
            self.ax.set_xlabel('Speed (RPM)', fontsize=18, weight='bold')
            self.ax.set_ylabel('Torque (Nm)', fontsize=18, weight='bold')
            self.ax.set_title('Combined Efficiency Map (Motor × Controller)', fontsize=20, weight='bold')
            self.ax.tick_params(axis='both', labelsize=16)
            self.ax.grid(True, linestyle='--', alpha=0.8)

            self._draw_motor_capability_curve()
            self._overlay_thermal_points_on_map()
            self._overlay_drive_cycle_on_efficiency_plot()
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e:
            logger.error("Error plotting combined efficiency map: %s", e)
            messagebox.showerror("Plot Error", str(e))

    def plot_efficiency_map_motor1(self):
        self._last_eff_plot = self.plot_efficiency_map_motor1  # for live graph-settings updates
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()

        if self.efficiency_data_1 is None:
            messagebox.showerror("Error", "No efficiency data uploaded for Motor 1.")
            return

        try:
            cmap = self.gs_str('cmap', 'viridis')
            fill_levels = max(2, self.gs_int('fill_levels', 50))
            line_levels = max(1, self.gs_int('line_levels', 20))

            torque_axis, rpm_axis, eff_map = self._extract_eff_map_from_dataframe(self.efficiency_data_1)
            self.eff1_map_torques = torque_axis
            self.eff1_map_rpms = rpm_axis
            self.eff1_map_matrix = eff_map
            self._autofill_motor_params_from_map(1, torque_axis, rpm_axis, eff_map)

            eff_percent = np.asarray(eff_map, dtype=float) * 100.0
            speed_grid, torque_grid = np.meshgrid(rpm_axis, torque_axis)
            if self.gs_bool("extrapolate_gaps", False):
                eff_percent = self._extrapolate_eff_gaps(eff_percent, torque_grid, speed_grid)
                speed_grid, torque_grid, eff_percent = self._dense_regrid_eff(
                    torque_axis, rpm_axis, eff_percent)
            # Blank out combinations the motor can't physically reach (peak
            # torque up to base speed, power-limited beyond it, nothing past
            # max speed) instead of showing interpolated efficiency there.
            cap_mask = self._motor_capability_mask(torque_grid, speed_grid, motor=1)
            if cap_mask is not None:
                eff_percent = np.where(cap_mask, eff_percent, np.nan)
            contour = self.ax.contourf(speed_grid, torque_grid, eff_percent, cmap=cmap, levels=fill_levels)
            self.efficiency_colorbar = self.figure.colorbar(contour, ax=self.ax)
            self.efficiency_colorbar.set_label('Efficiency (%)', fontsize=14, weight='bold')
            self.efficiency_colorbar.ax.tick_params(labelsize=16)
            contour_lines = self.ax.contour(speed_grid, torque_grid, eff_percent, colors='black', levels=line_levels, linewidths=0.5)
            self.ax.clabel(contour_lines, inline=True, fontsize=10, fmt='%1.0f%%', rightside_up=True)
            self.ax.set_xlabel('Speed (RPM)', fontsize=18, weight='bold')
            self.ax.set_ylabel('Torque (Nm)', fontsize=18, weight='bold')
            self.ax.set_title('Efficiency Contour Plot Motor 1', fontsize=24, weight='bold')
            self.ax.tick_params(axis='both', labelsize=16)
            self.ax.grid(True, linestyle='--', alpha=0.8)

            self._draw_motor_capability_curve()
            self._overlay_thermal_points_on_map()
            self._overlay_drive_cycle_on_efficiency_plot()
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e:
            logger.error("Error plotting Motor 1 efficiency map: %s", e)
            messagebox.showerror("Plot Error", str(e))


    def plot_efficiency_map_regen(self):
        """Regen (braking) view of the Motor map: negative-torque half, values
        mirrored from the uploaded (motoring-only) map.

        No datasheet map measures regen efficiency separately, so the app has
        always assumed |T|,RPM motoring efficiency applies to braking too --
        `_interpolate_efficiency_or_constant` already looks maps up via
        `np.abs(torque)` and `_motor_capability_mask` is already symmetric in
        torque -- but until now there was no view that actually SHOWED that
        assumption. This mirrors the Motor 1 map about T=0 and plots only the
        T<=0 half, so the mirrored region is visually obvious rather than
        implicit in the math.
        """
        self._last_eff_plot = self.plot_efficiency_map_regen
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()

        if self.efficiency_data_1 is None:
            messagebox.showerror("Error", "No efficiency data uploaded for Motor 1.")
            return

        try:
            cmap = self.gs_str('cmap', 'viridis')
            fill_levels = max(2, self.gs_int('fill_levels', 50))
            line_levels = max(1, self.gs_int('line_levels', 20))

            torque_axis, rpm_axis, eff_map = self._extract_eff_map_from_dataframe(self.efficiency_data_1)
            self.eff1_map_torques = torque_axis
            self.eff1_map_rpms = rpm_axis
            self.eff1_map_matrix = eff_map
            self._autofill_motor_params_from_map(1, torque_axis, rpm_axis, eff_map)

            eff_percent = np.asarray(eff_map, dtype=float) * 100.0
            torque_pos = np.asarray(torque_axis, dtype=float)

            # Mirror the motoring torque axis/matrix about T=0 to build the
            # regen half. Datasheet torque axes are almost always >= 0 (see
            # generate_sample_data.py); a map that already carries negative
            # torque rows is used as-is instead of double-mirroring it.
            if np.all(torque_pos >= 0):
                neg_torque = -torque_pos[::-1]
                neg_eff = eff_percent[::-1, :]
                if np.isclose(torque_pos[0], 0.0):
                    full_torque_axis = np.concatenate([neg_torque[:-1], torque_pos])
                    full_eff = np.concatenate([neg_eff[:-1, :], eff_percent], axis=0)
                else:
                    full_torque_axis = np.concatenate([neg_torque, torque_pos])
                    full_eff = np.concatenate([neg_eff, eff_percent], axis=0)
            else:
                full_torque_axis = torque_pos
                full_eff = eff_percent

            speed_grid, torque_grid = np.meshgrid(rpm_axis, full_torque_axis)
            if self.gs_bool("extrapolate_gaps", False):
                full_eff = self._extrapolate_eff_gaps(full_eff, torque_grid, speed_grid)
                speed_grid, torque_grid, full_eff = self._dense_regrid_eff(
                    full_torque_axis, rpm_axis, full_eff)

            cap_mask = self._motor_capability_mask(torque_grid, speed_grid, motor=1)
            if cap_mask is not None:
                full_eff = np.where(cap_mask, full_eff, np.nan)

            # Only the regen (T<=0) half is the point of this view -- the
            # motoring half is already the "Plot Motor Efficiency Map" view.
            plotted = np.where(torque_grid <= 0, full_eff, np.nan)

            if not np.any(np.isfinite(plotted)):
                self.show_placeholder_message(
                    "No regen-capable region for the current Motor Max Torque /\n"
                    "Max Power -- the negative-torque envelope is empty."
                )
                return

            contour = self.ax.contourf(speed_grid, torque_grid, plotted, cmap=cmap, levels=fill_levels)
            self.efficiency_colorbar = self.figure.colorbar(contour, ax=self.ax)
            self.efficiency_colorbar.set_label('Regen Efficiency (%, mirrored from motoring)', fontsize=14, weight='bold')
            self.efficiency_colorbar.ax.tick_params(labelsize=16)
            contour_lines = self.ax.contour(speed_grid, torque_grid, plotted, colors='black', levels=line_levels, linewidths=0.5)
            self.ax.clabel(contour_lines, inline=True, fontsize=10, fmt='%1.0f%%', rightside_up=True)
            self.ax.axhline(0, color='#334155', linewidth=1.0, linestyle='--')
            # torque_grid's mesh still spans the (unplotted, all-NaN) positive
            # half too -- contourf/pcolormesh autoscale to the full mesh extent
            # regardless of NaN, so without this the top half of the axes would
            # just be blank space. Restrict the view to the regen half only.
            neg_extent = full_torque_axis[full_torque_axis <= 0]
            if neg_extent.size:
                y_lo = float(np.min(neg_extent))
                self.ax.set_ylim(y_lo * 1.05, max(-y_lo * 0.03, 1.0))
            self.ax.set_xlabel('Speed (RPM)', fontsize=18, weight='bold')
            self.ax.set_ylabel('Torque (Nm)  — negative = regen/braking', fontsize=16, weight='bold')
            self.ax.set_title('Regen (Braking) Efficiency Map — Mirrored from Motoring Data', fontsize=18, weight='bold')
            self.ax.tick_params(axis='both', labelsize=16)
            self.ax.grid(True, linestyle='--', alpha=0.8)

            self._draw_motor_capability_curve()
            self._overlay_drive_cycle_on_efficiency_plot(regen=True)
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e:
            logger.error("Error plotting regen efficiency map: %s", e)
            messagebox.showerror("Plot Error", str(e))

    def plot_efficiency_map_motor2(self):
        self._last_eff_plot = self.plot_efficiency_map_motor2  # for live graph-settings updates
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()

        if self.efficiency_data_2 is None:
            messagebox.showerror("Error", "No efficiency data uploaded for Motor 2.")
            return

        try:
            cmap = self.gs_str('cmap', 'viridis')
            fill_levels = max(2, self.gs_int('fill_levels', 50))
            line_levels = max(1, self.gs_int('line_levels', 20))

            torque_axis, rpm_axis, eff_map = self._extract_eff_map_from_dataframe(self.efficiency_data_2)
            self.eff2_map_torques = torque_axis
            self.eff2_map_rpms = rpm_axis
            self.eff2_map_matrix = eff_map
            self._autofill_motor_params_from_map(2, torque_axis, rpm_axis, eff_map)

            eff_percent = np.asarray(eff_map, dtype=float) * 100.0
            speed_grid, torque_grid = np.meshgrid(rpm_axis, torque_axis)
            if self.gs_bool("extrapolate_gaps", False):
                eff_percent = self._extrapolate_eff_gaps(eff_percent, torque_grid, speed_grid)
                speed_grid, torque_grid, eff_percent = self._dense_regrid_eff(
                    torque_axis, rpm_axis, eff_percent)
            # Same motor envelope as the Motor map: the controller can't drive
            # the shaft past what the motor itself can physically reach.
            cap_mask = self._motor_capability_mask(torque_grid, speed_grid, motor=1)
            if cap_mask is not None:
                eff_percent = np.where(cap_mask, eff_percent, np.nan)
            contour = self.ax.contourf(speed_grid, torque_grid, eff_percent, cmap=cmap, levels=fill_levels)
            self.efficiency_colorbar = self.figure.colorbar(contour, ax=self.ax)
            self.efficiency_colorbar.set_label('Efficiency (%)', fontsize=14, weight='bold')
            self.efficiency_colorbar.ax.tick_params(labelsize=16)
            contour_lines = self.ax.contour(speed_grid, torque_grid, eff_percent, colors='black', levels=line_levels, linewidths=0.5)
            self.ax.clabel(contour_lines, inline=True, fontsize=10, fmt='%1.0f%%', rightside_up=True)
            self.ax.set_xlabel('Speed (RPM)', fontsize=18, weight='bold')
            self.ax.set_ylabel('Torque (Nm)', fontsize=18, weight='bold')
            self.ax.set_title('Efficiency Contour Plot Motor 2', fontsize=24, weight='bold')
            self.ax.tick_params(axis='both', labelsize=16)
            self.ax.grid(True, linestyle='--', alpha=0.8)

            self._draw_motor_capability_curve()
            self._overlay_thermal_points_on_map()
            self._overlay_drive_cycle_on_efficiency_plot()
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e:
            logger.error("Error plotting Motor 2 efficiency map: %s", e)
            messagebox.showerror("Plot Error", str(e))


    def plot_efficiency_difference_map(self):
        self._last_eff_plot = self.plot_efficiency_difference_map  # for live graph-settings updates
        self.safe_remove_colorbar('heatmap_colorbar')
        self.safe_remove_colorbar('efficiency_colorbar')
        self.safe_remove_colorbar('parametric_colorbar')
        self._remove_engine_secondary_axis()
        self.ax.clear()

        if self.efficiency_data_1 is None or self.efficiency_data_2 is None:
            messagebox.showerror("Error", "Please upload both the Motor and Controller efficiency maps.")
            return

        try:
            tq1, rpm1, eff1 = self._extract_eff_map_from_dataframe(self.efficiency_data_1)
            tq2, rpm2, eff2 = self._extract_eff_map_from_dataframe(self.efficiency_data_2)
            self.eff1_map_torques, self.eff1_map_rpms, self.eff1_map_matrix = tq1, rpm1, eff1
            self.eff2_map_torques, self.eff2_map_rpms, self.eff2_map_matrix = tq2, rpm2, eff2

            eff1_pct = np.asarray(eff1, dtype=float) * 100.0
            eff2_pct = np.asarray(eff2, dtype=float) * 100.0

            if self.gs_bool("extrapolate_gaps", False):
                speed_grid1, torque_grid1 = np.meshgrid(rpm1, tq1)
                speed_grid2, torque_grid2 = np.meshgrid(rpm2, tq2)
                eff1_pct = self._extrapolate_eff_gaps(eff1_pct, torque_grid1, speed_grid1)
                eff2_pct = self._extrapolate_eff_gaps(eff2_pct, torque_grid2, speed_grid2)

            rpm_min = max(float(np.min(rpm1)), float(np.min(rpm2)))
            rpm_max = min(float(np.max(rpm1)), float(np.max(rpm2)))
            tq_min = max(float(np.min(tq1)), float(np.min(tq2)))
            tq_max = min(float(np.max(tq1)), float(np.max(tq2)))
            if rpm_max <= rpm_min or tq_max <= tq_min:
                raise ValueError("No overlapping RPM/Torque region between Motor 1 and Motor 2 maps.")

            rpm_grid = np.linspace(rpm_min, rpm_max, 200)
            torque_grid = np.linspace(tq_min, tq_max, 200)
            speed_mesh, torque_mesh = np.meshgrid(rpm_grid, torque_grid)
            sample_points = np.column_stack((torque_mesh.ravel(), speed_mesh.ravel()))

            interp_1 = RegularGridInterpolator((tq1, rpm1), eff1_pct, bounds_error=False, fill_value=np.nan)
            interp_2 = RegularGridInterpolator((tq2, rpm2), eff2_pct, bounds_error=False, fill_value=np.nan)
            eff1_interp = interp_1(sample_points).reshape(torque_mesh.shape)
            eff2_interp = interp_2(sample_points).reshape(torque_mesh.shape)

            diff_map = eff2_interp - eff1_interp
            diff_map[np.abs(diff_map) > 15.0] = np.nan

            cap_mask = self._motor_capability_mask(torque_mesh, speed_mesh, motor=1)
            if cap_mask is not None:
                diff_map = np.where(cap_mask, diff_map, np.nan)

            # Diverging colormap centred on zero: red = Motor 2 worse, blue =
            # Motor 2 better, white = no difference. Far clearer than viridis,
            # which hides the sign of the change.
            finite = diff_map[np.isfinite(diff_map)]
            vmax = float(np.nanmax(np.abs(finite))) if finite.size else 1.0
            vmax = max(vmax, 0.5)
            norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)
            fill_levels = max(2, self.gs_int('fill_levels', 30))
            line_levels = max(1, self.gs_int('line_levels', 10))
            contour = self.ax.contourf(speed_mesh, torque_mesh, diff_map,
                                       cmap='RdBu', norm=norm, levels=fill_levels)
            self.efficiency_colorbar = self.figure.colorbar(contour, ax=self.ax)
            self.efficiency_colorbar.set_label('Efficiency Difference (%)  (Controller - Motor)', fontsize=14, weight='bold')
            self.efficiency_colorbar.ax.tick_params(labelsize=16)
            contour_lines = self.ax.contour(speed_mesh, torque_mesh, diff_map, colors='#334155', levels=line_levels, linewidths=0.5)
            self.ax.clabel(contour_lines, inline=True, fontsize=10, fmt='%1.0f%%', rightside_up=True)
            self.ax.set_xlabel('Speed (RPM)', fontsize=18, weight='bold')
            self.ax.set_ylabel('Torque (Nm)', fontsize=18, weight='bold')
            self.ax.set_title('Efficiency Difference Plot (Controller vs Motor)', fontsize=20, weight='bold')
            self.ax.tick_params(axis='both', labelsize=16)
            self.ax.grid(True, linestyle='--', alpha=0.8)
            self._draw_motor_capability_curve()
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e:
            logger.error("Error plotting efficiency difference map: %s", e)
            messagebox.showerror("Plot Error", str(e))
        
