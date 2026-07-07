"""
Per-analysis graph settings.

A single, schema-driven framework so every analysis gets its own collapsible
"Graph Settings" panel and so adding a new control later is one line in SCHEMA
plus one `self.gs_*()` read in the matching plot method.

Design
------
* SCHEMA maps an analysis-type name (the same strings used in
  ``self.plot_type`` / ``self.plot_mode``) to a list of field specs.
* Each field spec is a dict: ``{type, key, label, default, [choices]}``.
* Values live in ``self._gs_values[(analysis, key)]`` and persist across panel
  rebuilds and analysis switches.
* Plot methods read settings with ``self.gs_*()`` helpers, always passing the
  current hard-coded value as the default -- so an untouched setting reproduces
  the original look exactly.

Nothing here changes any calculation; it only restyles artists.
"""

import customtkinter as ctk
from matplotlib.ticker import MultipleLocator

from .theme import COLORS, FONTS

# Matplotlib line-style names <-> human labels shown in the combo.
STYLE_TO_MPL = {"solid": "-", "dashed": "--", "dotted": ":", "dashdot": "-."}
MPL_TO_STYLE = {v: k for k, v in STYLE_TO_MPL.items()}

COLOR_CHOICES = [
    "Auto", "black", "red", "darkorange", "blue", "green",
    "purple", "gray", "teal", "magenta", "brown", "gold",
]
STYLE_CHOICES = ["solid", "dashed", "dotted", "dashdot"]
WIDTH_CHOICES = ["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "4.0"]
CMAP_CHOICES = ["viridis", "plasma", "inferno", "magma", "cividis",
                "jet", "turbo", "coolwarm", "RdYlGn", "Spectral"]
# Matplotlib legend `loc` strings, in the order most people reach for.
LEGEND_CHOICES = [
    "best", "upper right", "upper left", "lower left", "lower right",
    "right", "center left", "center right", "lower center",
    "upper center", "center",
]


def _f(name, ftype, label, default, choices=None):
    spec = {"type": ftype, "key": name, "label": label, "default": default}
    if choices is not None:
        spec["choices"] = choices
    return spec


def _line(prefix, label, color, style, width):
    """Convenience: the (color, style, width) triple for one named line."""
    return [
        _f(f"{prefix}_color", "color", f"{label} color", color, COLOR_CHOICES),
        _f(f"{prefix}_style", "style", f"{label} style", style, STYLE_CHOICES),
        _f(f"{prefix}_width", "width", f"{label} width", width, WIDTH_CHOICES),
    ]


ALPHA_CHOICES = ["0.2", "0.4", "0.6", "0.7", "0.8", "1.0"]

# Grid is controlled per-axis (X gridlines / Y gridlines) plus shared
# style/opacity, instead of a single on/off switch.
_GRID_FIELDS = [
    _f("grid_x", "bool", "Grid: vertical (X) lines", True),
    _f("grid_y", "bool", "Grid: horizontal (Y) lines", True),
    # 0 = automatic ticks. Any positive value forces gridlines/ticks every N
    # units on that axis so the user can read off points at a chosen interval.
    _f("grid_x_step", "float", "Grid X spacing (0=auto)", 0),
    _f("grid_y_step", "float", "Grid Y spacing (0=auto)", 0),
    _f("grid_style", "style", "Grid line style", "dashed", STYLE_CHOICES),
    _f("grid_alpha", "choice", "Grid opacity", "0.7", ALPHA_CHOICES),
]

_UNIVERSAL_LINE = _GRID_FIELDS + [
    _f("show_legend", "bool", "Show legend", True),
    _f("legend_loc", "choice", "Legend position", "best", LEGEND_CHOICES),
    _f("title_size", "int", "Title font size", 16),
    _f("label_size", "int", "Axis label size", 14),
]

SCHEMA = {
    # Torque and Force share the "Powertrain Sizing" analysis but keep SEPARATE
    # settings (own key namespace), so e.g. grid spacing set for torque does not
    # bleed into force -- force has a much larger value range and wants a coarser
    # grid. The Output selector picks which of these two the panel edits.
    "Powertrain Sizing":
        _line("peak", "Peak curve", "black", "dashed", "2.0")
        + _line("cont", "Continuous curve", "gray", "dotted", "2.0")
        + _line("grad", "Gradient lines", "Auto", "dashed", "1.5")
        + _UNIVERSAL_LINE,
    "Powertrain Sizing::Force":
        _line("peak", "Peak curve", "black", "dashed", "2.0")
        + _line("cont", "Continuous curve", "gray", "dotted", "2.0")
        + _line("grad", "Gradient lines", "Auto", "solid", "2.0")
        + _UNIVERSAL_LINE,
    "Acceleration":
        _line("speed", "Speed curve", "black", "dashed", "2.0")
        + _UNIVERSAL_LINE,
    "Engine analysis":
        [_f("line_width", "width", "Line width", "2.0", WIDTH_CHOICES)]
        + _UNIVERSAL_LINE,
    "Parametric Study":
        [_f("cmap", "choice", "Colormap", "viridis", CMAP_CHOICES),
         _f("fill_levels", "int", "Filled contour levels", 20),
         _f("line_levels", "int", "Contour lines", 10)]
        + _UNIVERSAL_LINE,
    "Drive Cycle": [
        _f("hm_cmap", "choice", "Heatmap colormap", "YlOrRd", CMAP_CHOICES + ["YlOrRd", "YlGnBu", "hot"]),
        _f("hm_weight", "choice", "Heatmap weight", "Point Count", ["Point Count", "Energy (Wh)"]),
        _f("hm_alpha", "choice", "Heatmap opacity", "0.85", ALPHA_CHOICES),
        _f("hm_show_scatter", "bool", "Overlay scatter points", True),
        _f("hm_top_pct", "float", "Highlight top % (count/energy)", 70),
        _f("hm_show_top", "bool", "Show top-bins table", True),
    ] + _UNIVERSAL_LINE,
    # MTPA/MTPV draws up to four panels; grid/legend/font settings are applied
    # per-panel by _mtpa_apply_gs (apply_graph_style only touches self.ax).
    # Defaults mirror the original hard-coded look (title 13, grid alpha 0.5).
    "MTPA / MTPV (PMSM)":
        _line("env", "Torque envelope", "black", "solid", "2.2")
        + _line("power", "Power curve", "Auto", "solid", "2.2")
        + _line("id", "id curve", "Auto", "solid", "2.0")
        + _line("iq", "iq curve", "Auto", "solid", "2.0")
        + _line("is", "|i_s| curve", "gray", "dashed", "1.5")
        + [
            _f("region_shade", "bool", "Shade MTPA/FW/MTPV regions", True),
            _f("region_alpha", "float", "Region shade opacity", 0.10),
            _f("traj_size", "int", "Trajectory marker size", 4),
            _f("grid_x", "bool", "Grid: vertical (X) lines", True),
            _f("grid_y", "bool", "Grid: horizontal (Y) lines", True),
            _f("grid_x_step", "float", "Grid X spacing (0=auto)", 0),
            _f("grid_y_step", "float", "Grid Y spacing (0=auto)", 0),
            _f("grid_style", "style", "Grid line style", "dashed", STYLE_CHOICES),
            _f("grid_alpha", "choice", "Grid opacity", "0.5",
               ["0.2", "0.4", "0.5", "0.6", "0.7", "0.8", "1.0"]),
            _f("show_legend", "bool", "Show legend", True),
            _f("legend_loc", "choice", "Legend position", "Auto", ["Auto"] + LEGEND_CHOICES),
            _f("title_size", "int", "Title font size", 13),
            _f("label_size", "int", "Axis label size", 11),
        ],
    # Mechanical Design draws a different curve set per Design Check, so per-
    # line pickers aren't practical -- a shared line width + the universal
    # grid/legend/font block (defaults mirror the hard-coded look: title 14).
    "Mechanical Design (Motor)":
        [_f("line_width", "width", "Line width", "2.0", WIDTH_CHOICES)]
        + _GRID_FIELDS + [
            _f("show_legend", "bool", "Show legend", True),
            _f("legend_loc", "choice", "Legend position", "best", LEGEND_CHOICES),
            _f("title_size", "int", "Title font size", 14),
            _f("label_size", "int", "Axis label size", 12),
        ],
    # Motor BOM: the sankey view is axis-off (grid/legend don't apply there),
    # but Pareto and Group Split are ordinary single-axis bar plots and take
    # the universal grid/legend/font block.
    "Motor BOM (Cost & Weight)":
        _GRID_FIELDS + [
            _f("show_legend", "bool", "Show legend", True),
            _f("legend_loc", "choice", "Legend position", "lower right", LEGEND_CHOICES),
            _f("title_size", "int", "Title font size", 14),
            _f("label_size", "int", "Axis label size", 12),
        ],
    # Range analysis is multi-panel; settings are applied per panel by
    # _range_apply_gs (range_analysis.py), and ONLY the settings the user
    # has actually touched are applied -- the stock panels deliberately use
    # different grid alphas / font sizes, so a blanket apply would change
    # the default look. The eff-map colormap/levels/opacity are read inside
    # _plot_range_efficiency_map_panel with the original values as defaults.
    "Range analysis": [
        _f("line_width", "width", "Line width", "1.0", WIDTH_CHOICES),
        _f("cmap", "choice", "Eff-map colormap", "RdYlGn", ["RdYlGn"] + CMAP_CHOICES),
        _f("fill_levels", "int", "Eff-map filled levels", 40),
        _f("line_levels", "int", "Eff-map contour lines", 10),
        _f("map_alpha", "choice", "Eff-map opacity", "0.75",
           ["0.4", "0.6", "0.75", "0.9", "1.0"]),
        # Off by default: fills the Motor/Controller map's own NaN gaps with
        # the nearest known value and resamples onto a dense 200x200 grid
        # before masking, so the M Eff / C Eff panels render as a smooth
        # field instead of a blocky one where a handful of missing datasheet
        # cells erase whole contourf quads near the capability curve.
        _f("extrapolate_gaps", "bool", "Extrapolate to envelope (smooth map)", False),
    ] + _GRID_FIELDS + [
        _f("show_legend", "bool", "Show legend", True),
        _f("legend_loc", "choice", "Legend position", "best", LEGEND_CHOICES),
        _f("title_size", "int", "Title font size", 12),
        _f("label_size", "int", "Axis label size", 10),
    ],
    "Drive Cycle Efficiency": [
        _f("cmap", "choice", "Colormap", "viridis", CMAP_CHOICES),
        _f("fill_levels", "int", "Filled contour levels", 50),
        _f("line_levels", "int", "Contour lines", 20),
        # Off by default: fills data gaps *inside* the motor's capability
        # envelope with the nearest known map value, purely cosmetic (the
        # capability mask still blanks everything outside the envelope
        # regardless of this toggle -- see _extrapolate_eff_gaps).
        _f("extrapolate_gaps", "bool", "Extrapolate to envelope (fill data gaps)", False),
        # Drive-cycle points overlay (shown when 'Show Drive Cycle Data' is on).
        _f("overlay_style", "choice", "Overlay style", "Scatter", ["Scatter", "Heatmap", "Both"]),
        _f("overlay_weight", "choice", "Overlay heatmap weight", "Point Count", ["Point Count", "Energy (Wh)"]),
        _f("overlay_cmap", "choice", "Overlay heatmap colormap", "hot", CMAP_CHOICES + ["hot", "YlOrRd", "YlGnBu"]),
        _f("overlay_alpha", "choice", "Overlay heatmap opacity", "0.6", ALPHA_CHOICES),
        _f("overlay_gridsize", "int", "Overlay heatmap gridsize", 30),
    ] + _GRID_FIELDS + [
        _f("title_size", "int", "Title font size", 24),
        _f("label_size", "int", "Axis label size", 18),
    ],
    # Compare Standard Motor Data draws a variable number of lines (one per
    # selected motor), so per-line color pickers aren't practical -- a shared
    # line width plus the universal grid/legend/font controls is enough to
    # match the level of control the other line-plot analyses get. Kept as
    # four separate namespaces (one per radio button) so e.g. widening the
    # torque comparison's lines doesn't also widen the acceleration plot's.
    "Compare Standard Motor Data::Torque":
        [_f("line_width", "width", "Line width", "2.0", WIDTH_CHOICES)] + _UNIVERSAL_LINE,
    "Compare Standard Motor Data::Force":
        [_f("line_width", "width", "Line width", "2.0", WIDTH_CHOICES)] + _UNIVERSAL_LINE,
    "Compare Standard Motor Data::Acceleration":
        [_f("line_width", "width", "Line width", "2.0", WIDTH_CHOICES)] + _UNIVERSAL_LINE,
    "Compare Standard Motor Data::Efficiency": [
        _f("cmap", "choice", "Diff colormap", "RdBu", ["RdBu", "RdYlBu", "coolwarm", "Spectral"] + CMAP_CHOICES),
        _f("fill_levels", "int", "Filled contour levels", 30),
        _f("line_levels", "int", "Contour lines", 10),
    ] + _GRID_FIELDS + [
        _f("title_size", "int", "Title font size", 16),
        _f("label_size", "int", "Axis label size", 14),
    ],
}


class GraphSettingsMixin:
    """Builds the Graph Settings panel and exposes gs_* readers to plot code."""

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                         #
    # ------------------------------------------------------------------ #
    def gs_init(self):
        self._gs_values = {}      # (analysis, key) -> value
        self._gs_body = None      # frame rebuilt per analysis
        self._gs_current = None   # analysis currently shown in the panel

    def attach_graph_settings_body(self, section_frame):
        """Create the persistent content frame inside the Graph Settings card."""
        self._gs_body = ctk.CTkFrame(section_frame, fg_color="transparent")
        self._gs_body.pack(fill="x", padx=4, pady=(0, 6))

    # ------------------------------------------------------------------ #
    #  Value access (used by plot methods)                               #
    # ------------------------------------------------------------------ #
    def _gs_analysis(self):
        """Effective analysis name for graph settings.

        Torque and Force share the "Powertrain Sizing" analysis but keep separate
        settings namespaces; the Output selector decides which one is read/edited.
        """
        mode = getattr(self, "plot_mode", None)
        if (mode == "Powertrain Sizing"
                and getattr(self, "output_combo", None) is not None
                and self.output_combo.get() == "Force"):
            return "Powertrain Sizing::Force"
        if mode == "Compare Standard Motor Data" and getattr(self, "compare_std_plot_var", None) is not None:
            suffix = {
                "torque": "Torque", "force": "Force",
                "acceleration": "Acceleration", "efficiency": "Efficiency",
            }.get(self.compare_std_plot_var.get(), "Torque")
            return f"Compare Standard Motor Data::{suffix}"
        return mode

    def gs(self, key, default=None):
        return self._gs_values.get((self._gs_analysis(), key), default)

    def gs_bool(self, key, default=True):
        return bool(self.gs(key, default))

    def gs_int(self, key, default=0):
        try:
            return int(round(float(self.gs(key, default))))
        except Exception:
            return default

    def gs_float(self, key, default=1.0):
        try:
            return float(self.gs(key, default))
        except Exception:
            return default

    def gs_str(self, key, default=""):
        val = self.gs(key, default)
        return str(val) if val is not None else default

    def gs_linestyle(self, key, default_mpl="-"):
        name = self.gs(key, MPL_TO_STYLE.get(default_mpl, "solid"))
        return STYLE_TO_MPL.get(str(name), default_mpl)

    def gs_color(self, key, default):
        """Return a color, or `default` when the user left it on 'Auto'."""
        val = self.gs(key, default)
        if val in (None, "Auto", "auto", ""):
            return default
        return val

    # ------------------------------------------------------------------ #
    #  Panel construction                                                #
    # ------------------------------------------------------------------ #
    def populate_graph_settings(self, analysis):
        """Rebuild the controls inside the Graph Settings card for `analysis`."""
        body = getattr(self, "_gs_body", None)
        if body is None:
            return
        self._gs_current = analysis
        for child in body.winfo_children():
            child.destroy()

        specs = SCHEMA.get(analysis)
        if not specs:
            ctk.CTkLabel(
                body, text="No graph settings for this view yet.",
                font=(FONTS["family"], 11), text_color=COLORS["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=12, pady=6)
            return

        for spec in specs:
            self._build_gs_field(body, analysis, spec)

    def _gs_set(self, analysis, key, value):
        self._gs_values[(analysis, key)] = value
        self._gs_replot()

    def _gs_replot(self):
        """Re-render whatever is currently on screen so the change shows."""
        mode = getattr(self, "plot_mode", None)
        if mode == "Drive Cycle Efficiency" and getattr(self, "_last_eff_plot", None):
            try:
                self._last_eff_plot()
                return
            except Exception:
                pass
        if mode == "Drive Cycle" and getattr(self, "_last_dc_plot", None):
            try:
                self._last_dc_plot()
                return
            except Exception:
                pass
        if hasattr(self, "_safe_plot"):
            self._safe_plot()

    def _build_gs_field(self, parent, analysis, spec):
        key, ftype, label = spec["key"], spec["type"], spec["label"]
        current = self._gs_values.get((analysis, key), spec["default"])

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=3)
        ctk.CTkLabel(
            row, text=label, font=(FONTS["family"], 12),
            text_color=COLORS["text_muted"], anchor="w", width=150,
        ).pack(side="left", padx=(2, 8))

        if ftype == "bool":
            var = ctk.BooleanVar(value=bool(current))

            def _toggle(k=key, v=var):
                self._gs_set(analysis, k, bool(v.get()))
            ctk.CTkSwitch(row, text="", variable=var, command=_toggle,
                          progress_color=COLORS["primary"]).pack(side="right")

        elif ftype in ("color", "style", "width", "choice"):
            values = spec.get("choices") or (
                COLOR_CHOICES if ftype == "color"
                else STYLE_CHOICES if ftype == "style"
                else WIDTH_CHOICES
            )

            def _pick(value, k=key, t=ftype):
                self._gs_set(analysis, k, float(value) if t == "width" else value)
            combo = ctk.CTkComboBox(row, values=[str(v) for v in values],
                                    width=120, command=_pick)
            combo.set(str(current))
            combo.pack(side="right")

        elif ftype == "int":
            entry = ctk.CTkEntry(row, width=80, font=(FONTS["family"], 12))
            entry.insert(0, str(int(current)))
            entry.pack(side="right")

            def _commit(_e=None, k=key, w=entry):
                raw = w.get().strip()
                try:
                    self._gs_set(analysis, k, int(round(float(raw))))
                except Exception:
                    pass
            entry.bind("<Return>", _commit)
            entry.bind("<FocusOut>", _commit)

        elif ftype == "float":
            entry = ctk.CTkEntry(row, width=80, font=(FONTS["family"], 12))
            entry.insert(0, str(current))
            entry.pack(side="right")

            def _commit(_e=None, k=key, w=entry):
                raw = w.get().strip()
                try:
                    self._gs_set(analysis, k, float(raw))
                except Exception:
                    pass
            entry.bind("<Return>", _commit)
            entry.bind("<FocusOut>", _commit)

    # ------------------------------------------------------------------ #
    #  Universal axis restyle, applied after a single-axis plot          #
    # ------------------------------------------------------------------ #
    def apply_graph_style(self):
        """Apply grid / legend / title-size / label-size from the current
        analysis's settings to self.ax. Safe to call for any single-axis plot;
        absent settings keep the plot method's own choices."""
        ax = getattr(self, "ax", None)
        if ax is None:
            return
        try:
            grid_x = self.gs_bool("grid_x", True)
            grid_y = self.gs_bool("grid_y", True)
            grid_ls = self.gs_linestyle("grid_style", "--")
            grid_alpha = self.gs_float("grid_alpha", 0.7)

            # Optional fixed tick spacing (unit division) per axis. 0 = auto.
            # Guard against a tiny step over a wide range creating a runaway
            # number of ticks (which would freeze the UI): cap at ~1000.
            gx_step = self.gs_float("grid_x_step", 0.0)
            gy_step = self.gs_float("grid_y_step", 0.0)
            if gx_step and gx_step > 0:
                lo, hi = ax.get_xlim()
                if 0 < (hi - lo) / gx_step <= 1000:
                    ax.xaxis.set_major_locator(MultipleLocator(gx_step))
            if gy_step and gy_step > 0:
                lo, hi = ax.get_ylim()
                if 0 < (hi - lo) / gy_step <= 1000:
                    ax.yaxis.set_major_locator(MultipleLocator(gy_step))

            # The "seaborn-v0_8-whitegrid" base style (setup_plot_style) sets
            # axes.axisbelow=True globally, which puts gridlines below EVERY
            # artist including filled contours/heatmaps -- so on a colormap
            # (efficiency maps, Parametric Study, Drive Cycle heatmap) the
            # grid was invisible under the fill. 'line' restores matplotlib's
            # normal default: above patches/images, below plotted lines.
            ax.set_axisbelow('line')
            ax.grid(False)  # reset, then enable only the requested axes
            if grid_x:
                ax.grid(True, axis="x", linestyle=grid_ls, alpha=grid_alpha)
            if grid_y:
                ax.grid(True, axis="y", linestyle=grid_ls, alpha=grid_alpha)
        except Exception:
            pass

        try:
            show_legend = self.gs_bool("show_legend", True)
            legend_loc = self.gs_str("legend_loc", "best") or "best"
            handles, _labels = ax.get_legend_handles_labels()
            existing = ax.get_legend()
            if show_legend and handles:
                ax.legend(loc=legend_loc)
            elif not show_legend and existing is not None:
                existing.remove()
        except Exception:
            pass

        try:
            if ax.get_title():
                ax.title.set_fontsize(self.gs_int("title_size", 16))
            ax.xaxis.label.set_size(self.gs_int("label_size", 14))
            ax.yaxis.label.set_size(self.gs_int("label_size", 14))
        except Exception:
            pass
