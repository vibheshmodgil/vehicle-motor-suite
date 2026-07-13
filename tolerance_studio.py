"""Motor tolerancing & stack-up studio -- a single self-contained page.

Drop into an existing Tk/ttk app as one more ttk.Notebook tab:

    from tolerance_studio import ToleranceStudioPage
    nb.add(ToleranceStudioPage(nb, data_path="tolerance_studio.json"),
           text="Tolerance & Stack-up")

Pure stdlib + tkinter/ttk. matplotlib (chain chart) and openpyxl (.xlsx
bootstrap import) are optional -- the page degrades gracefully without them.
Touches nothing outside this module; the whole model persists to one JSON
file (loaded on init, saved after every edit and again on teardown).

UI notes (2026-07 redesign):
  * All styling is local: tk widgets carry their own colors, and every
    ttk style name is prefixed "Tol." so nothing leaks into the host app
    (no ttk.Style().theme_use() call anywhere).
  * Navigation is a custom segmented bar + tkraise()'d views (not a nested
    ttk.Notebook) so the active view is visually obvious and stylable.
  * Fits and Radial use master-detail layouts (table + editor panel);
    Axial pairs the chain editor with a live result card + waterfall chart.
  * Motion: all animation runs through ToleranceStudioPage._animate (one
    cancellable after() ticker per key, cubic ease-out; finalize always
    lands the exact end state). Module flag ANIMATIONS=False -- or an
    unmapped page, e.g. headless tests -- degrades every effect to an
    instant jump, so end states are identical with or without motion.
    Effects: sliding nav underline + view glide on tab switch, count-up
    on the big result numbers, bg pulse on status badges/chips when their
    state CHANGES, waterfall bars growing from their cumulative anchors,
    button hover fades, and the "saved" note flashing green then fading.

2026-07 fixes (user feedback):
  * Tolerances are SIGNED deviations: unilateral +/+ and -/- entries are
    valid; the only invariant is upper >= lower, enforced (swap + warn) at
    every entry point. Legacy files/sheets load unchanged.
  * Assemblies (dimension groups) are first-class: Model.groups persists,
    empty assemblies survive, and the Dimensions toolbar can add / rename /
    delete an assembly and move a dimension between assemblies.
  * Fits are grouped (Fit.group) into collapsible sections, rows are
    colored by classification (strong text color + soft tint + legend),
    and the editor shows an animated fit diagram: tolerance zones plus an
    exaggerated shaft-into-hole cross-section.
  * Dimensions view has a live animated tolerance-band strip for the
    selected row; Treeview selection is a strong accent highlight instead
    of the old faint tint.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    HAVE_MPL = True
except Exception:  # pragma: no cover - optional dependency
    HAVE_MPL = False

try:
    import openpyxl
    HAVE_OPENPYXL = True
except Exception:  # pragma: no cover - optional dependency
    HAVE_OPENPYXL = False


# --------------------------------------------------------------------------- #
#  Visual system (self-contained -- matches the VMI suite's indigo/slate)     #
# --------------------------------------------------------------------------- #
C = {
    "bg":            "#eef1f7",   # page background
    "surface":       "#ffffff",   # cards / tables
    "sunken":        "#f4f6fa",   # toolbars, group rows, zebra stripe
    "border":        "#dbe2ec",
    "border_strong": "#b9c4d4",
    "ink":           "#0f172a",
    "muted":         "#5a6a7e",
    "faint":         "#93a1b3",
    "accent":        "#4f46e5",   # indigo (VMI primary)
    "accent_dark":   "#4338ca",
    "accent_soft":   "#eef2ff",
    "good":          "#15803d",
    "good_soft":     "#e5f4ea",
    "info":          "#1d4ed8",
    "info_soft":     "#e8effd",
    "warn":          "#c2540a",
    "warn_soft":     "#fdeee1",
    "bad":           "#b91c1c",
    "bad_soft":      "#fdeaea",
}

F_UI      = ("Segoe UI", 10)
F_UI_B    = ("Segoe UI Semibold", 10)
F_SMALL   = ("Segoe UI", 9)
F_H1      = ("Segoe UI Semibold", 15)
F_H2      = ("Segoe UI Semibold", 11)
F_MONO    = ("Consolas", 10)
F_MONO_B  = ("Consolas", 11, "bold")
F_MONO_XL = ("Consolas", 17, "bold")
MONO_FONT = F_MONO  # back-compat alias

# fit classification -> (strong color, soft background)
CLS_COLORS = {
    "Clearance":    (C["good"], C["good_soft"]),
    "Transition":   (C["info"], C["info_soft"]),
    "Interference": (C["warn"], C["warn_soft"]),
    "Invalid":      (C["faint"], C["sunken"]),
}
BADGE_COLORS = {k: v[0] for k, v in CLS_COLORS.items()}  # back-compat

# --------------------------------------------------------------------------- #
#  Motion (all animation flows through ToleranceStudioPage._animate)          #
# --------------------------------------------------------------------------- #
# Master kill switch. When False -- or whenever the page is not actually on
# screen (winfo_ismapped() is false, e.g. under headless tests) -- every
# animation jumps straight to its final frame, so end states are identical
# with or without motion.
ANIMATIONS = True
_ANIM_FRAME_MS = 16  # ~60 fps


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _blend(a, b, t):
    """Linear blend between two '#rrggbb' colors, t in [0, 1]."""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t),
                              round(ga + (gb - ga) * t),
                              round(ba + (bb - ba) * t))


def _ease_out(t):
    """Cubic ease-out: fast start, gentle settle."""
    return 1.0 - (1.0 - t) ** 3


# --------------------------------------------------------------------------- #
#  Small helpers                                                              #
# --------------------------------------------------------------------------- #
def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _to_float(value, default=0.0):
    if value is None:
        return default
    s = str(value).strip()
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _fmt(x, nd=3):
    if x is None:
        return "—"
    try:
        return f"{x:.{nd}f}"
    except Exception:
        return str(x)


def _spread_labels(cv, items):
    """Nudge overlapping canvas text items apart vertically (top-to-bottom
    greedy passes) so tiny tolerance zones can't stack their value labels
    on top of each other. Works on final geometry; fade-in animations only
    touch fill color, so moving items up-front is safe."""
    infos = []
    for it in items:
        try:
            bb = cv.bbox(it)
        except Exception:
            bb = None
        if bb:
            infos.append([it, list(bb)])
    infos.sort(key=lambda e: (e[1][1] + e[1][3]) / 2.0)
    for _ in range(4):  # a nudge can create a new overlap further down
        moved = False
        for i in range(1, len(infos)):
            it, bb = infos[i]
            for _pit, pbb in infos[:i]:
                if (bb[0] < pbb[2] and bb[2] > pbb[0]
                        and bb[1] < pbb[3] and bb[3] > pbb[1]):
                    dy = (pbb[3] - bb[1]) + 1
                    cv.move(it, 0, dy)
                    bb[1] += dy
                    bb[3] += dy
                    moved = True
        if not moved:
            break


def _mk(cls, d):
    """Build a dataclass instance from a dict, ignoring unknown keys and
    tolerating missing ones (defaults fill the gaps). Never raises."""
    if not isinstance(d, dict):
        return cls()
    names = {f.name for f in dataclasses.fields(cls)}
    try:
        return cls(**{k: v for k, v in d.items() if k in names})
    except Exception:
        return cls()


# --------------------------------------------------------------------------- #
#  Data model (unchanged from the original implementation)                    #
# --------------------------------------------------------------------------- #
@dataclass
class Dimension:
    id: str = ""
    name: str = ""
    group: str = "General"
    nominal: float = 0.0
    # Signed ISO-style deviations. tol_up is the UPPER deviation and tol_lo
    # the LOWER one; each may carry either sign, so unilateral tolerances
    # like 15 +0.030/+0.010 (both positive) or 15 -0.010/-0.030 (both
    # negative) are representable. The only invariant is tol_up >= tol_lo,
    # enforced at every entry point (add, cell edit, import).
    tol_up: float = 0.0
    tol_lo: float = 0.0

    @property
    def max(self):
        return self.nominal + self.tol_up

    @property
    def min(self):
        return self.nominal + self.tol_lo

    @property
    def mid(self):
        return (self.max + self.min) / 2.0

    @property
    def half(self):
        return (self.max - self.min) / 2.0


@dataclass
class Fit:
    id: str = ""
    name: str = ""
    group: str = "General"
    hole_dim_id: str = ""
    shaft_dim_id: str = ""

    def max_clear(self, dims: Dict[str, Dimension]) -> Optional[float]:
        hole = dims.get(self.hole_dim_id)
        shaft = dims.get(self.shaft_dim_id)
        if hole is None or shaft is None:
            return None
        return hole.max - shaft.min

    def min_clear(self, dims: Dict[str, Dimension]) -> Optional[float]:
        hole = dims.get(self.hole_dim_id)
        shaft = dims.get(self.shaft_dim_id)
        if hole is None or shaft is None:
            return None
        return hole.min - shaft.max

    def classification(self, dims: Dict[str, Dimension]) -> str:
        mn, mx = self.min_clear(dims), self.max_clear(dims)
        if mn is None or mx is None:
            return "Invalid"
        if mn > 0:
            return "Clearance"
        if mx < 0:
            return "Interference"
        return "Transition"


@dataclass
class StackLink:
    id: str = ""
    dim_id: str = ""
    sense: int = 1  # +1 or -1


@dataclass
class AxialStack:
    id: str = ""
    name: str = ""
    method: str = "WC"  # 'WC' or 'RSS'
    links: List[StackLink] = field(default_factory=list)
    target_min: Optional[float] = None
    target_max: Optional[float] = None

    def compute(self, dims: Dict[str, Dimension]) -> Dict[str, Any]:
        pairs = [(l, dims.get(l.dim_id)) for l in self.links]
        valid = [(l, d) for l, d in pairs if d is not None]
        nominal = sum(l.sense * d.nominal for l, d in valid)

        if self.method == "RSS":
            mid = sum(l.sense * d.mid for l, d in valid)
            tol = math.sqrt(sum(d.half ** 2 for _, d in valid))
            gap_min, gap_max = mid - tol, mid + tol
        else:  # worst-case
            gap_max = (sum(d.max for l, d in valid if l.sense > 0)
                       - sum(d.min for l, d in valid if l.sense < 0))
            gap_min = (sum(d.min for l, d in valid if l.sense > 0)
                       - sum(d.max for l, d in valid if l.sense < 0))

        pass_fail = None
        if self.target_min is not None or self.target_max is not None:
            pass_fail = True
            if self.target_min is not None and gap_min < self.target_min - 1e-9:
                pass_fail = False
            if self.target_max is not None and gap_max > self.target_max + 1e-9:
                pass_fail = False

        return {
            "nominal": nominal, "min": gap_min, "max": gap_max,
            "pass": pass_fail, "invalid_links": len(pairs) - len(valid),
        }


@dataclass
class RadialContributor:
    id: str = ""
    label: str = ""
    type: str = "coaxiality"  # fitClearance | coaxiality | runout | bearingClearance
    fit_id: Optional[str] = None
    value: float = 0.0


@dataclass
class RadialAirgap:
    stator_id_dim_id: Optional[str] = None
    rotor_od_dim_id: Optional[str] = None
    method: str = "WC"
    contributors: List[RadialContributor] = field(default_factory=list)

    def contributor_e(self, c: RadialContributor, dims: Dict[str, Dimension],
                       fits: Dict[str, Fit]) -> float:
        if c.type == "fitClearance":
            fit = fits.get(c.fit_id) if c.fit_id else None
            mc = fit.max_clear(dims) if fit else None
            return (mc / 2.0) if mc is not None else 0.0
        if c.type in ("coaxiality", "runout"):
            return c.value / 2.0
        if c.type == "bearingClearance":
            # ISO 5753 "radial internal clearance" is itself a diametral
            # (peak-to-peak) spec -- the distance one ring can move from one
            # side to the opposite side -- so halve it the same way the
            # other diametral contributor types above already are.
            return c.value / 2.0
        return c.value

    def compute(self, dims: Dict[str, Dimension], fits: Dict[str, Fit]):
        stator = dims.get(self.stator_id_dim_id) if self.stator_id_dim_id else None
        rotor = dims.get(self.rotor_od_dim_id) if self.rotor_od_dim_id else None
        if stator is None or rotor is None:
            return None
        g0 = (stator.nominal - rotor.nominal) / 2.0
        es = [self.contributor_e(c, dims, fits) for c in self.contributors]
        e_wc = sum(es)
        e_rss = math.sqrt(sum(e * e for e in es))
        e_sel = e_rss if self.method == "RSS" else e_wc
        return {
            "g0": g0, "E_wc": e_wc, "E_rss": e_rss,
            "min_airgap_wc": g0 - e_wc, "min_airgap_rss": g0 - e_rss,
            "ecc_pct": (e_sel / g0 * 100.0) if g0 else 0.0,
        }


class Model:
    """Single source of truth. Fits/stacks/radial reference dimensions by id
    only -- never by a copy of the value."""

    def __init__(self):
        self.dimensions: List[Dimension] = []
        self.fits: List[Fit] = []
        self.stacks: List[AxialStack] = []
        self.radial: RadialAirgap = RadialAirgap()
        # Assemblies (dimension groups) exist in their own right, not just
        # as whatever strings the dimensions happen to carry -- so an empty
        # assembly survives a refresh and can be created before its parts.
        self.groups: List[str] = ["General"]

    # ---- lookups ----
    def dims_by_id(self) -> Dict[str, Dimension]:
        return {d.id: d for d in self.dimensions}

    def fits_by_id(self) -> Dict[str, Fit]:
        return {f.id: f for f in self.fits}

    def dim_by_id(self, did) -> Optional[Dimension]:
        return self.dims_by_id().get(did)

    def stack_by_id(self, sid) -> Optional[AxialStack]:
        return next((s for s in self.stacks if s.id == sid), None)

    def recompute(self):
        """No-op: Dimension/Fit/AxialStack/RadialAirgap values are computed
        live via properties/methods, so there's nothing to cache. Kept as an
        explicit step so the edit -> model.recompute() -> page.refresh_all()
        pattern has one obvious place to add caching later if needed."""
        return None

    # ---- assemblies (dimension groups) ----
    def register_group(self, name: str):
        name = (name or "").strip()
        if name and name not in self.groups:
            self.groups.append(name)

    def rename_group(self, old: str, new: str):
        new = (new or "").strip()
        if not new or old == new:
            return
        self.groups = [new if g == old else g for g in self.groups]
        if new not in self.groups:
            self.groups.append(new)
        # collapse duplicates while keeping order
        seen, uniq = set(), []
        for g in self.groups:
            if g not in seen:
                seen.add(g)
                uniq.append(g)
        self.groups = uniq
        for d in self.dimensions:
            if d.group == old:
                d.group = new

    def delete_group(self, name: str, delete_dims: bool):
        """Remove an assembly. delete_dims=True also removes its dimensions;
        False re-homes them to 'General'."""
        if delete_dims:
            self.dimensions = [d for d in self.dimensions if d.group != name]
        else:
            for d in self.dimensions:
                if d.group == name:
                    d.group = "General"
            self.register_group("General")
        self.groups = [g for g in self.groups if g != name]

    # ---- dimensions ----
    def add_dimension(self, name="New Dimension", group="General",
                       nominal=0.0, tol_up=0.0, tol_lo=0.0) -> Dimension:
        if tol_up < tol_lo:  # keep the invariant, whatever the caller sent
            tol_up, tol_lo = tol_lo, tol_up
        d = Dimension(id=_new_id(), name=name, group=group, nominal=nominal,
                       tol_up=tol_up, tol_lo=tol_lo)
        self.dimensions.append(d)
        self.register_group(group)
        return d

    def duplicate_dimension(self, did) -> Optional[Dimension]:
        d = self.dim_by_id(did)
        if d is None:
            return None
        nd = Dimension(id=_new_id(), name=d.name + " copy", group=d.group,
                        nominal=d.nominal, tol_up=d.tol_up, tol_lo=d.tol_lo)
        self.dimensions.append(nd)
        return nd

    def delete_dimension(self, did):
        self.dimensions = [d for d in self.dimensions if d.id != did]

    # ---- fits ----
    def add_fit(self, name="New Fit", hole_id="", shaft_id="",
                group="General") -> Fit:
        f = Fit(id=_new_id(), name=name, group=group or "General",
                hole_dim_id=hole_id, shaft_dim_id=shaft_id)
        self.fits.append(f)
        return f

    def delete_fit(self, fid):
        self.fits = [f for f in self.fits if f.id != fid]

    # ---- axial stacks ----
    def add_stack(self, name="New Stack") -> AxialStack:
        s = AxialStack(id=_new_id(), name=name)
        self.stacks.append(s)
        return s

    def delete_stack(self, sid):
        self.stacks = [s for s in self.stacks if s.id != sid]

    def add_link(self, stack: AxialStack, dim_id: str, sense: int = 1) -> StackLink:
        link = StackLink(id=_new_id(), dim_id=dim_id, sense=sense)
        stack.links.append(link)
        return link

    def remove_link(self, stack: AxialStack, link_id: str):
        stack.links = [l for l in stack.links if l.id != link_id]

    def move_link(self, stack: AxialStack, link_id: str, delta: int):
        idx = next((i for i, l in enumerate(stack.links) if l.id == link_id), None)
        if idx is None:
            return
        new_idx = idx + delta
        if 0 <= new_idx < len(stack.links):
            stack.links[idx], stack.links[new_idx] = stack.links[new_idx], stack.links[idx]

    def toggle_sense(self, stack: AxialStack, link_id: str):
        for l in stack.links:
            if l.id == link_id:
                l.sense = -l.sense

    # ---- radial contributors ----
    def add_contributor(self, label="New Contributor", type_="coaxiality",
                         fit_id=None, value=0.0) -> RadialContributor:
        c = RadialContributor(id=_new_id(), label=label, type=type_,
                               fit_id=fit_id, value=value)
        self.radial.contributors.append(c)
        return c

    def remove_contributor(self, cid):
        self.radial.contributors = [c for c in self.radial.contributors if c.id != cid]

    # ---- persistence ----
    def to_dict(self) -> dict:
        return {
            "groups": list(self.groups),
            "dimensions": [dataclasses.asdict(d) for d in self.dimensions],
            "fits": [dataclasses.asdict(f) for f in self.fits],
            "stacks": [dataclasses.asdict(s) for s in self.stacks],
            "radial": dataclasses.asdict(self.radial),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Model":
        m = cls()
        data = data or {}
        m.dimensions = [_mk(Dimension, d) for d in (data.get("dimensions") or [])]
        m.fits = [_mk(Fit, d) for d in (data.get("fits") or [])]

        stacks = []
        for sd in (data.get("stacks") or []):
            sd = dict(sd) if isinstance(sd, dict) else {}
            links_raw = sd.pop("links", []) or []
            stack = _mk(AxialStack, sd)
            stack.links = [_mk(StackLink, ld) for ld in links_raw]
            stacks.append(stack)
        m.stacks = stacks

        rd = dict(data.get("radial") or {})
        contribs_raw = rd.pop("contributors", []) or []
        radial = _mk(RadialAirgap, rd)
        radial.contributors = [_mk(RadialContributor, cd) for cd in contribs_raw]
        m.radial = radial

        # Assemblies: explicit list (new files) unioned with whatever the
        # dimensions reference (old files had no "groups" key at all).
        m.groups = [str(g) for g in (data.get("groups") or []) if str(g).strip()]
        if not m.groups:
            m.groups = ["General"]
        for d in m.dimensions:
            m.register_group(d.group)
        # Legacy files may hold tol values that violate up >= lo; normalize.
        for d in m.dimensions:
            if d.tol_up < d.tol_lo:
                d.tol_up, d.tol_lo = d.tol_lo, d.tol_up
        return m


def _build_seed_model() -> Model:
    """A plausible small hub-motor tolerance stack, wired up with real ids so
    every fit / stack / contributor references a real Dimension from the
    start. Purely a starting point -- everything is editable in-app."""
    m = Model()

    # tol args are signed deviations: (upper, lower), lower usually negative
    shaft_od = m.add_dimension("Shaft OD @ Bearing Seat", "Shaft", 15.000, 0.000, -0.008)
    brg_bore = m.add_dimension("Bearing Bore ID", "Bearing", 15.000, 0.010, 0.000)
    brg_od = m.add_dimension("Bearing OD", "Bearing", 35.000, 0.000, -0.011)
    housing_bore = m.add_dimension("Housing Bearing Bore", "Housing", 35.000, 0.018, 0.000)
    brg_width = m.add_dimension("Bearing Width", "Bearing", 11.000, 0.000, -0.120)
    circlip_gap = m.add_dimension("Circlip Groove-to-Shoulder", "Shaft", 2.000, 0.050, -0.050)
    shoulder_to_cover = m.add_dimension("Housing Shoulder-to-Cover Face", "Housing", 14.300, 0.100, -0.100)
    cover_thk = m.add_dimension("End Cover Thickness", "Housing", 3.000, 0.050, -0.050)
    stator_id = m.add_dimension("Stator ID", "Stator", 90.000, 0.050, 0.000)
    rotor_od = m.add_dimension("Rotor OD", "Rotor", 89.200, 0.000, -0.060)

    m.add_fit("Bearing Inner Ring Fit", brg_bore.id, shaft_od.id, group="Bearings")
    fit_outer = m.add_fit("Bearing Outer Ring Fit", housing_bore.id, brg_od.id, group="Bearings")

    axial = m.add_stack("Bearing Axial Float")
    axial.method = "WC"
    m.add_link(axial, shoulder_to_cover.id, +1)
    m.add_link(axial, cover_thk.id, -1)
    m.add_link(axial, brg_width.id, -1)
    axial.target_min, axial.target_max = 0.05, 0.60

    retention = m.add_stack("Circlip Retention Clearance")
    retention.method = "RSS"
    m.add_link(retention, shoulder_to_cover.id, +1)
    m.add_link(retention, cover_thk.id, -1)
    m.add_link(retention, brg_width.id, -1)
    m.add_link(retention, circlip_gap.id, -1)
    # No target set deliberately -- this second demo stack shows the RSS
    # method and the multi-stack picker rather than a worked pass/fail case;
    # a made-up target here would just be a guess. Set your own once you've
    # replaced these dimensions with real ones.

    m.radial.stator_id_dim_id = stator_id.id
    m.radial.rotor_od_dim_id = rotor_od.id
    m.radial.method = "WC"
    m.add_contributor("Bearing outer fit clearance", "fitClearance", fit_outer.id, 0.0)
    m.add_contributor("Rotor coaxiality (diametral)", "coaxiality", None, 0.030)
    m.add_contributor("Stator bore runout (diametral)", "runout", None, 0.020)
    m.add_contributor("Bearing internal clearance (datasheet, diametral)", "bearingClearance", None, 0.010)

    return m


# --------------------------------------------------------------------------- #
#  Reusable widgets                                                           #
# --------------------------------------------------------------------------- #
class EditableTreeview(ttk.Treeview):
    """Treeview with double-click-to-edit cells via a popup Entry.

    editable_cols: set of column ids (including "#0", the tree column) that
        may be edited. Rows tagged "noedit" are never editable regardless.
    on_edit(row_id, col_id, new_text): called after a commit (Return/FocusOut).
    """

    def __init__(self, master, editable_cols=None, on_edit=None, **kw):
        kw.setdefault("style", "Tol.Treeview")
        super().__init__(master, **kw)
        self.editable_cols = set(editable_cols or ())
        self.on_edit = on_edit
        self._editor = None
        self._hover_cursor_on = False
        self.bind("<Double-1>", self._begin_edit)
        # F-04: hover cursor distinguishes editable cells from read-only ones
        # (Min/Max, group headers) -- previously a double-click on a
        # read-only cell silently did nothing, with no cue beforehand.
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover_cursor(False))

    def _editable_col_at(self, event):
        """Return the resolved column id ('#0' or a data column name) under
        the pointer if that cell is currently editable, else None."""
        region = self.identify("region", event.x, event.y)
        if region not in ("cell", "tree"):
            return None
        row_id = self.identify_row(event.y)
        col = self.identify_column(event.x)
        if not row_id or not col:
            return None
        if "noedit" in self.item(row_id, "tags"):
            return None
        if col == "#0":
            col_id = "#0"
        else:
            cols = self["columns"]
            idx = int(col[1:]) - 1
            if idx < 0 or idx >= len(cols):
                return None
            col_id = cols[idx]
        return col_id if col_id in self.editable_cols else None

    def _set_hover_cursor(self, on):
        if on == self._hover_cursor_on:
            return
        self._hover_cursor_on = on
        try:
            self.configure(cursor="xterm" if on else "")
        except Exception:
            pass

    def _on_motion(self, event):
        self._set_hover_cursor(self._editable_col_at(event) is not None)

    def _begin_edit(self, event):
        row_id = self.identify_row(event.y)
        col_id = self._editable_col_at(event)
        if col_id is None:
            return
        self.edit_cell(row_id, col_id)

    def edit_cell(self, row_id, col_id):
        try:
            bbox = self.bbox(row_id, col_id)
        except Exception:
            bbox = None
        if not bbox:
            return
        x, y, w, h = bbox
        self._destroy_editor()
        current = self.item(row_id, "text") if col_id == "#0" else self.set(row_id, col_id)
        entry = tk.Entry(self, font=F_MONO, relief="solid", bd=1,
                         bg=C["surface"], fg=C["ink"],
                         insertbackground=C["ink"],
                         highlightthickness=1, highlightcolor=C["accent"],
                         highlightbackground=C["accent"])
        entry.insert(0, current)
        entry.select_range(0, tk.END)
        entry.place(x=x, y=y, width=max(w, 70), height=h)
        entry.focus_set()

        def commit(_evt=None):
            val = entry.get()
            self._destroy_editor()
            if col_id == "#0":
                self.item(row_id, text=val)
            else:
                self.set(row_id, col_id, val)
            if self.on_edit:
                self.on_edit(row_id, col_id, val)

        def cancel(_evt=None):
            self._destroy_editor()

        entry.bind("<Return>", commit)
        entry.bind("<KP_Enter>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", commit)
        self._editor = entry

    def _destroy_editor(self):
        if self._editor is not None:
            try:
                self._editor.destroy()
            except Exception:
                pass
            self._editor = None


class Segmented(tk.Frame):
    """Small two-or-more-way segmented toggle built from tk.Buttons.
    command(value) fires when the user picks a different value."""

    def __init__(self, master, values, command=None, **kw):
        kw.setdefault("bg", C["surface"])
        super().__init__(master, **kw)
        self.configure(highlightthickness=1, highlightbackground=C["border_strong"])
        self.values = list(values)
        self.command = command
        self._value = self.values[0]
        self._btns = {}
        for v in self.values:
            b = tk.Button(self, text=v, font=F_UI_B, relief="flat", bd=0,
                          padx=14, pady=3, cursor="hand2",
                          command=lambda vv=v: self._on_click(vv))
            b.pack(side="left")
            self._btns[v] = b
        self._paint()

    def _on_click(self, v):
        if v != self._value:
            self._value = v
            self._paint()
            if self.command:
                self.command(v)

    def get(self):
        return self._value

    def set(self, v):
        if v in self._btns:
            self._value = v
            self._paint()

    def _paint(self):
        for v, b in self._btns.items():
            if v == self._value:
                b.configure(bg=C["accent"], fg="white",
                            activebackground=C["accent_dark"], activeforeground="white")
            else:
                b.configure(bg=C["surface"], fg=C["muted"],
                            activebackground=C["sunken"], activeforeground=C["ink"])


class WrapBar(tk.Frame):
    """A button-bar container that keeps every control reachable even in a
    narrow window (F-02): items are packed left-to-right into a row, and a
    new row starts once the running width would pass a conservative floor.
    The split is decided once, as each item is added -- not recomputed live
    against the window's actual width.

    .add() takes a FACTORY (row_parent -> widget), not an already-built
    widget: each widget is constructed directly against whichever row frame
    it ends up in, so its real Tk parent and its pack container are always
    the same widget. (Two earlier versions cut corners here -- live re-wrap
    via .place() + a self-observing <Configure> binding fed back into a
    resize loop that hung the app outright; packing an already-built widget
    into a *different* frame via pack(in_=...) ran without error but
    silently failed to clip/position correctly once more than one row
    existed. Building against the real parent from the start avoids both.)
    """

    def __init__(self, master, min_row_width=640, **kw):
        """min_row_width: how much width to assume is actually available
        before wrapping. Pass the realistic figure for THIS bar's container,
        not the window width -- a bar sharing its row with a fixed-width
        side panel (e.g. Axial's chain editor next to the 380px result
        card) has much less room than a bar spanning the full width."""
        kw.setdefault("bg", C["sunken"])
        super().__init__(master, **kw)
        self.min_row_width = min_row_width
        self._row = None
        self._row_w = 0

    def _new_row(self):
        self._row = tk.Frame(self, bg=self["bg"])
        self._row.pack(fill="x")
        self._row_w = 0
        return self._row

    def add(self, factory, padx=(0, 6), pady=(6, 6)):
        if isinstance(padx, int):
            padx = (padx, padx)
        if isinstance(pady, int):
            pady = (pady, pady)
        row = self._row if self._row is not None else self._new_row()
        widget = factory(row)
        widget.update_idletasks()
        w = widget.winfo_reqwidth() + padx[0] + padx[1]
        if self._row_w > 0 and self._row_w + w > self.min_row_width:
            widget.destroy()
            widget = factory(self._new_row())
        widget.pack(side="left", padx=padx, pady=pady)
        self._row_w += w
        return widget


def _btn(parent, text, command, kind="ghost", padx=13, pady=5):
    """Flat tk.Button with hover states. kinds: primary / ghost / danger."""
    spec = {
        "primary": dict(bg=C["accent"], fg="white", hover=C["accent_dark"],
                        border=C["accent"]),
        "ghost":   dict(bg=C["surface"], fg=C["ink"], hover=C["sunken"],
                        border=C["border_strong"]),
        "danger":  dict(bg=C["surface"], fg=C["bad"], hover=C["bad_soft"],
                        border=C["border_strong"]),
    }[kind]
    b = tk.Button(parent, text=text, command=command, font=F_UI_B,
                  relief="flat", bd=0, padx=padx, pady=pady, cursor="hand2",
                  bg=spec["bg"], fg=spec["fg"],
                  activebackground=spec["hover"],
                  activeforeground=spec["fg"] if kind != "primary" else "white",
                  highlightthickness=1, highlightbackground=spec["border"])
    def _fade_to(target):
        """Short bg fade on hover in/out (self-contained: uses the button's
        own after loop, so it needs no page-level machinery)."""
        prev = getattr(b, "_hover_job", None)
        if prev is not None:
            try:
                b.after_cancel(prev)
            except Exception:
                pass
            b._hover_job = None
        if not ANIMATIONS:
            b.configure(bg=target)
            return
        start = b.cget("bg")
        steps = 5

        def step(i=1):
            if not b.winfo_exists():
                return
            b.configure(bg=_blend(start, target, i / steps))
            if i < steps:
                b._hover_job = b.after(_ANIM_FRAME_MS, step, i + 1)
            else:
                b._hover_job = None

        step()

    b.bind("<Enter>", lambda e: _fade_to(spec["hover"]))
    b.bind("<Leave>", lambda e: _fade_to(spec["bg"]))
    return b


def _card(parent, **kw):
    kw.setdefault("bg", C["surface"])
    f = tk.Frame(parent, highlightthickness=1,
                 highlightbackground=C["border"], **kw)
    return f


def _pill(parent, text="", fg=C["muted"], bg=C["sunken"]):
    return tk.Label(parent, text=text, font=F_UI_B, fg=fg, bg=bg,
                    padx=10, pady=2)


# --------------------------------------------------------------------------- #
#  The page                                                                   #
# --------------------------------------------------------------------------- #
class ToleranceStudioPage(ttk.Frame):
    def __init__(self, parent, data_path="tolerance_studio.json"):
        super().__init__(parent)
        self.data_path = data_path
        self.model = self._load_or_seed()
        self.active_stack_id = self.model.stacks[0].id if self.model.stacks else None
        self._loading_editor = False  # reentrancy guard for detail editors

        # animation bookkeeping (see _animate) -- must exist before any
        # refresh/_save can run, since those route through animated setters
        self._anim_jobs: Dict[str, dict] = {}   # key -> {"id": after-id, "finalize": fn}
        self._anim_num: Dict[str, Optional[float]] = {}   # last value per numeric label
        self._anim_state: Dict[str, tuple] = {}  # last (text, fg, bg) per status label

        self._init_styles()
        self._build_ui()
        self.refresh_all()
        self._save()
        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------ #
    #  Styles (all names prefixed Tol. -- nothing leaks to the host app)  #
    # ------------------------------------------------------------------ #
    def _init_styles(self):
        st = ttk.Style(self)
        st.configure("Tol.Treeview",
                     background=C["surface"], fieldbackground=C["surface"],
                     foreground=C["ink"], font=F_MONO, rowheight=26,
                     borderwidth=0)
        st.configure("Tol.Treeview.Heading",
                     font=("Segoe UI Semibold", 9),
                     foreground=C["muted"])
        # Selection must be unmissable (user feedback: the old accent_soft
        # highlight was so faint you couldn't tell which row was active).
        st.map("Tol.Treeview",
               background=[("selected", C["accent"])],
               foreground=[("selected", "#ffffff")])
        st.configure("Tol.TCombobox", font=F_UI)

    # ------------------------------------------------------------------ #
    #  Persistence                                                        #
    # ------------------------------------------------------------------ #
    def _load_or_seed(self) -> Model:
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return Model.from_dict(data)
            except Exception as exc:
                print(f"[tolerance_studio] failed to load {self.data_path}: {exc} -- using seed data")
        return _build_seed_model()

    def _save(self):
        try:
            with open(self.data_path, "w", encoding="utf-8") as fh:
                json.dump(self.model.to_dict(), fh, indent=2)
            note, color = None, None  # written; indicator below is cosmetic
        except Exception as exc:
            print(f"[tolerance_studio] save failed: {exc}")
            note, color = "⚠ save failed", C["bad"]
        # The label may already be gone during teardown -- never let the
        # cosmetic indicator turn a successful save into an error.
        try:
            if hasattr(self, "saved_lbl") and self.saved_lbl.winfo_exists():
                if note is None:
                    import datetime
                    note = f"✓ saved {datetime.datetime.now():%H:%M:%S}"
                    # flash green, then fade to the resting faint gray
                    self.saved_lbl.configure(text=note, fg=C["good"])
                    self._animate(
                        "saved", 1200,
                        lambda k: self.saved_lbl.configure(
                            fg=_blend(C["good"], C["faint"], k)),
                        finalize=lambda: self.saved_lbl.configure(fg=C["faint"]))
                else:
                    self.saved_lbl.configure(text=note, fg=color)
        except Exception:
            pass

    def _on_destroy(self, event):
        if event.widget is self:
            for key in list(self._anim_jobs):
                job = self._anim_jobs.pop(key, None)
                if job and job.get("id") is not None:
                    try:
                        self.after_cancel(job["id"])
                    except Exception:
                        pass
            self._save()

    # ------------------------------------------------------------------ #
    #  Animation engine                                                   #
    # ------------------------------------------------------------------ #
    # One shared after()-driven ticker per animation KEY. Starting a new
    # animation on a key cancels the old one, and cancellation always runs
    # the old animation's finalize() first -- so every animation is
    # guaranteed to land on its final state exactly once, whether it plays
    # out, is superseded, or the page is being torn down. When ANIMATIONS
    # is off or the page isn't mapped (headless tests), _animate degrades
    # to "apply final frame immediately".

    def _anims_on(self) -> bool:
        try:
            return ANIMATIONS and bool(self.winfo_ismapped())
        except Exception:
            return False

    def _animate(self, key, duration_ms, on_frame, finalize=None,
                 frame_ms=_ANIM_FRAME_MS):
        """on_frame(k) with k eased 0->1; finalize() once at the end.
        frame_ms: tick interval -- pass a larger value for expensive frames
        (e.g. a full matplotlib redraw) so ticks don't saturate the event
        queue and starve user input."""
        self._cancel_anim(key)
        if not self._anims_on():
            try:
                on_frame(1.0)
            finally:
                if finalize:
                    finalize()
            return
        start = time.perf_counter()
        job = {"id": None, "finalize": finalize}
        self._anim_jobs[key] = job

        def tick():
            if not self.winfo_exists():
                return
            t = (time.perf_counter() - start) * 1000.0 / duration_ms
            done = t >= 1.0
            try:
                on_frame(_ease_out(min(t, 1.0)))
            except tk.TclError:
                done = True
            if done:
                # pop first so finalize can't be re-run by a cancel
                if self._anim_jobs.get(key) is job:
                    self._anim_jobs.pop(key, None)
                if finalize:
                    try:
                        finalize()
                    except tk.TclError:
                        pass
            else:
                job["id"] = self.after(frame_ms, tick)

        tick()

    def _cancel_anim(self, key):
        job = self._anim_jobs.pop(key, None)
        if job is None:
            return
        if job.get("id") is not None:
            try:
                self.after_cancel(job["id"])
            except Exception:
                pass
        if job.get("finalize"):
            try:
                job["finalize"]()
            except Exception:
                pass

    def _set_number_animated(self, key, label, value, fmt_fn):
        """Count a numeric label from its previous value to `value`.
        value=None -> em-dash, no animation. First-ever value -> no
        animation (nothing to count from)."""
        old = self._anim_num.get(key)
        self._anim_num[key] = value
        akey = "num:" + key
        if value is None:
            self._cancel_anim(akey)
            label.configure(text="—")
            return
        if old is None or abs(value - old) < 1e-12:
            self._cancel_anim(akey)
            label.configure(text=fmt_fn(value))
            return

        def frame(k):
            label.configure(text=fmt_fn(old + (value - old) * k))

        self._animate(akey, 320, frame,
                      finalize=lambda: label.configure(text=fmt_fn(value)))

    def _set_status_animated(self, key, label, text, fg, bg):
        """Set a status badge/chip; if its state actually CHANGED, pulse the
        background from a strong tint of the new status color down to its
        soft resting color, so a PASS->FAIL flip catches the eye."""
        new = (text, fg, bg)
        prev = self._anim_state.get(key)
        self._anim_state[key] = new
        label.configure(text=text, fg=fg)
        akey = "st:" + key
        if prev is None or prev == new:
            self._cancel_anim(akey)
            label.configure(bg=bg)
            return
        hot = _blend(fg, bg, 0.45)

        def frame(k):
            label.configure(bg=_blend(hot, bg, k))

        self._animate(akey, 500, frame,
                      finalize=lambda: label.configure(bg=bg))

    # ------------------------------------------------------------------ #
    #  Name<->id maps (dedupe display names when two items share a name)  #
    # ------------------------------------------------------------------ #
    def _name_map(self, items) -> Dict[str, str]:
        counts: Dict[str, int] = {}
        for it in items:
            counts[it.name] = counts.get(it.name, 0) + 1
        out = {}
        for it in items:
            key = f"{it.name} [{it.id[:6]}]" if counts[it.name] > 1 else it.name
            out[key] = it.id
        return out

    def _dim_name_map(self):
        return self._name_map(self.model.dimensions)

    def _dim_id_to_display(self):
        return {v: k for k, v in self._dim_name_map().items()}

    def _fit_name_map(self):
        return self._name_map(self.model.fits)

    def _fit_id_to_display(self):
        return {v: k for k, v in self._fit_name_map().items()}

    def _stack_name_map(self):
        return self._name_map(self.model.stacks)

    def _stack_id_to_display(self):
        return {v: k for k, v in self._stack_name_map().items()}

    def _active_stack(self) -> Optional[AxialStack]:
        return self.model.stack_by_id(self.active_stack_id)

    # ------------------------------------------------------------------ #
    #  UI construction                                                    #
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = tk.Frame(self, bg=C["bg"])
        outer.pack(fill="both", expand=True)

        self._build_header(outer)
        self._build_nav(outer)

        holder = tk.Frame(outer, bg=C["bg"])
        holder.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        self.views = {}
        for key in ("dims", "fits", "axial", "radial", "summary"):
            v = tk.Frame(holder, bg=C["bg"])
            v.grid(row=0, column=0, sticky="nsew")
            self.views[key] = v

        self._build_dimensions_view(self.views["dims"])
        self._build_fits_view(self.views["fits"])
        self._build_axial_view(self.views["axial"])
        self._build_radial_view(self.views["radial"])
        self._build_summary_view(self.views["summary"])

        self._select_view("dims")

    # ---- header ----
    def _build_header(self, parent):
        head = tk.Frame(parent, bg=C["surface"])
        head.pack(fill="x")
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x")

        left = tk.Frame(head, bg=C["surface"])
        left.pack(side="left", padx=16, pady=(10, 8))
        tk.Label(left, text="Tolerance & Stack-up Studio", font=F_H1,
                 fg=C["ink"], bg=C["surface"]).pack(anchor="w")
        tk.Label(left, text="Dimensions → fits → axial chains → radial air-gap, all live",
                 font=F_SMALL, fg=C["faint"], bg=C["surface"]).pack(anchor="w")

        right = tk.Frame(head, bg=C["surface"])
        right.pack(side="right", padx=16)
        self.saved_lbl = tk.Label(right, text="", font=F_SMALL,
                                  fg=C["faint"], bg=C["surface"])
        self.saved_lbl.pack(side="right", padx=(12, 0))
        self.chip_gap = _pill(right)
        self.chip_gap.pack(side="right", padx=3)
        self.chip_stacks = _pill(right)
        self.chip_stacks.pack(side="right", padx=3)
        self.chip_fits = _pill(right)
        self.chip_fits.pack(side="right", padx=3)
        # Project load/save -- separate from the silent per-edit autosave to
        # data_path (that one never needs a click). These are the explicit,
        # named-file counterpart: "Save Project As" snapshots the current
        # model to a file of your choice; "Load Project" replaces the whole
        # model with one you pick, then that becomes what autosaves from
        # here on. Same JSON shape either way, so the two are interchangeable.
        _btn(right, "💾 Save Project As…", self._save_project_as).pack(side="right", padx=(12, 3))
        _btn(right, "📁 Load Project…", self._load_project).pack(side="right", padx=3)

    def _update_chips(self):
        dims = self.model.dims_by_id()
        fits = self.model.fits_by_id()

        n_invalid = sum(1 for f in self.model.fits
                        if f.classification(dims) == "Invalid")
        if n_invalid:
            self._set_status_animated(
                "chip_fits", self.chip_fits,
                f"Fits {len(self.model.fits)} · {n_invalid} invalid",
                C["warn"], C["warn_soft"])
        else:
            self._set_status_animated("chip_fits", self.chip_fits,
                                      f"Fits {len(self.model.fits)}",
                                      C["muted"], C["sunken"])

        judged = [s.compute(dims)["pass"] for s in self.model.stacks]
        judged = [p for p in judged if p is not None]
        if judged:
            n_pass = sum(1 for p in judged if p)
            ok = n_pass == len(judged)
            self._set_status_animated(
                "chip_stacks", self.chip_stacks,
                f"Stacks {n_pass}/{len(judged)} pass",
                C["good"] if ok else C["bad"],
                C["good_soft"] if ok else C["bad_soft"])
        else:
            self._set_status_animated("chip_stacks", self.chip_stacks,
                                      f"Stacks {len(self.model.stacks)}",
                                      C["muted"], C["sunken"])

        r = self.model.radial.compute(dims, fits)
        if r is None:
            self._set_status_animated("chip_gap", self.chip_gap,
                                      "Air-gap —", C["muted"], C["sunken"])
        else:
            ok = min(r["min_airgap_wc"], r["min_airgap_rss"]) > 0
            self._set_status_animated(
                "chip_gap", self.chip_gap,
                f"Air-gap {'OK' if ok else 'RISK'}",
                C["good"] if ok else C["bad"],
                C["good_soft"] if ok else C["bad_soft"])

    # ---- nav ----
    _NAV = [("dims", "Dimensions"), ("fits", "Fits"),
            ("axial", "Axial Stack-up"), ("radial", "Radial Air-gap"),
            ("summary", "Summary")]

    def _build_nav(self, parent):
        bar = tk.Frame(parent, bg=C["bg"])
        bar.pack(fill="x", padx=14, pady=(10, 8))
        self._nav_bar = bar
        self._nav_btns = {}
        for key, label in self._NAV:
            b = tk.Button(bar, text=label, font=F_UI_B, relief="flat", bd=0,
                          padx=16, pady=6, cursor="hand2",
                          command=lambda k=key: self._select_view(k))
            # bottom gap reserves a lane for the sliding accent underline
            b.pack(side="left", padx=(0, 6), pady=(0, 5))
            self._nav_btns[key] = b
        self._nav_indicator = tk.Frame(bar, bg=C["accent"], height=3)

    def _move_nav_indicator(self, key, animate=True):
        """Slide the accent underline beneath the active nav tab."""
        b = self._nav_btns[key]
        try:
            self.update_idletasks()
        except Exception:
            return
        tx, tw, th = b.winfo_x(), b.winfo_width(), b.winfo_height()
        if tw <= 1:  # geometry not computed yet (first call during build)
            self.after(60, lambda: self._move_nav_indicator(key, animate=False))
            return
        y = th + 1  # sits in the 5px lane below the buttons
        cx, cw = self._nav_indicator.winfo_x(), self._nav_indicator.winfo_width()
        if not animate or cw <= 1 or not self._anims_on():
            self._cancel_anim("nav")
            self._nav_indicator.place(x=tx, y=y, width=tw, height=3)
            return

        def frame(k):
            self._nav_indicator.place(x=round(cx + (tx - cx) * k), y=y,
                                      width=round(cw + (tw - cw) * k), height=3)

        self._animate("nav", 220, frame,
                      finalize=lambda: self._nav_indicator.place(
                          x=tx, y=y, width=tw, height=3))

    def _slide_in_view(self, view):
        """Raise `view` with a short horizontal glide. The view is briefly
        moved from grid to place management (grid_remove keeps its options,
        so plain grid() restores it exactly); finalize ALWAYS re-grids, and
        _animate guarantees finalize runs even when a rapid tab-hopping
        click supersedes this slide mid-flight."""
        holder = view.master
        if not self._anims_on() or holder.winfo_width() <= 1:
            self._cancel_anim("view")  # re-grids any view still mid-slide
            view.tkraise()
            return
        dist = 26
        self._cancel_anim("view")
        view.grid_remove()
        view.place(x=dist, y=0, relwidth=1.0, relheight=1.0)
        view.tkraise()

        def frame(k):
            view.place_configure(x=round(dist * (1.0 - k)))

        def fin():
            try:
                view.place_forget()
                view.grid()
                view.tkraise()
            except tk.TclError:
                pass

        self._animate("view", 200, frame, finalize=fin)

    def _select_view(self, key):
        self._active_view = key
        for k, b in self._nav_btns.items():
            if k == key:
                b.configure(bg=C["accent"], fg="white",
                            activebackground=C["accent_dark"],
                            activeforeground="white")
            else:
                b.configure(bg=C["surface"], fg=C["muted"],
                            activebackground=C["sunken"],
                            activeforeground=C["ink"])
        self._move_nav_indicator(key)
        self._slide_in_view(self.views[key])

    # ================================================================== #
    #  Dimensions view                                                    #
    # ================================================================== #
    def _build_dimensions_view(self, parent):
        # table on top, dimension preview below, split by a draggable sash --
        # the old fixed strip was the first thing clipped on small windows,
        # and the sash also lets the preview be made as tall as you like
        vpaned = tk.PanedWindow(parent, orient=tk.VERTICAL, sashrelief=tk.RAISED,
                                sashwidth=6, bg=C["border_strong"],
                                bd=0, opaqueresize=True)
        vpaned.pack(fill="both", expand=True)

        card = _card(vpaned)
        vpaned.add(card, minsize=180, stretch="always")

        toolbar = tk.Frame(card, bg=C["sunken"])
        toolbar.pack(fill="x")
        bar = WrapBar(toolbar)
        bar.pack(fill="x")
        bar.add(lambda p: _btn(p, "＋ Add Dimension", self._add_dimension, "primary"), padx=(10, 4), pady=8)
        bar.add(lambda p: _btn(p, "Duplicate", self._duplicate_dimension), padx=4, pady=8)
        bar.add(lambda p: _btn(p, "Delete", self._delete_dimension, "danger"), padx=(4, 16), pady=8)
        bar.add(lambda p: _btn(p, "＋ Add Assembly", self._add_assembly), padx=4, pady=8)
        bar.add(lambda p: _btn(p, "Rename Assembly", self._rename_assembly), padx=4, pady=8)
        bar.add(lambda p: _btn(p, "Delete Assembly", self._delete_assembly, "danger"), padx=4, pady=8)
        bar.add(lambda p: _btn(p, "Move to Assembly…", self._move_dim_to_assembly), padx=4, pady=8)
        tk.Label(toolbar, text="Double-click a Name / Nominal / Tol cell to edit. Tolerances are SIGNED deviations: "
                               "+0.030/+0.010 and −0.010/−0.030 are both valid; upper must stay ≥ lower.",
                 font=F_SMALL, fg=C["faint"], bg=C["sunken"], anchor="w"
                 ).pack(fill="x", padx=12, pady=(0, 6))

        wrap = tk.Frame(card, bg=C["surface"])
        wrap.pack(fill="both", expand=True, padx=1, pady=1)
        self.dim_tree = EditableTreeview(
            wrap, columns=("nominal", "tol_up", "tol_lo", "min", "max"),
            show="tree headings", editable_cols={"#0", "nominal", "tol_up", "tol_lo"},
            on_edit=self._on_dim_cell_edit, height=18)
        self.dim_tree.heading("#0", text="NAME / ASSEMBLY")
        self.dim_tree.column("#0", width=300, anchor="w")
        for cid, text in (("nominal", "NOMINAL"), ("tol_up", "UPPER TOL"), ("tol_lo", "LOWER TOL"),
                          ("min", "MIN (live)"), ("max", "MAX (live)")):
            self.dim_tree.heading(cid, text=text)
            self.dim_tree.column(cid, width=98, anchor="e")
        self.dim_tree.tag_configure("noedit", background=C["sunken"],
                                     font=("Segoe UI Semibold", 10),
                                     foreground=C["muted"])
        self.dim_tree.tag_configure("odd", background=C["surface"])
        self.dim_tree.tag_configure("even", background=C["sunken"])
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.dim_tree.yview)
        self.dim_tree.configure(yscrollcommand=vs.set)
        self.dim_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.dim_tree.bind("<<TreeviewSelect>>", self._on_dim_select)

        # Live preview pane for the selected dimension (animated): a real
        # engineering callout (Ø cross-section or linear dimension) plus the
        # MIN / NOMINAL / MAX tolerance band. In its own resizable pane so
        # it can never be pushed off-screen.
        prev = _card(vpaned)
        vpaned.add(prev, minsize=96, height=150, stretch="never")
        tk.Label(prev, text="DIMENSION PREVIEW — drag the divider above to resize",
                 font=("Segoe UI Semibold", 9), fg=C["faint"], bg=C["surface"]
                 ).pack(anchor="w", padx=10, pady=(6, 0))
        self.dim_canvas = tk.Canvas(prev, bg=C["surface"], highlightthickness=0)
        self.dim_canvas.pack(fill="both", expand=True, padx=8, pady=(2, 6))
        self.dim_canvas.bind("<Configure>",
                             lambda e: self._draw_dim_band(animate=False))

    def _on_dim_select(self, _evt=None):
        self._draw_dim_band()

    def _dim_is_diameter(self, d: Dimension) -> bool:
        """Is this dimension a diameter (drawn as a Ø cross-section) rather
        than a linear length? Name markers first (Ø, dia, bore, OD, ID),
        then structural use: any fit's hole/shaft, or the radial stator ID /
        rotor OD -- those are diameters by definition."""
        if re.search(r"ø|⌀|\bdia\w*|\bbore\b|\bod\b|\bid\b",
                     d.name or "", re.IGNORECASE):
            return True
        for fit in self.model.fits:
            if d.id in (fit.hole_dim_id, fit.shaft_dim_id):
                return True
        r = self.model.radial
        return d.id in (r.stator_id_dim_id, r.rotor_od_dim_id)

    def _draw_dim_band(self, animate=True):
        """Selected dimension as a small engineering drawing: left, a real
        callout -- a Ø cross-section (circle + diameter arrows) or a linear
        dimension (part + extension lines + arrows), picked by
        _dim_is_diameter; right, the animated MIN / NOMINAL / MAX tolerance
        band. Everything is laid out from the live canvas size, so dragging
        the pane divider makes the whole preview bigger."""
        cv = self.dim_canvas
        cv.delete("all")
        w = max(cv.winfo_width(), 320)
        h = max(cv.winfo_height(), 92)
        sel = self.dim_tree.selection()
        d = self.model.dim_by_id(sel[0]) if sel and not sel[0].startswith("grp::") else None
        if d is None:
            cv.create_text(w / 2, h / 2, text="Select a dimension to preview it",
                           font=F_SMALL, fill=C["faint"])
            self._cancel_anim("dimband")
            return

        span = d.max - d.min
        cv.create_text(10, 11, text=d.name, font=F_UI_B, fill=C["ink"], anchor="w")
        cv.create_text(w - 10, 11, font=F_MONO, fill=C["muted"], anchor="e",
                       text=f"{d.nominal:.3f}  {d.tol_up:+.3f} / {d.tol_lo:+.3f}"
                            f"   (band {span:.3f})")

        body_top = 24
        cy = (body_top + h - 6) / 2
        is_dia = self._dim_is_diameter(d)
        anim = {}

        # ---- left: engineering callout ----
        if is_dia:
            r = max(12, min(w * 0.12, (h - body_top - 14) / 2 - 8))
            cx = 20 + r
            cv.create_text(max(cx, 46), cy - r - 8, text=f"Ø {d.nominal:.3f}",
                           font=F_MONO, fill=C["ink"])
            cv.create_line(cx - 4, cy, cx + 4, cy, fill=C["faint"])  # center marks
            cv.create_line(cx, cy - 4, cx, cy + 4, fill=C["faint"])
            arc = cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=90,
                                extent=0.0, style="arc",
                                outline=C["accent"], width=2)
            dia_ln = cv.create_line(cx, cy, cx, cy, arrow="both", fill=C["muted"])
            anim["dia"] = (arc, dia_ln, cx, cy, r)
        else:
            px0, px1 = w * 0.04, w * 0.30
            ph = max(10, min(24, h * 0.24))
            py0 = cy + 2
            cv.create_rectangle(px0, py0, px1, py0 + ph, fill=C["sunken"],
                                outline=C["border_strong"])
            yd = py0 - 14
            for xe in (px0, px1):                     # extension lines
                cv.create_line(xe, py0 - 2, xe, yd - 4, fill=C["faint"])
            cv.create_text((px0 + px1) / 2, yd - 8, text=f"{d.nominal:.3f}",
                           font=F_MONO, fill=C["ink"])
            mid = (px0 + px1) / 2
            lin_ln = cv.create_line(mid, yd, mid, yd, arrow="both", fill=C["muted"])
            anim["lin"] = (lin_ln, px0, px1, yd)

        # ---- right: tolerance band (min / nominal / max) ----
        x0, x1 = w * 0.40, w * 0.96
        lo_v = min(d.min, d.nominal)
        hi_v = max(d.max, d.nominal)
        rng = (hi_v - lo_v) or 1.0
        lo_v -= rng * 0.18
        hi_v += rng * 0.18

        def X(v):
            return x0 + (v - lo_v) / (hi_v - lo_v) * (x1 - x0)

        xn, xmin, xmax = X(d.nominal), X(d.min), X(d.max)
        bh = max(7, min(12, h * 0.12))                # band half-height
        cv.create_line(x0, cy, x1, cy, fill=C["border_strong"])
        cv.create_line(xn, cy - bh - 6, xn, cy + bh + 6, fill=C["ink"], width=2)
        cv.create_text(xn, cy + bh + 9, text=f"nom {d.nominal:.3f}",
                       font=F_SMALL, fill=C["ink"], anchor="n")
        band_id = cv.create_rectangle(xn, cy - bh, xn, cy + bh,
                                      fill=C["accent_soft"], outline=C["accent"])
        y_lbl = cy - bh - 8
        lmin = cv.create_text(xmin, y_lbl, text=f"min {d.min:.3f}",
                              font=F_SMALL, fill=C["accent"], anchor="s")
        lmax = cv.create_text(xmax, y_lbl, text=f"max {d.max:.3f}",
                              font=F_SMALL, fill=C["accent"], anchor="s")
        if abs(xmax - xmin) < 84:                     # keep the labels apart
            cv.coords(lmin, min(xmin, xn - 44), y_lbl)
            cv.coords(lmax, max(xmax, xn + 44), y_lbl)

        def frame(k):
            cv.coords(band_id, xn + (xmin - xn) * k, cy - bh,
                      xn + (xmax - xn) * k, cy + bh)
            if "dia" in anim:
                arc_, ln_, cx_, cy_, r_ = anim["dia"]
                cv.itemconfigure(arc_, extent=359.9 * k)
                cv.coords(ln_, cx_ - (r_ - 3) * k, cy_, cx_ + (r_ - 3) * k, cy_)
            if "lin" in anim:
                ln_, px0_, px1_, yd_ = anim["lin"]
                mid_ = (px0_ + px1_) / 2
                cv.coords(ln_, mid_ - (mid_ - px0_) * k, yd_,
                          mid_ + (px1_ - mid_) * k, yd_)

        def fin():
            try:
                frame(1.0)
            except tk.TclError:
                pass

        if animate and self._anims_on():
            self._animate("dimband", 420, frame, finalize=fin)
        else:
            self._cancel_anim("dimband")
            frame(1.0)

    # ---- assembly management -------------------------------------------- #
    def _all_groups(self) -> List[str]:
        gs = list(self.model.groups)
        for d in self.model.dimensions:
            if d.group not in gs:
                gs.append(d.group)
        return sorted(gs, key=str.lower)

    def _ask_choice(self, title, prompt, values, initial=""):
        """Small modal with one readonly combobox; returns the choice or None."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        win.configure(bg=C["surface"])
        tk.Label(win, text=prompt, font=F_UI, fg=C["ink"], bg=C["surface"]
                 ).pack(padx=16, pady=(14, 6), anchor="w")
        cb = ttk.Combobox(win, state="readonly", values=values, font=F_UI, width=30)
        if initial in values:
            cb.set(initial)
        elif values:
            cb.set(values[0])
        cb.pack(padx=16, pady=(0, 10), fill="x")
        out = {"value": None}
        row = tk.Frame(win, bg=C["surface"])
        row.pack(padx=16, pady=(0, 14), anchor="e")

        def ok():
            out["value"] = cb.get()
            win.destroy()

        _btn(row, "OK", ok, "primary").pack(side="left", padx=(0, 6))
        _btn(row, "Cancel", win.destroy).pack(side="left")
        try:
            win.wait_visibility()
            win.grab_set()
        except Exception:
            pass
        cb.focus_set()
        win.bind("<Return>", lambda e: ok())
        win.bind("<Escape>", lambda e: win.destroy())
        win.wait_window()
        return out["value"]

    def _add_assembly(self):
        name = simpledialog.askstring("Add Assembly", "Assembly name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self._all_groups():
            messagebox.showinfo("Add Assembly", f"Assembly '{name}' already exists.")
            return
        self.model.register_group(name)
        self._save()
        self.refresh_all()
        try:
            self.dim_tree.selection_set(f"grp::{name}")
            self.dim_tree.see(f"grp::{name}")
        except Exception:
            pass

    def _selected_assembly(self) -> Optional[str]:
        """The assembly under the selection: a group row directly, or the
        selected dimension's group."""
        sel = self.dim_tree.selection()
        if not sel:
            return None
        if sel[0].startswith("grp::"):
            return sel[0][len("grp::"):]
        d = self.model.dim_by_id(sel[0])
        return d.group if d else None

    def _rename_assembly(self):
        group = self._selected_assembly()
        if group is None:
            messagebox.showinfo("Rename Assembly", "Select an assembly row (or one of its dimensions) first.")
            return
        new = simpledialog.askstring("Rename Assembly", "New name:",
                                     initialvalue=group, parent=self)
        if not new or not new.strip() or new.strip() == group:
            return
        self.model.rename_group(group, new.strip())
        self._save()
        self.refresh_all()

    def _delete_assembly(self):
        group = self._selected_assembly()
        if group is None:
            messagebox.showinfo("Delete Assembly", "Select an assembly row (or one of its dimensions) first.")
            return
        members = [d for d in self.model.dimensions if d.group == group]
        if not members:
            if messagebox.askyesno("Delete Assembly", f"Delete empty assembly '{group}'?"):
                self.model.delete_group(group, delete_dims=False)
                self._save()
                self.refresh_all()
            return
        ans = messagebox.askyesnocancel(
            "Delete Assembly",
            f"Assembly '{group}' contains {len(members)} dimension(s).\n\n"
            f"Yes  = delete the assembly AND its dimensions\n"
            f"No   = delete the assembly, move its dimensions to 'General'\n"
            f"Cancel = keep everything")
        if ans is None:
            return
        self.model.delete_group(group, delete_dims=bool(ans))
        self._save()
        self.refresh_all()

    def _move_dim_to_assembly(self):
        sel = self.dim_tree.selection()
        if not sel or sel[0].startswith("grp::"):
            messagebox.showinfo("Move to Assembly", "Select a dimension first.")
            return
        d = self.model.dim_by_id(sel[0])
        if d is None:
            return
        target = self._ask_choice("Move to Assembly",
                                  f"Move '{d.name}' to assembly:",
                                  self._all_groups(), initial=d.group)
        if not target or target == d.group:
            return
        d.group = target
        self.model.register_group(target)
        self._save()
        self.refresh_all()
        try:
            self.dim_tree.item(f"grp::{target}", open=True)
            self.dim_tree.selection_set(d.id)
            self.dim_tree.see(d.id)
        except Exception:
            pass

    def _selected_group_or_default(self) -> str:
        sel = self.dim_tree.selection()
        if sel:
            row_id = sel[0]
            if row_id.startswith("grp::"):
                return row_id[len("grp::"):]
            d = self.model.dim_by_id(row_id)
            if d:
                return d.group
        return "General"

    def _add_dimension(self):
        group = self._selected_group_or_default()
        d = self.model.add_dimension(name="New Dimension", group=group)
        self._save()
        self.refresh_all()
        self._select_view("dims")
        try:
            self.dim_tree.item(f"grp::{group}", open=True)
        except Exception:
            pass
        self.dim_tree.selection_set(d.id)
        self.dim_tree.see(d.id)
        self.dim_tree.after_idle(lambda: self.dim_tree.edit_cell(d.id, "#0"))

    def _duplicate_dimension(self):
        sel = self.dim_tree.selection()
        if not sel or sel[0].startswith("grp::"):
            return
        d = self.model.duplicate_dimension(sel[0])
        if d is None:
            return
        self._save()
        self.refresh_all()
        self.dim_tree.selection_set(d.id)
        self.dim_tree.see(d.id)

    def _dimension_usage_count(self, dim_id) -> int:
        n = 0
        for f in self.model.fits:
            if f.hole_dim_id == dim_id or f.shaft_dim_id == dim_id:
                n += 1
        for s in self.model.stacks:
            n += sum(1 for l in s.links if l.dim_id == dim_id)
        if self.model.radial.stator_id_dim_id == dim_id or self.model.radial.rotor_od_dim_id == dim_id:
            n += 1
        return n

    def _delete_dimension(self):
        sel = self.dim_tree.selection()
        if not sel or sel[0].startswith("grp::"):
            return
        dim_id = sel[0]
        d = self.model.dim_by_id(dim_id)
        if d is None:
            return
        used = self._dimension_usage_count(dim_id)
        msg = f"Delete dimension '{d.name}'?"
        if used:
            msg += (f"\n\nIt is referenced by {used} fit/link/contributor entr"
                    f"{'y' if used == 1 else 'ies'}, which will show as invalid "
                    f"until you update them.")
        if not messagebox.askyesno("Delete Dimension", msg):
            return
        self.model.delete_dimension(dim_id)
        self._save()
        self.refresh_all()

    def _on_dim_cell_edit(self, row_id, col_id, new_value):
        if row_id.startswith("grp::"):
            return
        d = self.model.dim_by_id(row_id)
        if d is None:
            return
        if col_id == "#0":
            d.name = new_value.strip() or d.name
        elif col_id == "nominal":
            d.nominal = _to_float(new_value, d.nominal)
        elif col_id == "tol_up":
            d.tol_up = _to_float(new_value, d.tol_up)  # signed deviation
        elif col_id == "tol_lo":
            d.tol_lo = _to_float(new_value, d.tol_lo)  # signed deviation
        if d.tol_up < d.tol_lo:
            d.tol_up, d.tol_lo = d.tol_lo, d.tol_up
            messagebox.showwarning(
                "Tolerance",
                f"Upper tolerance must be ≥ lower tolerance for '{d.name}'.\n"
                f"The two values were swapped: {d.tol_up:+.3f} / {d.tol_lo:+.3f}.")
        self._save()
        self.refresh_all()

    def refresh_dimensions_tab(self):
        open_groups = {iid for iid in self.dim_tree.get_children()
                       if self.dim_tree.item(iid, "open")}
        sel = self.dim_tree.selection()
        for iid in self.dim_tree.get_children():
            self.dim_tree.delete(iid)

        groups: Dict[str, List[Dimension]] = {g: [] for g in self._all_groups()}
        for d in self.model.dimensions:
            groups.setdefault(d.group, []).append(d)

        for group in sorted(groups.keys(), key=str.lower):
            gid = f"grp::{group}"
            n = len(groups[group])
            self.dim_tree.insert("", "end", iid=gid,
                                  text=f"  {group}  ({n})" if n else f"  {group}  (empty)",
                                  values=("", "", "", "", ""), tags=("noedit",),
                                  open=(gid in open_groups or not open_groups))
            for i, d in enumerate(sorted(groups[group], key=lambda x: x.name.lower())):
                self.dim_tree.insert(gid, "end", iid=d.id, text=d.name,
                                      tags=("even" if i % 2 else "odd",),
                                      values=(
                    f"{d.nominal:.3f}", f"{d.tol_up:+.3f}", f"{d.tol_lo:+.3f}",
                    f"{d.min:.3f}", f"{d.max:.3f}"))
        if sel:
            try:
                self.dim_tree.selection_set(sel)
            except Exception:
                pass
        try:
            self._draw_dim_band(animate=False)
        except Exception:
            pass

    def _jump_to_dimension(self, dim_id):
        self._select_view("dims")
        d = self.model.dim_by_id(dim_id)
        if d is None:
            return
        try:
            self.dim_tree.item(f"grp::{d.group}", open=True)
        except Exception:
            pass
        try:
            self.dim_tree.selection_set(dim_id)
            self.dim_tree.see(dim_id)
        except Exception:
            pass

    # ================================================================== #
    #  Fits view (master table + detail editor)                          #
    # ================================================================== #
    def _build_fits_view(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        card = _card(parent)
        card.grid(row=0, column=0, sticky="nsew")

        toolbar = tk.Frame(card, bg=C["sunken"])
        toolbar.pack(fill="x")
        bar = WrapBar(toolbar)
        bar.pack(fill="x")
        bar.add(lambda p: _btn(p, "＋ Add Fit", self._add_fit, "primary"), padx=(10, 4), pady=8)
        bar.add(lambda p: _btn(p, "Delete Fit", self._delete_selected_fit, "danger"), padx=4, pady=8)
        tk.Label(toolbar, text="Select a fit to edit it in the panel on the right — set its Group there to file similar fits together",
                 font=F_SMALL, fg=C["faint"], bg=C["sunken"], anchor="w"
                 ).pack(fill="x", padx=12, pady=(0, 6))

        # -- table / editor split: a real drag-to-resize divider (same
        # tk.PanedWindow pattern as Axial and Radial) instead of the old
        # fixed-width editor column -- drag the sash left to blow up the
        # fit diagram / animation, right to give the table more room.
        body = tk.PanedWindow(card, orient=tk.HORIZONTAL, sashrelief=tk.RAISED,
                              sashwidth=6, bg=C["border_strong"],
                              bd=0, opaqueresize=True)
        body.pack(fill="both", expand=True)

        leftf = tk.Frame(body, bg=C["surface"])
        body.add(leftf, minsize=340, width=720, stretch="always")

        # color legend, so the class colors are self-explanatory (packed
        # bottom-first so a shrinking window squeezes the table, not it)
        legend = tk.Frame(leftf, bg=C["surface"])
        legend.pack(side="bottom", fill="x", padx=1, pady=(0, 1))
        tk.Label(legend, text="Fit classes:", font=F_SMALL, fg=C["faint"],
                 bg=C["surface"]).pack(side="left", padx=(12, 8), pady=4)
        for cls, note in (("Clearance", "always a gap"),
                          ("Transition", "gap or grip"),
                          ("Interference", "always a grip / press"),
                          ("Invalid", "missing dimension")):
            fg, bg = CLS_COLORS[cls]
            tk.Label(legend, text=f"● {cls} — {note}", font=F_SMALL,
                     fg=fg, bg=bg, padx=8, pady=1).pack(side="left", padx=3, pady=4)

        wrap = tk.Frame(leftf, bg=C["surface"])
        wrap.pack(side="top", fill="both", expand=True, padx=1, pady=1)
        self.fits_tree = ttk.Treeview(
            wrap, columns=("hole", "shaft", "mn", "mx", "cls"),
            show="tree headings", style="Tol.Treeview", height=14)
        self.fits_tree.heading("#0", text="FIT / GROUP")
        self.fits_tree.column("#0", width=210, anchor="w")
        for cid, text, w, anchor in (
                ("hole", "HOLE DIMENSION", 190, "w"),
                ("shaft", "SHAFT DIMENSION", 190, "w"),
                ("mn", "MIN CLEAR", 95, "e"), ("mx", "MAX CLEAR", 95, "e"),
                ("cls", "CLASS", 130, "center")):
            self.fits_tree.heading(cid, text=text)
            self.fits_tree.column(cid, width=w, anchor=anchor)
        # Strong class color on the text + soft tint behind it, so a fit's
        # type is identifiable at a glance (user feedback: colors per fit type).
        for cls, (fg, bg) in CLS_COLORS.items():
            self.fits_tree.tag_configure(f"cls_{cls}", foreground=fg, background=bg)
        self.fits_tree.tag_configure("fitgrp", background=C["sunken"],
                                     foreground=C["muted"],
                                     font=("Segoe UI Semibold", 10))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.fits_tree.yview)
        self.fits_tree.configure(yscrollcommand=vs.set)
        self.fits_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.fits_tree.bind("<<TreeviewSelect>>", self._on_fit_select)

        # -- detail editor (resizable via the paned sash) --
        editor = tk.Frame(body, bg=C["surface"])
        body.add(editor, minsize=280, width=320, stretch="never")

        pad = dict(anchor="w", padx=16)
        tk.Label(editor, text="FIT DETAILS", font=("Segoe UI Semibold", 9),
                 fg=C["faint"], bg=C["surface"]).pack(pady=(14, 8), **pad)

        tk.Label(editor, text="Name", font=F_SMALL, fg=C["muted"], bg=C["surface"]).pack(**pad)
        self.fit_name_entry = tk.Entry(editor, font=F_UI, relief="solid", bd=1,
                                       bg=C["surface"], fg=C["ink"],
                                       highlightthickness=1,
                                       highlightbackground=C["border"],
                                       highlightcolor=C["accent"])
        self.fit_name_entry.pack(fill="x", padx=16, pady=(2, 10))
        self.fit_name_entry.bind("<Return>", self._commit_fit_editor)
        self.fit_name_entry.bind("<FocusOut>", self._commit_fit_editor)

        tk.Label(editor, text="Group (type a new name to create one)", font=F_SMALL,
                 fg=C["muted"], bg=C["surface"]).pack(**pad)
        self.fit_group_cb = ttk.Combobox(editor, font=F_UI)  # editable
        self.fit_group_cb.pack(fill="x", padx=16, pady=(2, 10))
        self.fit_group_cb.bind("<<ComboboxSelected>>", self._commit_fit_editor)
        self.fit_group_cb.bind("<Return>", self._commit_fit_editor)
        self.fit_group_cb.bind("<FocusOut>", self._commit_fit_editor)

        tk.Label(editor, text="Hole dimension (bore / ID)", font=F_SMALL,
                 fg=C["muted"], bg=C["surface"]).pack(**pad)
        self.fit_hole_cb = ttk.Combobox(editor, state="readonly", font=F_UI)
        self.fit_hole_cb.pack(fill="x", padx=16, pady=(2, 10))
        self.fit_hole_cb.bind("<<ComboboxSelected>>", self._commit_fit_editor)

        tk.Label(editor, text="Shaft dimension (OD)", font=F_SMALL,
                 fg=C["muted"], bg=C["surface"]).pack(**pad)
        self.fit_shaft_cb = ttk.Combobox(editor, state="readonly", font=F_UI)
        self.fit_shaft_cb.pack(fill="x", padx=16, pady=(2, 14))
        self.fit_shaft_cb.bind("<<ComboboxSelected>>", self._commit_fit_editor)

        self.fit_badge = tk.Label(editor, text="—", font=F_UI_B, padx=12, pady=4,
                                  fg=C["muted"], bg=C["sunken"])
        self.fit_badge.pack(**pad)
        self.fit_clear_lbl = tk.Label(editor, text="", font=F_MONO_B,
                                      fg=C["ink"], bg=C["surface"], justify="left")
        self.fit_clear_lbl.pack(pady=(10, 0), **pad)

        # Animated fit diagram: staged limits-and-fits figure (zero line ->
        # hole zone -> shaft zone -> clearance arrows) built from the basic
        # dimensions, so a new user can SEE where the fit numbers come from.
        drow = tk.Frame(editor, bg=C["surface"])
        drow.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(drow, text="FIT DIAGRAM", font=("Segoe UI Semibold", 9),
                 fg=C["faint"], bg=C["surface"]).pack(side="left")
        _btn(drow, "▶ Animate", self._replay_fit_diagram, padx=8, pady=1
             ).pack(side="right")
        # vertical zoom of the zone plot: ＋ stretches the deviation axis so
        # tight zones and their labels spread apart, − compresses it
        _btn(drow, "＋", lambda: self._fit_vzoom_step(1.25), padx=7, pady=1
             ).pack(side="right", padx=(0, 4))
        _btn(drow, "－", lambda: self._fit_vzoom_step(0.8), padx=7, pady=1
             ).pack(side="right", padx=(0, 4))
        self.fit_canvas = tk.Canvas(editor, width=264, height=224,
                                    bg=C["surface"], highlightthickness=1,
                                    highlightbackground=C["border"])
        # fills whatever space the sash gives the editor, and the drawing
        # rescales with it -- drag the divider left to watch the animation
        # up close
        self.fit_canvas.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        self.fit_canvas.bind("<Configure>", self._on_fit_canvas_resize)

        tk.Label(editor,
                 text="max clear = hole.max − shaft.min\nmin clear = hole.min − shaft.max",
                 font=F_SMALL, fg=C["faint"], bg=C["surface"],
                 justify="left").pack(pady=(10, 0), **pad)
        self._fit_editor_placeholder = tk.Label(
            editor, text="", font=F_SMALL, fg=C["faint"], bg=C["surface"])
        self._fit_editor_placeholder.pack(**pad)

    def _selected_fit(self) -> Optional[Fit]:
        sel = self.fits_tree.selection()
        if not sel:
            return None
        return next((f for f in self.model.fits if f.id == sel[0]), None)

    def _add_fit(self):
        # new fit lands in the group under the current selection
        group = "General"
        sel = self.fits_tree.selection()
        if sel:
            if sel[0].startswith("fgrp::"):
                group = sel[0][len("fgrp::"):]
            else:
                f0 = self._selected_fit()
                if f0:
                    group = f0.group
        f = self.model.add_fit(name=f"Fit {len(self.model.fits) + 1}", group=group)
        self._save()
        self.refresh_all()
        try:
            self.fits_tree.item(f"fgrp::{group}", open=True)
            self.fits_tree.selection_set(f.id)
            self.fits_tree.see(f.id)
        except Exception:
            pass

    def _delete_selected_fit(self):
        f = self._selected_fit()
        if f is None:
            return
        if not messagebox.askyesno("Delete Fit", f"Delete fit '{f.name}'?"):
            return
        self.model.delete_fit(f.id)
        self._save()
        self.refresh_all()

    def _delete_fit(self, fid):  # back-compat entry point
        self.model.delete_fit(fid)
        self._save()
        self.refresh_all()

    def _on_fit_select(self, _evt=None):
        self._load_fit_editor()

    def _load_fit_editor(self):
        self._loading_editor = True
        try:
            f = self._selected_fit()
            names = sorted(self._dim_name_map().keys())
            self.fit_hole_cb["values"] = names
            self.fit_shaft_cb["values"] = names
            fit_groups = sorted({x.group or "General" for x in self.model.fits} | {"General"},
                                key=str.lower)
            self.fit_group_cb["values"] = fit_groups
            if f is None:
                self.fit_name_entry.delete(0, tk.END)
                self.fit_group_cb.set("")
                self.fit_hole_cb.set("")
                self.fit_shaft_cb.set("")
                self.fit_badge.configure(text="—", fg=C["muted"], bg=C["sunken"])
                self.fit_clear_lbl.configure(text="")
                self._fit_editor_placeholder.configure(
                    text="Select a fit in the table,\nor click “＋ Add Fit”.")
                self._draw_fit_diagram(None, animate=False)
                return
            self._fit_editor_placeholder.configure(text="")
            id_to_disp = self._dim_id_to_display()
            self.fit_name_entry.delete(0, tk.END)
            self.fit_name_entry.insert(0, f.name)
            self.fit_group_cb.set(f.group or "General")
            self.fit_hole_cb.set(id_to_disp.get(f.hole_dim_id, ""))
            self.fit_shaft_cb.set(id_to_disp.get(f.shaft_dim_id, ""))

            dims = self.model.dims_by_id()
            cls = f.classification(dims)
            fg, bg = CLS_COLORS[cls]
            self.fit_badge.configure(text=cls.upper(), fg=fg, bg=bg)
            self.fit_clear_lbl.configure(
                text=f"min clear  {_fmt(f.min_clear(dims))}\n"
                     f"max clear  {_fmt(f.max_clear(dims))}")
            self._draw_fit_diagram(f)
        finally:
            self._loading_editor = False

    def _commit_fit_editor(self, _evt=None):
        if self._loading_editor:
            return
        f = self._selected_fit()
        if f is None:
            return
        f.name = self.fit_name_entry.get().strip() or f.name
        f.group = self.fit_group_cb.get().strip() or f.group or "General"
        names = self._dim_name_map()
        hole = names.get(self.fit_hole_cb.get())
        shaft = names.get(self.fit_shaft_cb.get())
        if hole:
            f.hole_dim_id = hole
        if shaft:
            f.shaft_dim_id = shaft
        self._save()
        self.refresh_all()
        try:
            self.fits_tree.selection_set(f.id)
        except Exception:
            pass

    # ---- animated fit diagram ------------------------------------------- #
    def _replay_fit_diagram(self):
        self._fitdiag_sig = None  # force the animation to play again
        self._draw_fit_diagram(self._selected_fit(), animate=True)

    def _fit_vzoom_step(self, factor):
        """＋ / − buttons: stretch or compress the deviation axis of the fit
        diagram (static redraw -- no replay of the staged animation)."""
        self._fit_vzoom = max(0.5, min(3.0, getattr(self, "_fit_vzoom", 1.0) * factor))
        self._draw_fit_diagram(self._selected_fit(), animate=False)

    def _on_fit_canvas_resize(self, _evt=None):
        """Redraw at the new canvas size (the sash is draggable). Static:
        the sig check keeps the slide animation from replaying on every
        drag tick."""
        try:
            self._draw_fit_diagram(self._selected_fit(), animate=False)
        except Exception:
            pass

    def _draw_fit_diagram(self, f: Optional[Fit], animate=True):
        """Textbook limits-and-fits figure for the selected fit, revealed in
        teaching stages so a new user can SEE how the fit comes out of the
        basic dimensions: (1) the zero line = the basic size Ø, (2) the HOLE
        tolerance zone grows from its two deviations (edge values + the
        resulting limit sizes shown), (3) the SHAFT zone likewise, (4)
        dimension arrows measure between the zone edges -- hole MIN vs shaft
        MAX gives min clearance, hole MAX vs shaft MIN gives max clearance --
        and the classification + a plain-words conclusion with the real
        numbers appears. Static (final frame) when animations are off."""
        cv = getattr(self, "fit_canvas", None)
        if cv is None:
            return
        self._cancel_anim("fitdiag")
        cv.delete("all")
        # the canvas lives in a resizable pane -- lay out proportionally to
        # its real size (fall back to the design size before first map)
        W = max(cv.winfo_width(), 240)
        H = max(cv.winfo_height(), 210)
        s = max(1.0, min(W / 264.0, H / 224.0))  # font scale, up only
        f_tag = ("Segoe UI Semibold", round(10 * s))
        f_lbl = ("Segoe UI Semibold", round(8 * s))
        f_tiny = ("Segoe UI", round(7 * s))
        f_dev = ("Consolas", round(7 * s))
        if f is None:
            self._fitdiag_sig = None
            cv.create_text(W / 2, H / 2, text="Select a fit to see its diagram",
                           font=F_SMALL, fill=C["faint"])
            return
        dims = self.model.dims_by_id()
        hole, shaft = dims.get(f.hole_dim_id), dims.get(f.shaft_dim_id)
        if hole is None or shaft is None:
            self._fitdiag_sig = None
            cv.create_text(W / 2, H / 2, justify="center", font=F_SMALL,
                           fill=C["faint"],
                           text="Pick both a hole and a shaft\ndimension to draw the fit")
            return
        # Only replay the entry animation when the picture actually changed
        # (refresh_all reloads this editor after EVERY edit anywhere).
        sig = (f.id, round(hole.min, 6), round(hole.max, 6),
               round(shaft.min, 6), round(shaft.max, 6))
        if sig == getattr(self, "_fitdiag_sig", None):
            animate = False
        self._fitdiag_sig = sig
        cls = f.classification(dims)
        fg, bg = CLS_COLORS[cls]
        mn, mx = f.min_clear(dims), f.max_clear(dims)

        # ---- geometry: deviations from the BASIC SIZE (hole nominal) ----
        ref = hole.nominal
        eh_hi, eh_lo = hole.max - ref, hole.min - ref    # hole ES / EI
        es_hi, es_lo = shaft.max - ref, shaft.min - ref  # shaft es / ei
        devs = [eh_hi, eh_lo, es_hi, es_lo, 0.0]
        lo, hi = min(devs), max(devs)
        rng = (hi - lo) or 1.0
        # ± / − buttons scale the deviation axis: more zoom = less padding =
        # taller zones = more room between the edge value labels
        vz = max(0.5, min(3.0, getattr(self, "_fit_vzoom", 1.0)))
        lo, hi = lo - rng * 0.22 / vz, hi + rng * 0.22 / vz
        top, bot = round(14 + 10 * s), round(H * 0.58)

        def Y(v):
            return bot - (v - lo) / (hi - lo) * (bot - top)

        y0 = Y(0.0)
        hx0, hx1 = round(W * 0.10), round(W * 0.34)      # hole zone
        sx0, sx1 = round(W * 0.58), round(W * 0.82)      # shaft zone
        xa1, xa2 = round(W * 0.46), round(W * 0.91)      # min / max arrows
        x_beg, x_end = round(W * 0.04), round(W * 0.96)
        SURF = C["surface"]

        def val_color(v):
            if v is None:
                return C["muted"]
            return C["good"] if v > 1e-9 else (C["warn"] if v < -1e-9 else C["muted"])

        # ---- create every element at final geometry; the staged frame() ----
        # ---- below reveals them in teaching order                       ----
        # stage 1: the zero line IS the basic dimension
        zero_ln = cv.create_line(x_beg, y0, x_beg, y0, fill=C["border_strong"],
                                 dash=(4, 2))
        zero_lbl = cv.create_text(x_beg + 2, y0 - 7 * s, anchor="w", font=f_tiny,
                                  fill=SURF,
                                  text=f"0 line = basic size Ø {ref:.3f} mm")

        # stage 2/3: each tolerance zone grows out of its deviations
        hole_rc = cv.create_rectangle(hx0, y0, hx1, y0, fill=C["info_soft"],
                                      outline=C["info"], width=1.4, state="hidden")
        shaft_rc = cv.create_rectangle(sx0, y0, sx1, y0, fill=bg,
                                       outline=fg, width=1.4, state="hidden")

        fades = []  # (canvas item, final color, stage 1..4)
        small = [zero_lbl]  # tiny value labels -> de-overlapped at the end
        fades.append((zero_lbl, C["muted"], 1))

        for name, zfg, x0, x1, d_hi, d_lo, d0, stage in (
                ("HOLE (bore)", C["info"], hx0, hx1, eh_hi, eh_lo, hole, 2),
                ("SHAFT", fg, sx0, sx1, es_hi, es_lo, shaft, 3)):
            xm = (x0 + x1) / 2
            fades.append((cv.create_text(xm, bot + 11 * s, font=f_lbl,
                                         fill=SURF, text=name), zfg, stage))
            # how the zone is made: basic size + the two deviations...
            fades.append((cv.create_text(xm, bot + 23 * s, font=f_dev, fill=SURF,
                                         text=f"{d_hi:+.3f} / {d_lo:+.3f}"),
                          C["muted"], stage))
            # ...gives the limit sizes
            fades.append((cv.create_text(xm, bot + 34 * s, font=f_dev, fill=SURF,
                                         text=f"Ø {d0.min:.3f} … {d0.max:.3f}"),
                          C["ink"], stage))
            # deviation values right at the zone edges (one label when the
            # zone is a single line, so nothing prints twice on itself)
            edge_devs = (d_hi,) if abs(d_hi - d_lo) < 1e-9 else (d_hi, d_lo)
            for dv in edge_devs:
                t = cv.create_text(x0 - 3, Y(dv), font=f_dev, fill=SURF,
                                   anchor="e", text=f"{dv:+.3f}")
                fades.append((t, zfg, stage))
                small.append(t)

        # stage 4: measure between the zone edges -> min / max clearance
        yA1a, yA1b = Y(eh_lo), Y(es_hi)   # tightest pair: hole MIN vs shaft MAX
        yA2a, yA2b = Y(eh_hi), Y(es_lo)   # loosest pair: hole MAX vs shaft MIN
        ext_lines = []
        for xz, yz, xa in ((hx1, yA1a, xa1), (sx0, yA1b, xa1),
                           (hx1, yA2a, xa2), (sx1, yA2b, xa2)):
            ext_lines.append(cv.create_line(xz, yz, xa, yz, fill=SURF,
                                            dash=(2, 3), state="hidden"))
        ar1 = cv.create_line(xa1, yA1a, xa1, yA1a, arrow="both", fill=SURF,
                             width=1.4, state="hidden")
        ar2 = cv.create_line(xa2, yA2a, xa2, yA2a, arrow="both", fill=SURF,
                             width=1.4, state="hidden")
        ar1_lbl = cv.create_text(xa1, max(yA1a, yA1b) + 5 * s, anchor="n",
                                 font=f_dev, fill=SURF, text=f"min {_fmt(mn)}")
        ar2_lbl = cv.create_text(xa2, min(yA2a, yA2b) - 4 * s, anchor="s",
                                 font=f_dev, fill=SURF, text=f"max {_fmt(mx)}")
        fades.append((ar1_lbl, val_color(mn), 4))
        fades.append((ar2_lbl, val_color(mx), 4))
        small.extend((ar1_lbl, ar2_lbl))
        # tiny zones / clearances stack their value labels -- push them apart
        _spread_labels(cv, small)

        # conclusion: classification + what it means, with the real numbers
        fades.append((cv.create_text(W / 2, 7 + 6 * s, font=f_tag, fill=SURF,
                                     text=f"{cls.upper()} FIT"), fg, 4))
        expl = {
            "Clearance": (f"Shaft is always smaller than the hole — gap between "
                          f"{_fmt(mn)} and {_fmt(mx)} mm: parts slide / rotate."),
            "Transition": (f"Zones overlap — an actual pair lands anywhere from "
                           f"{_fmt(mn)} mm (press) to {_fmt(mx)} mm (gap)."),
            "Interference": (f"Shaft is always larger than the hole — squeeze of "
                             f"{_fmt(-(mx or 0))} to {_fmt(-(mn or 0))} mm: press / shrink fit."),
            "Invalid": "",
        }[cls]
        fades.append((cv.create_text(W / 2, H - 5, anchor="s", font=f_tiny,
                                     fill=SURF, width=W - 14, justify="center",
                                     text=f"gap = hole − shaft.  {expl}"),
                      C["muted"], 4))

        # ---- staged reveal: zero line -> hole zone -> shaft zone -> arrows ----
        def ramp(k, a, b):
            return 0.0 if k <= a else (1.0 if k >= b else (k - a) / (b - a))

        def frame(k):
            ps = (ramp(k, 0.00, 0.20), ramp(k, 0.16, 0.44),
                  ramp(k, 0.42, 0.70), ramp(k, 0.70, 1.00))
            cv.coords(zero_ln, x_beg, y0, x_beg + (x_end - x_beg) * ps[0], y0)
            for rc, p, yt, yb in ((hole_rc, ps[1], Y(eh_hi), Y(eh_lo)),
                                  (shaft_rc, ps[2], Y(es_hi), Y(es_lo))):
                cv.itemconfigure(rc, state="hidden" if p <= 0 else "normal")
                x0, _, x1, _ = cv.coords(rc)
                cv.coords(rc, x0, y0 + (yt - y0) * p, x1, y0 + (yb - y0) * p)
            p4 = ps[3]
            for e in ext_lines:
                cv.itemconfigure(e, state="hidden" if p4 <= 0 else "normal",
                                 fill=_blend(SURF, C["faint"], p4))
            for ar, ya, yb_, v in ((ar1, yA1a, yA1b, mn), (ar2, yA2a, yA2b, mx)):
                cv.itemconfigure(ar, state="hidden" if p4 <= 0 else "normal",
                                 fill=_blend(SURF, val_color(v), p4))
                cv.coords(ar, cv.coords(ar)[0], ya,
                          cv.coords(ar)[0], ya + (yb_ - ya) * p4)
            for item, colr, stage in fades:
                cv.itemconfigure(item, fill=_blend(SURF, colr, ps[stage - 1]))

        if animate and self._anims_on():
            def fin():
                try:
                    frame(1.0)
                except tk.TclError:
                    pass
            self._animate("fitdiag", 1500, frame, finalize=fin)
        else:
            frame(1.0)

    def refresh_fits_tab(self):
        open_groups = {iid for iid in self.fits_tree.get_children()
                       if self.fits_tree.item(iid, "open")}
        sel = self.fits_tree.selection()
        for iid in self.fits_tree.get_children():
            self.fits_tree.delete(iid)
        dims = self.model.dims_by_id()
        id_to_disp = self._dim_id_to_display()

        groups: Dict[str, List[Fit]] = {}
        for f in self.model.fits:
            groups.setdefault(f.group or "General", []).append(f)

        for group in sorted(groups.keys(), key=str.lower):
            gid = f"fgrp::{group}"
            self.fits_tree.insert("", "end", iid=gid,
                                   text=f"  {group}  ({len(groups[group])})",
                                   values=("", "", "", "", ""), tags=("fitgrp",),
                                   open=(gid in open_groups or not open_groups))
            for f in sorted(groups[group], key=lambda x: x.name.lower()):
                cls = f.classification(dims)
                self.fits_tree.insert(gid, "end", iid=f.id, text=f.name,
                                       tags=(f"cls_{cls}",),
                                       values=(
                    id_to_disp.get(f.hole_dim_id, "(missing)"),
                    id_to_disp.get(f.shaft_dim_id, "(missing)"),
                    _fmt(f.min_clear(dims)), _fmt(f.max_clear(dims)),
                    f"● {cls}"))
        if sel:
            try:
                self.fits_tree.selection_set(sel)
            except Exception:
                pass
        self._load_fit_editor()

    # ================================================================== #
    #  Axial stack-up view                                                #
    # ================================================================== #
    def _build_axial_view(self, parent):
        # -- stack selector row --
        selrow = _card(parent)
        selrow.pack(fill="x", pady=(0, 10))
        inner = WrapBar(selrow, bg=C["surface"])
        inner.pack(fill="x", padx=10, pady=8)
        inner.add(lambda p: tk.Label(p, text="Stack", font=F_UI_B, fg=C["muted"],
                                     bg=C["surface"]), padx=(4, 8))

        def _mk_stack_cb(p):
            cb = ttk.Combobox(p, state="readonly", width=30, font=F_UI)
            cb.bind("<<ComboboxSelected>>", self._on_stack_selected)
            return cb
        self.stack_cb = inner.add(_mk_stack_cb, padx=(0, 10))
        inner.add(lambda p: _btn(p, "＋ Add", self._add_stack), padx=3)
        inner.add(lambda p: _btn(p, "Rename", self._rename_stack), padx=3)
        inner.add(lambda p: _btn(p, "Delete", self._delete_stack, "danger"), padx=(3, 24))

        inner.add(lambda p: tk.Label(p, text="Method", font=F_UI_B, fg=C["muted"],
                                     bg=C["surface"]), padx=(0, 8))

        def _mk_stack_seg(p):
            return Segmented(p, ["WC", "RSS"], command=self._on_stack_method_change)
        self.stack_method_seg = inner.add(_mk_stack_seg, padx=(0, 4))

        # -- chain editor / result+chart split: a real drag-to-resize divider
        # (same tk.PanedWindow pattern the host app uses), not a fixed-width
        # column -- drag it right to make the waterfall chart bigger, drag
        # it left to give the link table more room. Each pane remembers
        # nothing between sessions; it just starts at a sensible width.
        paned = tk.PanedWindow(parent, orient=tk.HORIZONTAL, sashrelief=tk.RAISED,
                               sashwidth=6, bg=C["border_strong"],
                               bd=0, opaqueresize=True)
        paned.pack(fill="both", expand=True)

        # -- left: chain editor --
        left = _card(paned)
        paned.add(left, minsize=320, width=560, stretch="always")

        # This bar shares the window with a resizable result/chart pane, so
        # its available width varies as the user drags the sash -- use a
        # conservative threshold rather than assuming generous space.
        lbar = WrapBar(left, min_row_width=380)
        lbar.pack(fill="x")
        self.add_link_cb = lbar.add(
            lambda p: ttk.Combobox(p, state="readonly", width=26, font=F_UI),
            padx=(10, 4), pady=8)
        lbar.add(lambda p: _btn(p, "＋ Add Link", self._add_link), padx=(3, 12), pady=8)
        lbar.add(lambda p: _btn(p, "▲", lambda: self._move_link(-1), padx=8), padx=(0, 2), pady=8)
        lbar.add(lambda p: _btn(p, "▼", lambda: self._move_link(1), padx=8), padx=2, pady=8)
        lbar.add(lambda p: _btn(p, "±", self._toggle_link_sense, padx=8), padx=2, pady=8)
        lbar.add(lambda p: _btn(p, "✕", self._remove_link, "danger", padx=8), padx=(2, 12), pady=8)
        lbar.add(lambda p: _btn(p, "Edit Tol…", self._jump_to_dim_from_link), padx=2, pady=8)

        lwrap = tk.Frame(left, bg=C["surface"])
        lwrap.pack(fill="both", expand=True, padx=1, pady=1)
        self.link_tree = ttk.Treeview(
            lwrap, columns=("sense", "dim", "contribution", "mn", "mx"),
            show="headings", style="Tol.Treeview", height=10)
        for cid, text, w, anchor in (
                ("sense", "±", 44, "center"), ("dim", "DIMENSION", 260, "w"),
                ("contribution", "± NOMINAL", 105, "e"),
                ("mn", "MIN", 90, "e"), ("mx", "MAX", 90, "e")):
            self.link_tree.heading(cid, text=text)
            self.link_tree.column(cid, width=w, anchor=anchor)
        self.link_tree.tag_configure("pos", foreground=C["good"])
        self.link_tree.tag_configure("neg", foreground=C["warn"])
        lvs = ttk.Scrollbar(lwrap, orient="vertical", command=self.link_tree.yview)
        self.link_tree.configure(yscrollcommand=lvs.set)
        self.link_tree.pack(side="left", fill="both", expand=True)
        lvs.pack(side="right", fill="y")

        tk.Label(left, text="＋ links grow the gap · − links consume it · ± flips the selected link",
                 font=F_SMALL, fg=C["faint"], bg=C["surface"]).pack(anchor="w", padx=12, pady=(0, 8))

        # -- right: result + chart --
        right = tk.Frame(paned, bg=C["bg"])
        paned.add(right, minsize=300, width=380, stretch="always")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        res = _card(right)
        res.grid(row=0, column=0, sticky="ew")
        rin = tk.Frame(res, bg=C["surface"])
        rin.pack(fill="x", padx=14, pady=12)
        tk.Label(rin, text="RESULT", font=("Segoe UI Semibold", 9),
                 fg=C["faint"], bg=C["surface"]).grid(row=0, column=0, sticky="w")
        self.stack_pass_lbl = tk.Label(rin, text="—", font=F_UI_B, padx=14, pady=3,
                                       fg=C["muted"], bg=C["sunken"])
        self.stack_pass_lbl.grid(row=0, column=1, sticky="e")
        rin.grid_columnconfigure(0, weight=1)

        self.stack_nom_lbl = tk.Label(rin, text="—", font=F_MONO_XL,
                                      fg=C["ink"], bg=C["surface"])
        self.stack_nom_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.stack_range_lbl = tk.Label(rin, text="", font=F_MONO_B,
                                        fg=C["muted"], bg=C["surface"])
        self.stack_range_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))

        trow = tk.Frame(rin, bg=C["surface"])
        trow.grid(row=3, column=0, columnspan=2, sticky="w")
        tk.Label(trow, text="Target", font=F_SMALL, fg=C["muted"],
                 bg=C["surface"]).pack(side="left", padx=(0, 6))
        self.target_min_entry = tk.Entry(trow, width=8, font=F_MONO, relief="solid",
                                         bd=1, justify="right", bg=C["surface"],
                                         highlightthickness=1,
                                         highlightbackground=C["border"],
                                         highlightcolor=C["accent"])
        self.target_min_entry.pack(side="left")
        tk.Label(trow, text="…", font=F_SMALL, fg=C["faint"],
                 bg=C["surface"]).pack(side="left", padx=4)
        self.target_max_entry = tk.Entry(trow, width=8, font=F_MONO, relief="solid",
                                         bd=1, justify="right", bg=C["surface"],
                                         highlightthickness=1,
                                         highlightbackground=C["border"],
                                         highlightcolor=C["accent"])
        self.target_max_entry.pack(side="left")
        tk.Label(trow, text="mm", font=F_SMALL, fg=C["faint"],
                 bg=C["surface"]).pack(side="left", padx=6)
        for e in (self.target_min_entry, self.target_max_entry):
            e.bind("<Return>", self._on_target_change)
            e.bind("<FocusOut>", self._on_target_change)

        chart = _card(right)
        chart.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        tk.Label(chart, text="CHAIN WATERFALL", font=("Segoe UI Semibold", 9),
                 fg=C["faint"], bg=C["surface"]).pack(anchor="w", padx=14, pady=(10, 0))
        if HAVE_MPL:
            self.chain_fig = Figure(figsize=(3.7, 3.0), dpi=100,
                                    facecolor=C["surface"])
            self.chain_ax = self.chain_fig.add_subplot(111)
            self.chain_canvas = FigureCanvasTkAgg(self.chain_fig, master=chart)
            self.chain_canvas.get_tk_widget().configure(bg=C["surface"])
            self.chain_canvas.get_tk_widget().pack(fill="both", expand=True,
                                                   padx=8, pady=8)
        else:
            tk.Label(chart, text="matplotlib not installed —\nchain chart unavailable.",
                     fg=C["warn"], bg=C["surface"], font=F_SMALL,
                     justify="center").pack(padx=10, pady=24)

    def _on_stack_selected(self, _evt=None):
        sid = self._stack_name_map().get(self.stack_cb.get())
        if sid:
            self.active_stack_id = sid
            self.refresh_stack_tab()

    def _add_stack(self):
        s = self.model.add_stack(name=f"Stack {len(self.model.stacks) + 1}")
        self.active_stack_id = s.id
        self._save()
        self.refresh_all()

    def _rename_stack(self):
        s = self._active_stack()
        if s is None:
            return
        new = simpledialog.askstring("Rename Stack", "Name:", initialvalue=s.name, parent=self)
        if new:
            s.name = new.strip() or s.name
            self._save()
            self.refresh_all()

    def _delete_stack(self):
        s = self._active_stack()
        if s is None:
            return
        if not messagebox.askyesno("Delete Stack", f"Delete stack '{s.name}'?"):
            return
        self.model.delete_stack(s.id)
        self.active_stack_id = self.model.stacks[0].id if self.model.stacks else None
        self._save()
        self.refresh_all()

    def _selected_link_id(self):
        sel = self.link_tree.selection()
        return sel[0] if sel else None

    def _add_link(self):
        s = self._active_stack()
        if s is None:
            return
        dim_id = self._dim_name_map().get(self.add_link_cb.get())
        if not dim_id:
            return
        self.model.add_link(s, dim_id, sense=1)
        self._save()
        self.refresh_stack_tab()

    def _remove_link(self):
        s, lid = self._active_stack(), self._selected_link_id()
        if s is None or lid is None:
            return
        if not messagebox.askyesno("Remove Link", "Remove this link from the stack?"):
            return
        self.model.remove_link(s, lid)
        self._save()
        self.refresh_stack_tab()

    def _move_link(self, delta):
        s, lid = self._active_stack(), self._selected_link_id()
        if s is None or lid is None:
            return
        self.model.move_link(s, lid, delta)
        self._save()
        self.refresh_stack_tab()
        try:
            self.link_tree.selection_set(lid)
        except Exception:
            pass

    def _toggle_link_sense(self):
        s, lid = self._active_stack(), self._selected_link_id()
        if s is None or lid is None:
            return
        self.model.toggle_sense(s, lid)
        self._save()
        self.refresh_stack_tab()
        try:
            self.link_tree.selection_set(lid)
        except Exception:
            pass

    def _jump_to_dim_from_link(self):
        s, lid = self._active_stack(), self._selected_link_id()
        if s is None or lid is None:
            return
        link = next((l for l in s.links if l.id == lid), None)
        if link is None:
            return
        self._jump_to_dimension(link.dim_id)

    def _on_stack_method_change(self, _value=None):
        s = self._active_stack()
        if s is None:
            return
        s.method = self.stack_method_seg.get()
        self._save()
        self.refresh_stack_tab()

    def _on_target_change(self, _evt=None):
        s = self._active_stack()
        if s is None:
            return
        tmin_txt = self.target_min_entry.get().strip()
        tmax_txt = self.target_max_entry.get().strip()
        s.target_min = _to_float(tmin_txt, None) if tmin_txt else None
        s.target_max = _to_float(tmax_txt, None) if tmax_txt else None
        self._save()
        self.refresh_stack_tab()

    def refresh_stack_tab(self):
        disp = self._stack_id_to_display()
        self.stack_cb["values"] = [disp[s.id] for s in self.model.stacks]
        if self.active_stack_id is None and self.model.stacks:
            self.active_stack_id = self.model.stacks[0].id
        stack = self._active_stack()
        self.stack_cb.set(disp.get(stack.id, "") if stack else "")

        self.add_link_cb["values"] = sorted(self._dim_name_map().keys())

        for iid in self.link_tree.get_children():
            self.link_tree.delete(iid)
        dims = self.model.dims_by_id()

        if stack is None:
            self._set_number_animated("stack_nom", self.stack_nom_lbl, None, _fmt)
            self.stack_range_lbl.configure(text="No stack — click “＋ Add”.")
            self._set_status_animated("stack_pass", self.stack_pass_lbl,
                                      "—", C["muted"], C["sunken"])
            self.target_min_entry.delete(0, tk.END)
            self.target_max_entry.delete(0, tk.END)
            self._draw_chain()
            return

        for link in stack.links:
            d = dims.get(link.dim_id)
            name = d.name if d else "(missing dimension)"
            contrib = (link.sense * d.nominal) if d else None
            self.link_tree.insert(
                "", "end", iid=link.id,
                tags=("pos" if link.sense > 0 else "neg",),
                values=("＋" if link.sense > 0 else "−", name, _fmt(contrib),
                        _fmt(d.min) if d else "—", _fmt(d.max) if d else "—"))

        self.stack_method_seg.set(stack.method)
        self.target_min_entry.delete(0, tk.END)
        if stack.target_min is not None:
            self.target_min_entry.insert(0, _fmt(stack.target_min))
        self.target_max_entry.delete(0, tk.END)
        if stack.target_max is not None:
            self.target_max_entry.insert(0, _fmt(stack.target_max))

        res = stack.compute(dims)
        self._set_number_animated("stack_nom", self.stack_nom_lbl,
                                  res["nominal"], lambda v: f"{_fmt(v)} mm")
        note = (f"   ⚠ {res['invalid_links']} missing link(s)"
                if res["invalid_links"] else "")
        self.stack_range_lbl.configure(
            text=f"range [{_fmt(res['min'])}, {_fmt(res['max'])}]  ({stack.method}){note}")
        pf = res["pass"]
        if pf is None:
            self._set_status_animated("stack_pass", self.stack_pass_lbl,
                                      "NO TARGET", C["muted"], C["sunken"])
        elif pf:
            self._set_status_animated("stack_pass", self.stack_pass_lbl,
                                      "PASS", C["good"], C["good_soft"])
        else:
            self._set_status_animated("stack_pass", self.stack_pass_lbl,
                                      "FAIL", C["bad"], C["bad_soft"])

        self._draw_chain()

    def _draw_chain(self):
        if not HAVE_MPL:
            return
        stack = self._active_stack()
        ax = self.chain_ax
        ax.clear()
        ax.set_facecolor(C["surface"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color(C["border_strong"])

        dims = self.model.dims_by_id()
        rows = ([] if stack is None else
                [(l, dims.get(l.dim_id)) for l in stack.links])
        rows = [(l, d) for l, d in rows if d is not None]
        if not rows:
            ax.text(0.5, 0.5, "No links in this stack",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=9, color=C["faint"])
            ax.set_xticks([])
            ax.set_yticks([])
            self._cancel_anim("chain")
            self.chain_canvas.draw_idle()
            return

        # Horizontal waterfall: one row per link, running cumulative total.
        cum = 0.0
        labels = []
        bar_info = []  # (rectangle patch, cumulative start, signed delta)
        for i, (link, d) in enumerate(rows):
            delta = link.sense * d.nominal
            left = min(cum, cum + delta)
            color = C["good"] if link.sense > 0 else C["warn"]
            bc = ax.barh(i, abs(delta), left=left, height=0.55, color=color,
                         edgecolor="white", linewidth=0.5, zorder=3)
            bar_info.append((bc.patches[0], cum, delta))
            new_cum = cum + delta
            # thin connector down to the next row
            if i < len(rows) - 1:
                ax.plot([new_cum, new_cum], [i, i + 1], color=C["border_strong"],
                        linewidth=0.8, zorder=2)
            cum = new_cum
            labels.append(d.name if len(d.name) <= 24 else d.name[:22] + "…")

        if stack.target_min is not None or stack.target_max is not None:
            lo = stack.target_min if stack.target_min is not None else ax.get_xlim()[0]
            hi = stack.target_max if stack.target_max is not None else ax.get_xlim()[1]
            ax.axvspan(lo, hi, color=C["good"], alpha=0.08, zorder=1)
        ax.axvline(cum, color=C["ink"], linewidth=1.2, zorder=4)
        ax.axvline(0, color=C["border_strong"], linewidth=0.8, zorder=1)

        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=7, color=C["muted"])
        ax.invert_yaxis()
        ax.tick_params(axis="x", labelsize=7, colors=C["muted"])
        ax.grid(axis="x", color=C["border"], linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)
        ax.set_xlabel("mm", fontsize=8, color=C["muted"])
        try:
            self.chain_fig.tight_layout()
        except Exception:
            pass

        # Grow the bars out of their cumulative anchors. Axes limits, grid,
        # connectors and the total line are all drawn at final positions
        # already (only the bar rectangles animate), so nothing rescales
        # mid-flight. finalize snaps every bar to its exact final geometry.
        def _bars_at(k):
            for patch, c0, delta in bar_info:
                w = abs(delta) * k
                patch.set_width(w)
                patch.set_x(c0 if delta >= 0 else c0 - w)
            self.chain_canvas.draw_idle()

        if self._anims_on():
            # 33ms ticks: each frame is a full matplotlib redraw (~30-40ms),
            # so a 16ms tick would saturate the Tk event queue for the whole
            # animation and make the UI feel busy.
            self._animate("chain", 420, _bars_at,
                          finalize=lambda: _bars_at(1.0), frame_ms=33)
        else:
            self._cancel_anim("chain")
            self.chain_canvas.draw_idle()

    # ================================================================== #
    #  Radial air-gap view                                                #
    # ================================================================== #
    def _build_radial_view(self, parent):
        # -- geometry row --
        geo = _card(parent)
        geo.pack(fill="x", pady=(0, 10))
        gin = WrapBar(geo, bg=C["surface"])
        gin.pack(fill="x", padx=10, pady=8)
        gin.add(lambda p: tk.Label(p, text="Stator ID", font=F_UI_B, fg=C["muted"],
                                   bg=C["surface"]), padx=(4, 6))

        def _mk_stator_cb(p):
            cb = ttk.Combobox(p, state="readonly", width=26, font=F_UI)
            cb.bind("<<ComboboxSelected>>", self._on_radial_dims_change)
            return cb
        self.stator_cb = gin.add(_mk_stator_cb, padx=(0, 14))
        gin.add(lambda p: tk.Label(p, text="Rotor OD", font=F_UI_B, fg=C["muted"],
                                   bg=C["surface"]), padx=(0, 6))

        def _mk_rotor_cb(p):
            cb = ttk.Combobox(p, state="readonly", width=26, font=F_UI)
            cb.bind("<<ComboboxSelected>>", self._on_radial_dims_change)
            return cb
        self.rotor_cb = gin.add(_mk_rotor_cb, padx=(0, 24))

        self.g0_label = gin.add(
            lambda p: tk.Label(p, text="g₀ = —", font=F_MONO_B,
                               fg=C["accent"], bg=C["surface"]), padx=(0, 20))
        gin.add(lambda p: tk.Label(p, text="Method", font=F_UI_B, fg=C["muted"],
                                   bg=C["surface"]), padx=(0, 8))

        def _mk_radial_seg(p):
            return Segmented(p, ["WC", "RSS"], command=self._on_radial_method_change)
        self.radial_method_seg = gin.add(_mk_radial_seg, padx=(0, 4))

        # -- contributors / result split: drag-to-resize, same as Axial.
        paned = tk.PanedWindow(parent, orient=tk.HORIZONTAL, sashrelief=tk.RAISED,
                               sashwidth=6, bg=C["border_strong"],
                               bd=0, opaqueresize=True)
        paned.pack(fill="both", expand=True)

        # -- left: contributors --
        left = _card(paned)
        paned.add(left, minsize=320, width=560, stretch="always")

        ctoolbar = tk.Frame(left, bg=C["sunken"])
        ctoolbar.pack(fill="x")
        # Shares its row with the 300px-wide result column -- same reasoning
        # as Axial's lbar above.
        cbar = WrapBar(ctoolbar, min_row_width=420)
        cbar.pack(fill="x")
        cbar.add(lambda p: _btn(p, "＋ Add Contributor", self._add_contributor, "primary"), padx=(10, 4), pady=8)
        cbar.add(lambda p: _btn(p, "Remove", self._remove_contributor, "danger"), padx=4, pady=8)
        tk.Label(ctoolbar, text="Eccentricity sources — each contributes e to rotor offset",
                 font=F_SMALL, fg=C["faint"], bg=C["sunken"], anchor="w"
                 ).pack(fill="x", padx=12, pady=(0, 6))

        cwrap = tk.Frame(left, bg=C["surface"])
        cwrap.pack(fill="both", expand=True, padx=1, pady=1)
        self.radial_tree = ttk.Treeview(
            cwrap, columns=("label", "type", "fit", "e"),
            show="headings", style="Tol.Treeview", height=8)
        for cid, text, w, anchor in (
                ("label", "CONTRIBUTOR", 210, "w"), ("type", "TYPE", 130, "w"),
                ("fit", "FROM FIT", 170, "w"), ("e", "e (mm)", 80, "e")):
            self.radial_tree.heading(cid, text=text)
            self.radial_tree.column(cid, width=w, anchor=anchor)
        cvs = ttk.Scrollbar(cwrap, orient="vertical", command=self.radial_tree.yview)
        self.radial_tree.configure(yscrollcommand=cvs.set)
        self.radial_tree.pack(side="left", fill="both", expand=True)
        cvs.pack(side="right", fill="y")
        self.radial_tree.bind("<<TreeviewSelect>>", self._on_radial_select)

        form = tk.Frame(left, bg=C["surface"])
        form.pack(fill="x", padx=12, pady=(6, 12))
        tk.Label(form, text="Label", font=F_SMALL, fg=C["muted"],
                 bg=C["surface"]).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.contrib_label_entry = tk.Entry(form, width=26, font=F_UI, relief="solid",
                                            bd=1, bg=C["surface"],
                                            highlightthickness=1,
                                            highlightbackground=C["border"],
                                            highlightcolor=C["accent"])
        self.contrib_label_entry.grid(row=1, column=0, padx=(0, 10), sticky="w")
        tk.Label(form, text="Type", font=F_SMALL, fg=C["muted"],
                 bg=C["surface"]).grid(row=0, column=1, sticky="w", padx=(0, 4))
        self.contrib_type_cb = ttk.Combobox(form, state="readonly", width=16, font=F_UI,
                                            values=("fitClearance", "coaxiality",
                                                    "runout", "bearingClearance"))
        self.contrib_type_cb.grid(row=1, column=1, padx=(0, 10), sticky="w")
        tk.Label(form, text="Pull from fit", font=F_SMALL, fg=C["muted"],
                 bg=C["surface"]).grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.contrib_fit_cb = ttk.Combobox(form, state="readonly", width=22, font=F_UI)
        self.contrib_fit_cb.grid(row=1, column=2, padx=(0, 10), sticky="w")
        tk.Label(form, text="Value (mm)", font=F_SMALL, fg=C["muted"],
                 bg=C["surface"]).grid(row=0, column=3, sticky="w", padx=(0, 4))
        self.contrib_value_entry = tk.Entry(form, width=9, font=F_MONO, relief="solid",
                                            bd=1, justify="right", bg=C["surface"],
                                            highlightthickness=1,
                                            highlightbackground=C["border"],
                                            highlightcolor=C["accent"])
        self.contrib_value_entry.grid(row=1, column=3, sticky="w")

        for w in (self.contrib_label_entry, self.contrib_value_entry):
            w.bind("<Return>", self._on_contrib_form_commit)
            w.bind("<FocusOut>", self._on_contrib_form_commit)
        self.contrib_type_cb.bind("<<ComboboxSelected>>", self._on_contrib_type_change)
        self.contrib_fit_cb.bind("<<ComboboxSelected>>", self._on_contrib_form_commit)

        # -- right: results --
        res = _card(paned)
        paned.add(res, minsize=260, width=300, stretch="always")
        rin = tk.Frame(res, bg=C["surface"])
        rin.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(rin, text="AIR-GAP RESULT", font=("Segoe UI Semibold", 9),
                 fg=C["faint"], bg=C["surface"]).pack(anchor="w")
        self.radial_status_lbl = tk.Label(rin, text="—", font=F_UI_B, padx=14, pady=3,
                                          fg=C["muted"], bg=C["sunken"])
        self.radial_status_lbl.pack(anchor="w", pady=(8, 10))
        self.radial_big_lbl = tk.Label(rin, text="—", font=F_MONO_XL,
                                       fg=C["ink"], bg=C["surface"])
        self.radial_big_lbl.pack(anchor="w")
        tk.Label(rin, text="min air-gap (worst case)", font=F_SMALL,
                 fg=C["faint"], bg=C["surface"]).pack(anchor="w", pady=(0, 10))
        self.radial_detail_lbl = tk.Label(rin, text="", font=F_MONO,
                                          fg=C["muted"], bg=C["surface"],
                                          justify="left")
        self.radial_detail_lbl.pack(anchor="w")
        tk.Label(rin,
                 text=("e per type (all diametral in → radial out):\n"
                       " fitClearance → max_clear / 2\n"
                       " coaxiality / runout → t / 2\n"
                       " bearingClearance → value / 2  (ISO 5753 RIC)"),
                 font=F_SMALL, fg=C["faint"], bg=C["surface"],
                 justify="left").pack(anchor="w", pady=(14, 0))

    def _on_radial_dims_change(self, _evt=None):
        names = self._dim_name_map()
        self.model.radial.stator_id_dim_id = names.get(self.stator_cb.get())
        self.model.radial.rotor_od_dim_id = names.get(self.rotor_cb.get())
        self._save()
        self.refresh_radial_tab()

    def _on_radial_method_change(self, _value=None):
        self.model.radial.method = self.radial_method_seg.get()
        self._save()
        self.refresh_radial_tab()

    def _add_contributor(self):
        c = self.model.add_contributor(label=f"Contributor {len(self.model.radial.contributors) + 1}")
        self._save()
        self.refresh_radial_tab()
        self.radial_tree.selection_set(c.id)
        self.radial_tree.see(c.id)

    def _remove_contributor(self):
        sel = self.radial_tree.selection()
        if not sel:
            return
        if not messagebox.askyesno("Remove Contributor", "Remove this contributor?"):
            return
        self.model.remove_contributor(sel[0])
        self._save()
        self.refresh_radial_tab()

    def _selected_contributor(self) -> Optional[RadialContributor]:
        sel = self.radial_tree.selection()
        if not sel:
            return None
        return next((c for c in self.model.radial.contributors if c.id == sel[0]), None)

    def _on_radial_select(self, _evt=None):
        self._loading_editor = True
        try:
            c = self._selected_contributor()
            if c is None:
                return
            self.contrib_label_entry.delete(0, tk.END)
            self.contrib_label_entry.insert(0, c.label)
            self.contrib_type_cb.set(c.type)
            fit_map = self._fit_name_map()
            id_to_disp = {v: k for k, v in fit_map.items()}
            self.contrib_fit_cb["values"] = sorted(fit_map.keys())
            self.contrib_fit_cb.set(id_to_disp.get(c.fit_id, ""))
            self.contrib_value_entry.configure(state="normal")
            self.contrib_value_entry.delete(0, tk.END)
            self.contrib_value_entry.insert(0, _fmt(c.value))
            is_fit = (c.type == "fitClearance")
            self.contrib_fit_cb.configure(state=("readonly" if is_fit else "disabled"))
            self.contrib_value_entry.configure(state=("disabled" if is_fit else "normal"))
        finally:
            self._loading_editor = False

    def _on_contrib_type_change(self, _evt=None):
        self._on_contrib_form_commit()
        self._on_radial_select()

    def _on_contrib_form_commit(self, _evt=None):
        if self._loading_editor:
            return
        c = self._selected_contributor()
        if c is None:
            return
        c.label = self.contrib_label_entry.get().strip() or c.label
        c.type = self.contrib_type_cb.get() or c.type
        if c.type == "fitClearance":
            fit_map = self._fit_name_map()
            c.fit_id = fit_map.get(self.contrib_fit_cb.get())
        else:
            c.value = _to_float(self.contrib_value_entry.get(), c.value)
        self._save()
        self.refresh_radial_tab()
        try:
            self.radial_tree.selection_set(c.id)
        except Exception:
            pass

    def refresh_radial_tab(self):
        names = self._dim_name_map()
        id_to_disp = self._dim_id_to_display()
        self.stator_cb["values"] = sorted(names.keys())
        self.rotor_cb["values"] = sorted(names.keys())
        self.stator_cb.set(id_to_disp.get(self.model.radial.stator_id_dim_id, ""))
        self.rotor_cb.set(id_to_disp.get(self.model.radial.rotor_od_dim_id, ""))
        self.radial_method_seg.set(self.model.radial.method)

        dims = self.model.dims_by_id()
        fits = self.model.fits_by_id()
        result = self.model.radial.compute(dims, fits)

        sel = self.radial_tree.selection()
        for iid in self.radial_tree.get_children():
            self.radial_tree.delete(iid)
        fit_id_to_disp = self._fit_id_to_display()
        for c in self.model.radial.contributors:
            e = self.model.radial.contributor_e(c, dims, fits)
            fit_disp = fit_id_to_disp.get(c.fit_id, "—") if c.type == "fitClearance" else "—"
            self.radial_tree.insert("", "end", iid=c.id,
                                     values=(c.label, c.type, fit_disp, _fmt(e)))
        if sel:
            try:
                self.radial_tree.selection_set(sel)
            except Exception:
                pass

        if result is None:
            self.g0_label.configure(text="g₀ = —")
            self._set_number_animated("radial_gap", self.radial_big_lbl, None, _fmt)
            self._set_status_animated("radial_status", self.radial_status_lbl,
                                      "PICK DIMS", C["muted"], C["sunken"])
            self.radial_detail_lbl.configure(
                text="Select a Stator ID and Rotor OD\ndimension above to compute g₀.")
        else:
            self.g0_label.configure(text=f"g₀ = {_fmt(result['g0'])} mm")
            ok = min(result["min_airgap_wc"], result["min_airgap_rss"]) > 0
            self._set_status_animated(
                "radial_status", self.radial_status_lbl,
                "OK — rotor clears" if ok else "RISK — rub possible",
                C["good"] if ok else C["bad"],
                C["good_soft"] if ok else C["bad_soft"])
            self.radial_big_lbl.configure(fg=C["good"] if ok else C["bad"])
            self._set_number_animated("radial_gap", self.radial_big_lbl,
                                      result["min_airgap_wc"],
                                      lambda v: f"{_fmt(v)} mm")
            self.radial_detail_lbl.configure(text=(
                f"min air-gap RSS  {_fmt(result['min_airgap_rss'])} mm\n"
                f"E (WC)           {_fmt(result['E_wc'])} mm\n"
                f"E (RSS)          {_fmt(result['E_rss'])} mm\n"
                f"eccentricity     {_fmt(result['ecc_pct'], 1)} %  ({self.model.radial.method})"))

    # ================================================================== #
    #  Summary view                                                       #
    # ================================================================== #
    def _build_summary_view(self, parent):
        card = _card(parent)
        card.pack(fill="both", expand=True)

        # Save/Load Project live in the header (visible from every tab, not
        # just this one) -- this bar keeps only the two actions specific to
        # Summary: a flat CSV export of the table above, and the .xlsx
        # bootstrap import that seeds Dimensions from an external sheet.
        bar = WrapBar(card)
        bar.pack(fill="x")
        bar.add(lambda p: _btn(p, "Export CSV", self._export_csv), padx=(10, 4), pady=8)
        xlsx_btn = bar.add(lambda p: _btn(p, "Import .xlsx (dimensions)", self._import_xlsx),
                            padx=4, pady=8)
        if not HAVE_OPENPYXL:
            xlsx_btn.configure(state="disabled", cursor="arrow")
            bar.add(lambda p: tk.Label(p, text="openpyxl not installed", font=F_SMALL,
                                       fg=C["warn"], bg=C["sunken"]), padx=6, pady=8)

        wrap = tk.Frame(card, bg=C["surface"])
        wrap.pack(fill="both", expand=True, padx=1, pady=1)
        self.summary_tree = ttk.Treeview(
            wrap, columns=("kind", "name", "status", "detail"),
            show="headings", style="Tol.Treeview")
        for cid, text, w, anchor in (
                ("kind", "KIND", 90, "w"), ("name", "NAME", 230, "w"),
                ("status", "STATUS", 120, "center"), ("detail", "DETAIL", 420, "w")):
            self.summary_tree.heading(cid, text=text)
            self.summary_tree.column(cid, width=w, anchor=anchor)
        self.summary_tree.tag_configure("ok", background=C["good_soft"])
        self.summary_tree.tag_configure("bad", background=C["bad_soft"])
        self.summary_tree.tag_configure("warn", background=C["warn_soft"])
        self.summary_tree.tag_configure("info", background=C["info_soft"])
        self.summary_tree.tag_configure("plain", background=C["surface"])
        svs = ttk.Scrollbar(wrap, orient="vertical", command=self.summary_tree.yview)
        self.summary_tree.configure(yscrollcommand=svs.set)
        self.summary_tree.pack(side="left", fill="both", expand=True)
        svs.pack(side="right", fill="y")

    @staticmethod
    def _summary_tag(status):
        return {"PASS": "ok", "OK": "ok", "Clearance": "ok",
                "FAIL": "bad", "RISK": "bad", "Interference": "warn",
                "Transition": "info"}.get(status, "plain")

    def refresh_summary_tab(self):
        for iid in self.summary_tree.get_children():
            self.summary_tree.delete(iid)
        dims = self.model.dims_by_id()
        fits = self.model.fits_by_id()

        for f in self.model.fits:
            cls = f.classification(dims)
            mn, mx = f.min_clear(dims), f.max_clear(dims)
            self.summary_tree.insert("", "end", iid=f"fit::{f.id}",
                                      tags=(self._summary_tag(cls),),
                                      values=("Fit", f.name, cls,
                                              f"min={_fmt(mn)}  max={_fmt(mx)}"))

        for s in self.model.stacks:
            res = s.compute(dims)
            status = "—" if res["pass"] is None else ("PASS" if res["pass"] else "FAIL")
            self.summary_tree.insert("", "end", iid=f"stack::{s.id}",
                                      tags=(self._summary_tag(status),),
                                      values=("Stack", s.name, status,
                                              f"[{_fmt(res['min'])}, {_fmt(res['max'])}] mm ({s.method})"))

        rres = self.model.radial.compute(dims, fits)
        if rres is not None:
            status = "OK" if min(rres["min_airgap_wc"], rres["min_airgap_rss"]) > 0 else "RISK"
            self.summary_tree.insert("", "end", iid="radial",
                                      tags=(self._summary_tag(status),),
                                      values=("Radial", "Air-gap", status,
                                              f"g0={_fmt(rres['g0'])}  min(WC)={_fmt(rres['min_airgap_wc'])}  "
                                              f"ecc={_fmt(rres['ecc_pct'], 1)}%"))

    def _export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                             filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["Kind", "Name", "Status", "Detail"])
                for iid in self.summary_tree.get_children():
                    w.writerow(self.summary_tree.item(iid, "values"))
            messagebox.showinfo("Export CSV", f"Saved to {path}")
        except Exception as exc:
            messagebox.showerror("Export CSV", str(exc))

    def _save_project_as(self):
        """Save the current model to a file of your choice, and make that
        file the target of every future autosave (standard "Save As"
        behavior -- the same JSON shape as the silent per-edit save to
        data_path, just user-named and user-located)."""
        path = filedialog.asksaveasfilename(
            title="Save Tolerance Project As", defaultextension=".json",
            filetypes=[("Tolerance Studio Project", "*.json"), ("All files", "*.*")])
        if not path:
            return
        self.data_path = path
        try:
            self._save()
            messagebox.showinfo("Save Project", f"Saved project to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Save Project", str(exc))

    def _load_project(self):
        """Replace the current model with one loaded from a file you pick;
        that file becomes the new autosave target, same as opening a
        project in any other app."""
        path = filedialog.askopenfilename(
            title="Load Tolerance Project",
            filetypes=[("Tolerance Studio Project", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.model = Model.from_dict(data)
            self.active_stack_id = self.model.stacks[0].id if self.model.stacks else None
            self.data_path = path
            self._save()
            self.refresh_all()
            messagebox.showinfo("Load Project", f"Loaded project from:\n{path}")
        except Exception as exc:
            messagebox.showerror("Load Project", str(exc))

    def _import_xlsx(self):
        if not HAVE_OPENPYXL:
            messagebox.showwarning("Import .xlsx", "openpyxl is not installed.")
            return
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                raise ValueError("Sheet is empty")
            header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]

            def col(*names):
                for n in names:
                    if n in header:
                        return header.index(n)
                return None

            i_name, i_group, i_nom = col("name"), col("group"), col("nominal")
            i_up = col("upper", "upper tol", "tolup", "+tol", "tol_up")
            i_lo = col("lower", "lower tol", "tollo", "-tol", "tol_lo")
            if i_name is None or i_nom is None:
                raise ValueError("Sheet needs at least 'Name' and 'Nominal' columns")
            # "Upper"/"Lower" headers are read as SIGNED deviations. The
            # legacy "+Tol"/"-Tol"/"TolLo" headers keep their old magnitude
            # meaning for the minus column (a positive 0.02 there means
            # -0.02), so old sheets import unchanged.
            signed_lo = i_lo is not None and header[i_lo] in ("upper", "lower", "upper tol", "lower tol")

            added = 0
            for row in rows[1:]:
                if row is None or i_name >= len(row) or row[i_name] is None:
                    continue
                name = str(row[i_name])
                group = (str(row[i_group]) if i_group is not None and i_group < len(row)
                         and row[i_group] is not None else "Imported")
                nominal = _to_float(row[i_nom] if i_nom < len(row) else None, 0.0)
                tol_up = (_to_float(row[i_up], 0.0) if i_up is not None and i_up < len(row) else 0.0)
                raw_lo = (_to_float(row[i_lo], 0.0) if i_lo is not None and i_lo < len(row) else 0.0)
                tol_lo = raw_lo if (signed_lo or raw_lo <= 0) else -raw_lo
                self.model.add_dimension(name=name, group=group, nominal=nominal,
                                          tol_up=tol_up, tol_lo=tol_lo)
                added += 1
            self._save()
            self.refresh_all()
            messagebox.showinfo("Import .xlsx", f"Added {added} dimension(s).")
        except Exception as exc:
            messagebox.showerror("Import .xlsx", str(exc))

    # ------------------------------------------------------------------ #
    #  Global refresh                                                     #
    # ------------------------------------------------------------------ #
    def refresh_all(self):
        self.model.recompute()
        self.refresh_dimensions_tab()
        self.refresh_fits_tab()
        self.refresh_stack_tab()
        self.refresh_radial_tab()
        self.refresh_summary_tab()
        self._update_chips()


# --------------------------------------------------------------------------- #
#  Standalone demo (drop-in usage shown in the module docstring above)        #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Tolerance Studio -- standalone demo")
    root.geometry("1280x820")
    root.configure(bg=C["bg"])
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True)
    page = ToleranceStudioPage(nb, data_path="tolerance_studio.json")
    nb.add(page, text="Tolerance & Stack-up")
    root.mainloop()
