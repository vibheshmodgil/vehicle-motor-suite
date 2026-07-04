"""
EnhancementsMixin -- all the *new* cross-cutting UI features, kept in one place
so the original/auto-generated mixins stay clean:

  * a top action toolbar + a bottom status bar
  * export current figure (PNG / SVG / PDF) and current data (CSV / XLSX)
  * a one-click standalone HTML report (figure embedded + summary + metrics)
  * save / load all inputs as a JSON "scenario"
  * a Light/Dark plot-theme toggle (post-processes the figure, so it works for
    every plot type without touching the individual plot methods)
  * a cursor read-out wired into the status bar
  * Enter-to-plot and a busy-cursor "safe plot" wrapper that surfaces errors in
    the status bar instead of only raising
  * a loss-breakdown waterfall view for the Range analysis

None of this changes any calculation or any existing input field.
"""

import base64
import io
import json
import os
import webbrowser
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import customtkinter as ctk

from . import theme
from .theme import COLORS
from .formatting import fmt, fmt_wh, fmt_km, fmt_pct


class EnhancementsMixin:

    # ------------------------------------------------------------------ #
    #  Toolbar + status bar                                              #
    # ------------------------------------------------------------------ #
    def build_toolbar(self):
        """A slim action bar of quick buttons under the header."""
        bar = ctk.CTkFrame(self, fg_color=COLORS["header_bg_soft"], corner_radius=0,
                           height=46, border_width=0)
        bar.pack(side="top", fill="x")

        def btn(text, cmd):
            b = ctk.CTkButton(bar, text=text, command=cmd, width=10, height=30,
                              corner_radius=8, font=(COLORS_FONT, 12, "bold"))
            b.pack(side="left", padx=(8, 0), pady=8)
            return b

        btn("Plot  \u23ce", self._safe_plot)
        btn("Export figure", self.export_current_figure)
        btn("Export data", self.export_current_data)
        btn("Report", self.generate_report)
        ctk.CTkLabel(bar, text="\u2502", text_color=COLORS["border"]).pack(side="left", padx=4)
        btn("Save scenario", self.save_scenario)
        btn("Load scenario", self.load_scenario)
        ctk.CTkLabel(bar, text="│", text_color=COLORS["border"]).pack(side="left", padx=4)
        btn("\U0001F4AC Assistant", self.toggle_assistant_panel)

        # Theme toggle pinned to the right.
        self.theme_switch = ctk.CTkSwitch(
            bar, text="Dark plots", command=self.toggle_theme,
            progress_color=COLORS["primary"], font=(COLORS_FONT, 12),
        )
        self.theme_switch.pack(side="right", padx=12, pady=8)

    def build_status_bar(self):
        """A bottom status strip used for messages, errors and the cursor
        read-out."""
        self.status_bar = ctk.CTkFrame(self, fg_color=COLORS["card"], corner_radius=0,
                                       height=28, border_width=1,
                                       border_color=COLORS["border"])
        self.status_bar.pack(side="bottom", fill="x")
        self.status_label = ctk.CTkLabel(
            self.status_bar, text="Ready.", anchor="w",
            font=(COLORS_FONT, 12), text_color=COLORS["text_muted"],
        )
        self.status_label.pack(side="left", fill="x", expand=True, padx=12)
        self.cursor_label = ctk.CTkLabel(
            self.status_bar, text="", anchor="e",
            font=(COLORS_FONT, 11), text_color=COLORS["text_muted"],
        )
        self.cursor_label.pack(side="right", padx=12)

    def set_status(self, message, kind="info"):
        colors = {"info": COLORS["text_muted"], "ok": COLORS["success"],
                  "warn": COLORS["warning"], "error": COLORS["danger"]}
        if hasattr(self, "status_label"):
            try:
                self.status_label.configure(text=message,
                                            text_color=colors.get(kind, COLORS["text_muted"]))
                self.status_label.update_idletasks()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Wiring done once, after the canvas exists                         #
    # ------------------------------------------------------------------ #
    def enh_setup(self):
        self._dark_mode = False
        self._debug = getattr(self, "_debug", False)

        # Wrap canvas.draw / draw_idle so dark mode applies to *every* plot.
        if hasattr(self, "canvas") and not getattr(self, "_draw_wrapped", False):
            for name in ("draw", "draw_idle"):
                orig = getattr(self.canvas, name, None)
                if orig is None:
                    continue

                def make(orig_fn):
                    def wrapped(*a, **k):
                        if getattr(self, "_dark_mode", False):
                            try:
                                theme.apply_dark_to_figure(self.figure)
                            except Exception:
                                pass
                        return orig_fn(*a, **k)
                    return wrapped
                setattr(self.canvas, name, make(orig))
            self._draw_wrapped = True

            # Cursor read-out.
            try:
                self.canvas.mpl_connect("motion_notify_event", self._on_plot_hover)
            except Exception:
                pass

        # Enter anywhere re-runs the current plot.
        try:
            self.bind("<Return>", lambda e: self._safe_plot())
            self.bind("<KP_Enter>", lambda e: self._safe_plot())
        except Exception:
            pass

    def _on_plot_hover(self, event):
        if event.inaxes and event.xdata is not None and event.ydata is not None:
            self.cursor_label.configure(text=f"x={event.xdata:.2f}   y={event.ydata:.2f}")
        else:
            self.cursor_label.configure(text="")

    # ------------------------------------------------------------------ #
    #  Safe plot (busy cursor + error surfacing)                         #
    # ------------------------------------------------------------------ #
    def _mark_core_fields(self):
        """Red-border any malformed core numeric inputs (visual feedback only;
        does not block plotting). Returns a list of problem messages."""
        from .validation import parse_float
        errors = []
        checks = [
            ("m_ref", "Reference mass", {"minimum": 0}),
            ("wheel_radius", "Wheel radius", {"minimum": 0}),
            ("gear_ratio", "Gear ratio", {}),
            ("rear_load_ratio", "Rear load ratio", {}),
            ("ambient_temp", "Ambient temp", {}),
            ("ambient_pressure", "Ambient pressure", {}),
        ]
        for attr, label, kw in checks:
            w = getattr(self, attr, None)
            if w is not None:
                parse_float(w, label, errors=errors, **kw)
        # crr / cd_a are optional (blank allowed).
        for attr, label in (("crr", "Crr"), ("cd_a", "CdA")):
            w = getattr(self, attr, None)
            if w is not None:
                parse_float(w, label, allow_blank=True, errors=errors)
        return errors

    def _safe_plot(self):
        """Re-run the current analysis with a busy cursor, catching errors and
        reporting them in the status bar rather than crashing the UI thread."""
        mode = getattr(self, "plot_mode", None) or self.plot_type.get()
        try:
            problems = self._mark_core_fields()
        except Exception:
            problems = []
        if problems:
            self.set_status("Check inputs: " + "; ".join(problems[:3]), "warn")
        else:
            self.set_status(f"Computing: {mode} \u2026", "info")
        try:
            self.configure(cursor="watch")
            self.update_idletasks()
        except Exception:
            pass
        try:
            if self.plot_type.get() == "Compare Standard Motor Data":
                self.update_compare_std_plot()
            else:
                self.plot_graph()
            if not problems:
                self.set_status(f"{mode}: done.", "ok")
        except Exception as exc:
            self.set_status(f"{mode} failed: {exc}", "error")
            messagebox.showerror("Plot Error", str(exc))
        finally:
            try:
                self.configure(cursor="")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Theme toggle                                                      #
    # ------------------------------------------------------------------ #
    def toggle_theme(self):
        self._dark_mode = bool(self.theme_switch.get()) if hasattr(self, "theme_switch") else (not getattr(self, "_dark_mode", False))
        # NOTE: this toggle styles the *plot only* (post-processing the figure),
        # it deliberately does NOT call ctk.set_appearance_mode(): switching the
        # whole-app appearance left tk frames/canvas stranded as black patches
        # when toggling back to light.
        try:
            if self._dark_mode:
                theme.apply_dark_to_figure(self.figure)
            else:
                theme.apply_matplotlib_theme()      # restore light rc defaults
                theme.apply_light_to_figure(self.figure)  # repaint existing figure
        except Exception:
            pass
        # Re-render the current view so the change is visible immediately.
        try:
            self._safe_plot()
        except Exception:
            try:
                self.canvas.draw()
            except Exception:
                pass
        self.set_status(f"Plot theme: {'dark' if self._dark_mode else 'light'}.", "info")

    # ------------------------------------------------------------------ #
    #  Exports                                                           #
    # ------------------------------------------------------------------ #
    def export_current_figure(self):
        path = filedialog.asksaveasfilename(
            title="Export figure",
            defaultextension=".png",
            initialfile="vmi_figure.png",
            filetypes=[("PNG image", "*.png"), ("SVG vector", "*.svg"),
                       ("PDF document", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.figure.savefig(path, dpi=200, bbox_inches="tight",
                                facecolor=self.figure.get_facecolor())
            self.set_status(f"Figure exported: {os.path.basename(path)}", "ok")
        except Exception as exc:
            self.set_status(f"Figure export failed: {exc}", "error")
            messagebox.showerror("Export error", str(exc))

    def _current_dataframe(self):
        """Pick the most relevant table for the current analysis."""
        mode = getattr(self, "plot_mode", "")
        # Range analysis -> a one-row metrics table.
        if mode == "Range analysis" and getattr(self, "_last_range_metrics", None):
            import pandas as pd
            return pd.DataFrame([self._last_range_metrics]), "range_metrics"
        for attr, label in (("torque_speed_df", "torque_speed"),
                            ("dataframe", "drive_cycle")):
            d = getattr(self, attr, None)
            if d is not None and hasattr(d, "to_csv"):
                return d, label
        return None, None

    def export_current_data(self):
        d, label = self._current_dataframe()
        if d is None:
            self.set_status("No tabular data to export for this view.", "warn")
            messagebox.showwarning("No data",
                                   "There is no table associated with the current view. "
                                   "Plot a torque-speed or drive-cycle analysis first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export data",
            defaultextension=".csv",
            initialfile=f"vmi_{label}.csv",
            filetypes=[("CSV", "*.csv"), ("Excel", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".xlsx"):
                d.to_excel(path, index=False)
            else:
                d.to_csv(path, index=False)
            self.set_status(f"Data exported: {os.path.basename(path)}", "ok")
        except Exception as exc:
            self.set_status(f"Data export failed: {exc}", "error")
            messagebox.showerror("Export error", str(exc))

    # ------------------------------------------------------------------ #
    #  One-click HTML report                                             #
    # ------------------------------------------------------------------ #
    def generate_report(self):
        path = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".html",
            initialfile="vmi_report.html",
            filetypes=[("HTML report", "*.html"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            png = io.BytesIO()
            self.figure.savefig(png, format="png", dpi=160, bbox_inches="tight",
                                facecolor=self.figure.get_facecolor())
            png.seek(0)
            b64 = base64.b64encode(png.read()).decode("ascii")

            mode = getattr(self, "plot_mode", "Analysis")
            summary = ""
            if hasattr(self, "range_results_label"):
                try:
                    summary = self.range_results_label.cget("text")
                except Exception:
                    summary = ""

            rows = ""
            metrics = getattr(self, "_last_range_metrics", None)
            if metrics:
                for k, v in metrics.items():
                    rows += f"<tr><td>{k}</td><td>{v}</td></tr>"

            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            html = _REPORT_TEMPLATE.format(
                mode=mode, stamp=stamp, b64=b64,
                summary=(summary or "(no numeric summary for this view)").replace("\n", "<br>"),
                rows=rows or "<tr><td colspan='2'>No metrics table for this view.</td></tr>",
                primary=COLORS["primary"], dark=COLORS["header_bg"],
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            self.set_status(f"Report saved: {os.path.basename(path)}", "ok")
            try:
                webbrowser.open("file://" + os.path.abspath(path))
            except Exception:
                pass
        except Exception as exc:
            self.set_status(f"Report failed: {exc}", "error")
            messagebox.showerror("Report error", str(exc))

    # ------------------------------------------------------------------ #
    #  Scenario save / load (all entry + combobox + switch values)       #
    # ------------------------------------------------------------------ #
    def _scenario_widgets(self):
        """Yield (name, widget, kind) for every savable input widget."""
        for name, w in vars(self).items():
            if isinstance(w, ctk.CTkEntry):
                yield name, w, "entry"
            elif isinstance(w, ctk.CTkComboBox):
                yield name, w, "combo"
            elif isinstance(w, ctk.CTkSegmentedButton):
                yield name, w, "segment"
            elif isinstance(w, (ctk.CTkSwitch, ctk.CTkCheckBox)):
                yield name, w, "toggle"

    # ---- loaded-data persistence (DataFrames / maps + their tick marks) ---- #
    @staticmethod
    def _to_py_scalar(x):
        return x.item() if hasattr(x, "item") else x

    def _enc_obj(self, v):
        """JSON-safe encoding for the data objects a scenario can carry."""
        import numpy as np
        import pandas as pd
        if v is None:
            return None
        if isinstance(v, pd.DataFrame):
            safe = v.where(pd.notnull(v), None)
            return {
                "__k": "df",
                "index": [self._to_py_scalar(i) for i in v.index],
                "columns": [self._to_py_scalar(c) for c in v.columns],
                "data": safe.values.tolist(),
            }
        if isinstance(v, np.ndarray):
            return {"__k": "nd", "data": np.asarray(v, dtype=float).tolist()}
        if isinstance(v, (np.floating, np.integer)):
            return self._to_py_scalar(v)
        if isinstance(v, dict):
            return {"__k": "dict", "items": [[k, self._enc_obj(val)] for k, val in v.items()]}
        if isinstance(v, (list, tuple)):
            return [self._enc_obj(x) for x in v]
        # Final fallback: keep JSON-native scalars; drop anything else (e.g. the
        # cached scipy spline stored in engine_efficiency_curves, which the
        # engine code rebuilds on demand from the raw rpm/eff arrays).
        if isinstance(v, (str, int, float, bool)):
            return v
        return None

    def _dec_obj(self, v):
        import numpy as np
        import pandas as pd
        if isinstance(v, dict):
            k = v.get("__k")
            if k == "df":
                return pd.DataFrame(v["data"], index=v["index"], columns=v["columns"])
            if k == "nd":
                return np.asarray(v["data"], dtype=float)
            if k == "dict":
                return {key: self._dec_obj(val) for key, val in v["items"]}
            return v
        if isinstance(v, list):
            return [self._dec_obj(x) for x in v]
        return v

    @staticmethod
    def _is_present(v):
        import pandas as pd
        if v is None:
            return False
        if isinstance(v, pd.DataFrame):
            return not v.empty
        if isinstance(v, dict):
            return len(v) > 0
        return True

    def _dataset_slots(self):
        """Every loadable dataset: which attributes hold it, and the indicator /
        buttons that show it's loaded."""
        return [
            dict(name="drive_cycle", attrs=["dataframe"], primary="dataframe",
                 indicator="drive_cycle_indicator",
                 buttons=["drive_cycle_delete_button", "plot_drive_cycle_button", "plot_torque_speed_button"]),
            dict(name="motor_data", attrs=["motor_dataframe", "motor_curve_source"], primary="motor_dataframe",
                 indicator="motor_data_indicator", buttons=["motor_data_delete_button"]),
            dict(name="engine_data", attrs=["engine_dataframe"], primary="engine_dataframe",
                 indicator="engine_data_indicator", buttons=["engine_data_delete_button"]),
            dict(name="engine_eff", attrs=["engine_efficiency_curves"], primary="engine_efficiency_curves",
                 indicator="engine_eff_indicator", buttons=["engine_eff_delete_button"]),
            dict(name="eff1", attrs=["efficiency_data_1", "eff1_map_torques", "eff1_map_rpms", "eff1_map_matrix"],
                 primary="efficiency_data_1", indicator="eff1_indicator", buttons=["eff1_delete_button"]),
            dict(name="eff2", attrs=["efficiency_data_2", "eff2_map_torques", "eff2_map_rpms", "eff2_map_matrix"],
                 primary="efficiency_data_2", indicator="eff2_indicator", buttons=["eff2_delete_button"]),
            dict(name="range_motor",
                 attrs=["range_motor_efficiency_map", "range_motor_eff_map_torques", "range_motor_eff_map_rpms"],
                 primary="range_motor_efficiency_map", indicator="range_motor_eff_indicator",
                 buttons=["range_motor_eff_delete_button"]),
            dict(name="range_controller",
                 attrs=["range_controller_efficiency_map", "range_controller_eff_map_torques", "range_controller_eff_map_rpms"],
                 primary="range_controller_efficiency_map", indicator="range_controller_eff_indicator",
                 buttons=["range_controller_eff_delete_button"]),
        ]

    def _set_indicator(self, indicator_name, present, button_names=()):
        ind = getattr(self, indicator_name, None)
        if ind is not None:
            try:
                ind.configure(text="✅" if present else "❌",
                              text_color=COLORS["success"] if present else COLORS["warning"])
            except Exception:
                pass
        for b in button_names:
            w = getattr(self, b, None)
            if w is not None:
                try:
                    w.configure(state="normal" if present else "disabled")
                except Exception:
                    pass

    def _collect_scenario_data(self):
        """Snapshot every scenario widget plus loaded datasets into a dict.
        Shared by Save Scenario and the session autosave."""
        data = {}
        for name, w, kind in self._scenario_widgets():
            try:
                if kind == "entry":
                    data[name] = w.get()
                elif kind in ("combo", "segment"):
                    data[name] = w.get()
                elif kind == "toggle":
                    data[name] = bool(w.get())
            except Exception:
                pass

        # Also persist any loaded datasets so they come back next time.
        datasets = {}
        for slot in self._dataset_slots():
            primary_val = getattr(self, slot["primary"], None)
            if not self._is_present(primary_val):
                continue
            blob = {}
            for attr in slot["attrs"]:
                blob[attr] = self._enc_obj(getattr(self, attr, None))
            datasets[slot["name"]] = blob
        if datasets:
            data["__datasets__"] = datasets
        return data

    def save_scenario(self):
        data = self._collect_scenario_data()

        path = filedialog.asksaveasfilename(
            title="Save scenario",
            defaultextension=".json",
            initialfile="vmi_scenario.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.set_status(f"Scenario saved ({len(data)} fields).", "ok")
        except Exception as exc:
            self.set_status(f"Save failed: {exc}", "error")
            messagebox.showerror("Save error", str(exc))

    def load_scenario(self):
        path = filedialog.askopenfilename(
            title="Load scenario",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self.set_status(f"Load failed: {exc}", "error")
            messagebox.showerror("Load error", str(exc))
            return

        applied, restored = self._apply_scenario_data(data)
        self.set_status(
            f"Scenario loaded ({applied} fields, {restored} datasets).", "ok")

    def _apply_scenario_data(self, data):
        """Apply a scenario dict to the UI (fields + datasets + derived state).
        Shared by Load Scenario and the session restore. Returns
        (fields_applied, datasets_restored)."""
        applied = 0
        for name, w, kind in self._scenario_widgets():
            if name not in data:
                continue
            val = data[name]
            try:
                if kind == "entry":
                    w.delete(0, "end")
                    w.insert(0, str(val))
                elif kind in ("combo", "segment"):
                    w.set(val)
                elif kind == "toggle":
                    (w.select if val else w.deselect)()
                applied += 1
            except Exception:
                pass

        # Restore any saved datasets and tick their indicators automatically.
        datasets = data.get("__datasets__", {}) or {}
        restored = 0
        for slot in self._dataset_slots():
            blob = datasets.get(slot["name"])
            if blob is None:
                continue
            for attr in slot["attrs"]:
                if attr in blob:
                    try:
                        setattr(self, attr, self._dec_obj(blob[attr]))
                    except Exception:
                        pass
            present = self._is_present(getattr(self, slot["primary"], None))
            self._set_indicator(slot["indicator"], present, slot["buttons"])
            if present:
                restored += 1

        # Refresh derived UI that depends on the restored data.
        try:
            if self._is_present(getattr(self, "dataframe", None)) and hasattr(self, "update_drive_cycle_properties"):
                self.update_drive_cycle_properties()
        except Exception:
            pass
        # Lock/unlock manual motor inputs to match the restored motor curve.
        try:
            if hasattr(self, "set_motor_params_enabled"):
                self.set_motor_params_enabled(
                    not self._is_present(getattr(self, "motor_dataframe", None))
                )
        except Exception:
            pass
        if hasattr(self, "_sync_shared_efficiency_ticks"):
            try:
                self._sync_shared_efficiency_ticks()
            except Exception:
                pass

        # Re-plot the current view so everything reflects the loaded scenario.
        try:
            self.update_plot()
        except Exception:
            pass

        return applied, restored

    # ------------------------------------------------------------------ #
    #  Session autosave / restore                                         #
    # ------------------------------------------------------------------ #
    SESSION_FILE = "vmi_last_session.json"

    def autosave_session(self):
        """Silently snapshot the whole UI state to SESSION_FILE (called on
        window close, so nothing is lost between runs)."""
        try:
            data = self._collect_scenario_data()
            with open(self.SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def restore_last_session(self):
        """Restore SESSION_FILE if present. Returns True if something was
        applied. Defensive per-field, so a stale file can't break startup."""
        try:
            with open(self.SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        try:
            applied, restored = self._apply_scenario_data(data)
        except Exception:
            return False
        self.set_status(
            f"Restored last session ({applied} fields, {restored} datasets).", "ok")
        return True

    def _on_app_close(self):
        self.autosave_session()
        try:
            self.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Loss-breakdown waterfall (Range analysis view)                    #
    # ------------------------------------------------------------------ #
    def _draw_loss_waterfall(self, ax, metrics):
        """Waterfall from the energy sinks up to the net battery draw (Wh/km)."""
        terms = [
            ("Aero", metrics.get("aerodynamic_loss_per_km", 0.0)),
            ("Rolling", metrics.get("rolling_loss_per_km", 0.0)),
            ("Grade", metrics.get("grade_loss_per_km", 0.0)),
            ("Inertia", metrics.get("inertia_loss_motoring_per_km", 0.0)),
            ("Trans.", metrics.get("transmission_loss_per_km", 0.0)),
            ("Motor", metrics.get("motor_loss_per_km", 0.0)),
            ("Controller", metrics.get("controller_loss_per_km", 0.0)),
            ("Aux", metrics.get("aux_loss_total_per_km", 0.0)),
        ]
        regen = metrics.get("regen_energy_per_km", 0.0)
        net = metrics.get("net_energy_loss_per_km", sum(v for _, v in terms) - regen)

        labels = [t[0] for t in terms] + ["Regen", "Net"]
        running = 0.0
        for i, (lab, val) in enumerate(terms):
            ax.bar(i, val, bottom=running, color=COLORS["primary"], edgecolor="white")
            ax.text(i, running + val + 0.2, f"{val:.1f}", ha="center", va="bottom", fontsize=9)
            running += val
        # Regen pulls the total back down.
        ax.bar(len(terms), -regen, bottom=running, color=COLORS["success"], edgecolor="white")
        ax.text(len(terms), running - regen - 0.6, f"-{regen:.1f}", ha="center", va="top", fontsize=9)
        # Net result bar from zero.
        ax.bar(len(terms) + 1, net, color=COLORS["warning"], edgecolor="white")
        ax.text(len(terms) + 1, net + 0.2, f"{net:.1f}", ha="center", va="bottom", fontsize=9, weight="bold")

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=10)
        ax.set_ylabel("Energy (Wh/km)", fontsize=12, weight="bold")
        ax.set_title("Energy Loss Breakdown (per km)", fontsize=15, weight="bold")
        ax.grid(True, axis="y", linestyle="--", alpha=0.5)


# Font family pulled once (avoids importing FONTS name clashes in the methods).
COLORS_FONT = "Segoe UI"

_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Vehicle - Motor Integration Report</title>
<style>
 body{{font-family:'Segoe UI',Arial,sans-serif;margin:0;background:#eef1f7;color:#0f172a}}
 header{{background:{dark};color:#fff;padding:22px 32px}}
 header h1{{margin:0;font-size:22px}} header p{{margin:4px 0 0;color:#a5b4fc;font-size:13px}}
 main{{max-width:980px;margin:24px auto;padding:0 24px}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:20px;margin-bottom:20px}}
 .card h2{{margin:0 0 12px;font-size:15px;text-transform:uppercase;letter-spacing:.04em;color:{primary}}}
 img{{max-width:100%;border-radius:10px;border:1px solid #e2e8f0}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 td{{padding:7px 10px;border-bottom:1px solid #eef1f7}} td:first-child{{color:#475569}}
 .summary{{font-size:14px;line-height:1.6}}
 footer{{text-align:center;color:#94a3b8;font-size:12px;padding:18px}}
</style></head><body>
<header><h1>Vehicle &harr; Motor Integration &mdash; {mode}</h1><p>Generated {stamp}</p></header>
<main>
 <div class="card"><h2>Figure</h2><img src="data:image/png;base64,{b64}" alt="figure"></div>
 <div class="card"><h2>Summary</h2><div class="summary">{summary}</div></div>
 <div class="card"><h2>Metrics</h2><table>{rows}</table></div>
</main>
<footer>Produced by the Vehicle-Motor Integration Suite</footer>
</body></html>"""
