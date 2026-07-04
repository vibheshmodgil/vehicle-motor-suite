"""
Central theme for the suite.

Everything visual lives here. The original program scattered colour literals
through the code; now a single modern palette drives the whole UI. The dict
keys the original widgets relied on (primary, secondary, accent, background,
text, success, warning, header_bg, section_bg) are all still present, so the
verbatim widget code simply picks up the new look.

Nothing here changes inputs, options or calculations.
"""

import customtkinter as ctk
import matplotlib as mpl

# --------------------------------------------------------------------------- #
#  Modern palette (refined indigo / slate, light surface)                     #
# --------------------------------------------------------------------------- #
COLORS = {
    # --- brand / accents ---
    "primary":        "#4f46e5",   # indigo-600
    "primary_hover":  "#4338ca",   # indigo-700
    "secondary":      "#0ea5e9",   # sky-500
    "accent":         "#6366f1",   # indigo-500
    "success":        "#16a34a",   # green-600
    "warning":        "#ea580c",   # orange-600
    "danger":         "#dc2626",   # red-600 (invalid input / destructive)

    # --- surfaces ---
    "background":     "#eef1f7",   # app background (soft cool gray)
    "card":           "#ffffff",   # section cards
    "section_bg":     "#f8fafc",   # slate-50 (tables / light panels)
    "input_bg":       "#ffffff",   # entry fields
    "border":         "#e2e8f0",   # slate-200 hairline borders

    # --- text ---
    "text":           "#0f172a",   # slate-900
    "text_muted":     "#475569",   # slate-600

    # --- header / app bar ---
    "header_bg":      "#1e1b4b",   # indigo-950 (dark app bar)
    "header_bg_soft": "#eef2ff",   # indigo-50 (analysis selector strip)
    "on_header":      "#f8fafc",   # text on dark bar
    "on_header_muted": "#a5b4fc",  # indigo-300 subtitle on dark bar

    # --- plot canvas ---
    "plot_bg":        "#ffffff",
    "plot_axes_bg":   "#ffffff",
}

FONTS = {
    "family":          "Segoe UI",
    "family_semibold": "Segoe UI Semibold",
    "mono":            "Consolas",
}


def _nudge_ctk_widget_colors():
    """Recolour CustomTkinter's default widget theme toward our palette so the
    many default-styled buttons / combos / switches look cohesive."""
    try:
        theme = ctk.ThemeManager.theme
    except Exception:
        return

    def dual(c):
        return [c, c]

    pairs = {
        "CTkButton": {
            "fg_color": dual(COLORS["primary"]),
            "hover_color": dual(COLORS["primary_hover"]),
            "text_color": dual("#ffffff"),
            "border_color": dual(COLORS["primary"]),
        },
        "CTkComboBox": {
            "border_color": dual(COLORS["border"]),
            "button_color": dual(COLORS["primary"]),
            "button_hover_color": dual(COLORS["primary_hover"]),
            "fg_color": dual(COLORS["input_bg"]),
            "text_color": dual(COLORS["text"]),
        },
        "CTkEntry": {
            "border_color": dual(COLORS["border"]),
            "fg_color": dual(COLORS["input_bg"]),
            "text_color": dual(COLORS["text"]),
        },
        "CTkSwitch": {
            "progress_color": dual(COLORS["primary"]),
        },
        "CTkSegmentedButton": {
            "selected_color": dual(COLORS["primary"]),
            "selected_hover_color": dual(COLORS["primary_hover"]),
            "unselected_color": dual(COLORS["section_bg"]),
            "text_color": dual(COLORS["text"]),
        },
        "CTkRadioButton": {
            "fg_color": dual(COLORS["primary"]),
            "hover_color": dual(COLORS["primary_hover"]),
        },
    }
    for widget, opts in pairs.items():
        if widget not in theme:
            continue
        for key, value in opts.items():
            if key in theme[widget]:
                try:
                    theme[widget][key] = value
                except Exception:
                    pass


def apply_matplotlib_theme():
    """Light, clean matplotlib defaults (font + surfaces only). Individual plot
    methods set their own colours/sizes, so this is purely cosmetic baseline."""
    mpl.rcParams.update({
        "figure.facecolor": COLORS["plot_bg"],
        "axes.facecolor": COLORS["plot_axes_bg"],
        "axes.edgecolor": COLORS["border"],
        "axes.labelcolor": COLORS["text"],
        "text.color": COLORS["text"],
        "xtick.color": COLORS["text_muted"],
        "ytick.color": COLORS["text_muted"],
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
        "axes.titleweight": "bold",
        "figure.autolayout": False,
    })


def apply_appearance():
    """Set up CustomTkinter + matplotlib look. Safe to call more than once."""
    # Disable CustomTkinter's automatic per-monitor DPI tracker. Its 100 ms
    # `check_dpi_scaling` loop reconfigures every registered widget on a DPI
    # change, but destroyed CTkComboBox dropdown menus (we rebuild the Graph
    # Settings panel often) stay registered -> `TclError: invalid command name
    # ...dropdownmenu`. Worse, that loop first drops the window to alpha=0.15 and
    # only restores it *after* the rescale, so the crash leaves the whole window
    # stuck semi-transparent. Turning the tracker off removes both symptoms.
    # Must run before the CTk window is created (both entry points call this).
    try:
        ctk.deactivate_automatic_dpi_awareness()
    except Exception:
        pass
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    _nudge_ctk_widget_colors()
    apply_matplotlib_theme()


# --------------------------------------------------------------------------- #
#  Dark plot theme (applied as a post-process to a finished figure so it works #
#  for every plot type without editing the individual plot methods).          #
# --------------------------------------------------------------------------- #
PLOT_DARK = {
    "bg":   "#0f172a",   # slate-900
    "axes": "#1e293b",   # slate-800
    "text": "#e2e8f0",   # slate-200
    "muted": "#94a3b8",  # slate-400
    "grid": "#334155",   # slate-700
}


def apply_dark_to_figure(fig):
    """Recolour a finished matplotlib Figure for a dark background.

    Leaves the data artists' own colours alone (lines, scatter, images) and
    only restyles the canvas, spines, ticks, labels, titles, legends and
    gridlines. Reversible by simply re-plotting in light mode.
    """
    if fig is None:
        return
    d = PLOT_DARK
    fig.patch.set_facecolor(d["bg"])
    for ax in fig.get_axes():
        ax.set_facecolor(d["axes"])
        for spine in ax.spines.values():
            spine.set_color(d["grid"])
        ax.tick_params(colors=d["muted"])
        ax.xaxis.label.set_color(d["text"])
        ax.yaxis.label.set_color(d["text"])
        if ax.get_title():
            ax.title.set_color(d["text"])
        leg = ax.get_legend()
        if leg is not None:
            leg.get_frame().set_facecolor(d["axes"])
            leg.get_frame().set_edgecolor(d["grid"])
            for txt in leg.get_texts():
                txt.set_color(d["text"])
        ax.grid(True, color=d["grid"], alpha=0.4)


def apply_light_to_figure(fig):
    """Restore a finished matplotlib Figure to the light theme.

    The inverse of apply_dark_to_figure: re-light the canvas, spines, ticks,
    labels, titles, legends and gridlines so toggling dark -> light leaves no
    black surfaces behind. Data artists are left untouched.
    """
    if fig is None:
        return
    fig.patch.set_facecolor(COLORS["plot_bg"])
    for ax in fig.get_axes():
        ax.set_facecolor(COLORS["plot_axes_bg"])
        for spine in ax.spines.values():
            spine.set_color(COLORS["border"])
        ax.tick_params(colors=COLORS["text_muted"])
        ax.xaxis.label.set_color(COLORS["text"])
        ax.yaxis.label.set_color(COLORS["text"])
        if ax.get_title():
            ax.title.set_color(COLORS["text"])
        leg = ax.get_legend()
        if leg is not None:
            leg.get_frame().set_facecolor(COLORS["card"])
            leg.get_frame().set_edgecolor(COLORS["border"])
            for txt in leg.get_texts():
                txt.set_color(COLORS["text"])
        ax.grid(True, color=COLORS["border"], alpha=0.6)


# Apply on import so simply importing the package configures the look,
# mirroring the original module-level behaviour.
apply_appearance()
