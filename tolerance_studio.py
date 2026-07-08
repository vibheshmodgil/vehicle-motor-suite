"""Motor tolerancing & stack-up studio -- a single self-contained page.

Drop into an existing Tk/ttk app as one more ttk.Notebook tab:

    from tolerance_studio import ToleranceStudioPage
    nb.add(ToleranceStudioPage(nb, data_path="tolerance_studio.json"),
           text="Tolerance & Stack-up")

Pure stdlib + tkinter/ttk. matplotlib (chain chart) and openpyxl (.xlsx
bootstrap import) are optional -- the page degrades gracefully without them.
Touches nothing outside this module; the whole model persists to one JSON
file (loaded on init, saved after every edit and again on teardown).
"""

from __future__ import annotations

import csv
import dataclasses
import json
import math
import os
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


MONO_FONT = ("Consolas", 10)
BADGE_COLORS = {
    "Clearance": "#1b8a3a",
    "Transition": "#1c5fa8",
    "Interference": "#c96a15",
    "Invalid": "#8a8f98",
}


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
#  Data model                                                                 #
# --------------------------------------------------------------------------- #
@dataclass
class Dimension:
    id: str = ""
    name: str = ""
    group: str = "General"
    nominal: float = 0.0
    tol_up: float = 0.0    # stored >= 0
    tol_lo: float = 0.0    # stored <= 0

    @property
    def max(self):
        return self.nominal + max(self.tol_up, 0.0)

    @property
    def min(self):
        return self.nominal + min(self.tol_lo, 0.0)

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
            return c.value
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

    # ---- dimensions ----
    def add_dimension(self, name="New Dimension", group="General",
                       nominal=0.0, tol_up=0.0, tol_lo=0.0) -> Dimension:
        d = Dimension(id=_new_id(), name=name, group=group, nominal=nominal,
                       tol_up=abs(tol_up), tol_lo=-abs(tol_lo))
        self.dimensions.append(d)
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
    def add_fit(self, name="New Fit", hole_id="", shaft_id="") -> Fit:
        f = Fit(id=_new_id(), name=name, hole_dim_id=hole_id, shaft_dim_id=shaft_id)
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
        return m


def _build_seed_model() -> Model:
    """A plausible small hub-motor tolerance stack, wired up with real ids so
    every fit / stack / contributor references a real Dimension from the
    start. Purely a starting point -- everything is editable in-app."""
    m = Model()

    shaft_od = m.add_dimension("Shaft OD @ Bearing Seat", "Shaft", 15.000, 0.000, 0.008)
    brg_bore = m.add_dimension("Bearing Bore ID", "Bearing", 15.000, 0.010, 0.000)
    brg_od = m.add_dimension("Bearing OD", "Bearing", 35.000, 0.000, 0.011)
    housing_bore = m.add_dimension("Housing Bearing Bore", "Housing", 35.000, 0.018, 0.000)
    brg_width = m.add_dimension("Bearing Width", "Bearing", 11.000, 0.000, 0.120)
    circlip_gap = m.add_dimension("Circlip Groove-to-Shoulder", "Shaft", 2.000, 0.050, 0.050)
    shoulder_to_cover = m.add_dimension("Housing Shoulder-to-Cover Face", "Housing", 13.500, 0.100, 0.100)
    cover_thk = m.add_dimension("End Cover Thickness", "Housing", 3.000, 0.050, 0.050)
    stator_id = m.add_dimension("Stator ID", "Stator", 90.000, 0.050, 0.000)
    rotor_od = m.add_dimension("Rotor OD", "Rotor", 89.200, 0.000, 0.060)

    fit_inner = m.add_fit("Bearing Inner Ring Fit", brg_bore.id, shaft_od.id)
    fit_outer = m.add_fit("Bearing Outer Ring Fit", housing_bore.id, brg_od.id)

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
    retention.target_min, retention.target_max = 0.0, None

    m.radial.stator_id_dim_id = stator_id.id
    m.radial.rotor_od_dim_id = rotor_od.id
    m.radial.method = "WC"
    m.add_contributor("Bearing outer fit clearance", "fitClearance", fit_outer.id, 0.0)
    m.add_contributor("Rotor coaxiality (diametral)", "coaxiality", None, 0.030)
    m.add_contributor("Stator bore runout (diametral)", "runout", None, 0.020)
    m.add_contributor("Bearing radial clearance", "bearingClearance", None, 0.010)

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
        super().__init__(master, **kw)
        self.editable_cols = set(editable_cols or ())
        self.on_edit = on_edit
        self._editor = None
        self.bind("<Double-1>", self._begin_edit)

    def _begin_edit(self, event):
        region = self.identify("region", event.x, event.y)
        if region not in ("cell", "tree"):
            return
        row_id = self.identify_row(event.y)
        col = self.identify_column(event.x)
        if not row_id or not col:
            return
        if "noedit" in self.item(row_id, "tags"):
            return
        if col == "#0":
            col_id = "#0"
        else:
            cols = self["columns"]
            idx = int(col[1:]) - 1
            if idx < 0 or idx >= len(cols):
                return
            col_id = cols[idx]
        if col_id not in self.editable_cols:
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
        entry = ttk.Entry(self, font=MONO_FONT)
        entry.insert(0, current)
        entry.select_range(0, tk.END)
        entry.place(x=x, y=y, width=max(w, 60), height=h)
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


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable Canvas+Frame; scrolls only while the pointer
    is over it, so it doesn't hijack the mouse wheel from sibling widgets."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                         lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# --------------------------------------------------------------------------- #
#  The page                                                                   #
# --------------------------------------------------------------------------- #
class ToleranceStudioPage(ttk.Frame):
    def __init__(self, parent, data_path="tolerance_studio.json"):
        super().__init__(parent)
        self.data_path = data_path
        self.model = self._load_or_seed()
        self.active_stack_id = self.model.stacks[0].id if self.model.stacks else None
        self._fit_widgets: Dict[str, dict] = {}

        self._build_ui()
        self.refresh_all()
        self._save()
        self.bind("<Destroy>", self._on_destroy)

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
        except Exception as exc:
            print(f"[tolerance_studio] save failed: {exc}")

    def _on_destroy(self, event):
        if event.widget is self:
            self._save()

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
        style = ttk.Style(self)
        style.configure("Mono.Treeview", font=MONO_FONT, rowheight=22)
        style.configure("Mono.Treeview.Heading", font=(MONO_FONT[0], MONO_FONT[1], "bold"))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        self.dim_tab = ttk.Frame(self.nb)
        self.fits_tab = ttk.Frame(self.nb)
        self.axial_tab = ttk.Frame(self.nb)
        self.radial_tab = ttk.Frame(self.nb)
        self.summary_tab = ttk.Frame(self.nb)

        self.nb.add(self.dim_tab, text="Dimensions")
        self.nb.add(self.fits_tab, text="Fits")
        self.nb.add(self.axial_tab, text="Axial Stack-up")
        self.nb.add(self.radial_tab, text="Radial Air-gap")
        self.nb.add(self.summary_tab, text="Summary")

        self._build_dimensions_tab()
        self._build_fits_tab()
        self._build_axial_tab()
        self._build_radial_tab()
        self._build_summary_tab()

    # ---- Dimensions tab ----
    def _build_dimensions_tab(self):
        top = ttk.Frame(self.dim_tab)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Add", command=self._add_dimension).pack(side="left", padx=2)
        ttk.Button(top, text="Duplicate", command=self._duplicate_dimension).pack(side="left", padx=2)
        ttk.Button(top, text="Delete", command=self._delete_dimension).pack(side="left", padx=2)

        self.dim_tree = EditableTreeview(
            self.dim_tab, columns=("nominal", "tol_up", "tol_lo", "min", "max"),
            show="tree headings", editable_cols={"#0", "nominal", "tol_up", "tol_lo"},
            on_edit=self._on_dim_cell_edit, style="Mono.Treeview", height=18)
        self.dim_tree.heading("#0", text="Name / Group")
        self.dim_tree.column("#0", width=230, anchor="w")
        for cid, text in (("nominal", "Nominal"), ("tol_up", "+Tol"), ("tol_lo", "-Tol"),
                          ("min", "Min"), ("max", "Max")):
            self.dim_tree.heading(cid, text=text)
            self.dim_tree.column(cid, width=80, anchor="e")
        self.dim_tree.tag_configure("noedit", background="#eef1f4",
                                     font=(MONO_FONT[0], MONO_FONT[1], "bold"))
        self.dim_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

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
        self.nb.select(self.dim_tab)
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
            d.tol_up = abs(_to_float(new_value, d.tol_up))
        elif col_id == "tol_lo":
            d.tol_lo = -abs(_to_float(new_value, d.tol_lo))
        self._save()
        self.refresh_all()

    def refresh_dimensions_tab(self):
        open_groups = {iid for iid in self.dim_tree.get_children()
                       if self.dim_tree.item(iid, "open")}
        sel = self.dim_tree.selection()
        for iid in self.dim_tree.get_children():
            self.dim_tree.delete(iid)

        groups: Dict[str, List[Dimension]] = {}
        for d in self.model.dimensions:
            groups.setdefault(d.group, []).append(d)

        for group in sorted(groups.keys()):
            gid = f"grp::{group}"
            self.dim_tree.insert("", "end", iid=gid, text=group,
                                  values=("", "", "", "", ""), tags=("noedit",),
                                  open=(gid in open_groups or not open_groups))
            for d in sorted(groups[group], key=lambda x: x.name.lower()):
                self.dim_tree.insert(gid, "end", iid=d.id, text=d.name, values=(
                    f"{d.nominal:.3f}", f"{d.tol_up:+.3f}", f"{d.tol_lo:+.3f}",
                    f"{d.min:.3f}", f"{d.max:.3f}"))
        if sel:
            try:
                self.dim_tree.selection_set(sel)
            except Exception:
                pass

    def _jump_to_dimension(self, dim_id):
        self.nb.select(self.dim_tab)
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

    # ---- Fits tab ----
    def _build_fits_tab(self):
        top = ttk.Frame(self.fits_tab)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Add Fit", command=self._add_fit).pack(side="left", padx=2)

        self.fits_scroll = ScrollableFrame(self.fits_tab)
        self.fits_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _add_fit(self):
        self.model.add_fit(name=f"Fit {len(self.model.fits) + 1}")
        self._save()
        self.refresh_all()

    def _delete_fit(self, fid):
        if not messagebox.askyesno("Delete Fit", "Delete this fit?"):
            return
        self.model.delete_fit(fid)
        self._save()
        self.refresh_all()

    def _build_fit_card(self, parent, fit: Fit):
        card = ttk.LabelFrame(parent, text="Fit")
        card.pack(fill="x", padx=6, pady=6)

        top = ttk.Frame(card)
        top.pack(fill="x", padx=8, pady=(8, 4))
        name_var = tk.StringVar(value=fit.name)
        name_entry = ttk.Entry(top, textvariable=name_var, width=22)
        name_entry.pack(side="left")

        def commit_name(_evt=None):
            fit.name = name_var.get().strip() or fit.name
            self._save()
            self.refresh_summary_tab()

        name_entry.bind("<Return>", commit_name)
        name_entry.bind("<FocusOut>", commit_name)

        ttk.Label(top, text="  Hole:").pack(side="left")
        hole_cb = ttk.Combobox(top, state="readonly", width=20)
        hole_cb.pack(side="left")
        ttk.Label(top, text="  Shaft:").pack(side="left")
        shaft_cb = ttk.Combobox(top, state="readonly", width=20)
        shaft_cb.pack(side="left")

        ttk.Button(top, text="Delete",
                   command=lambda: self._delete_fit(fit.id)).pack(side="right")

        bottom = ttk.Frame(card)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        badge = tk.Label(bottom, text="—", bg=BADGE_COLORS["Invalid"], fg="white",
                          font=("Segoe UI", 9, "bold"), padx=8, pady=2)
        badge.pack(side="left")
        detail = tk.Label(bottom, text="", font=MONO_FONT)
        detail.pack(side="left", padx=10)

        self._fit_widgets[fit.id] = {
            "card": card, "name_var": name_var, "hole_cb": hole_cb,
            "shaft_cb": shaft_cb, "badge": badge, "detail": detail,
        }

        def on_hole(_evt=None):
            fit.hole_dim_id = self._dim_name_map().get(hole_cb.get(), fit.hole_dim_id)
            self._save()
            self.refresh_fit_badge(fit.id)
            self.refresh_summary_tab()

        def on_shaft(_evt=None):
            fit.shaft_dim_id = self._dim_name_map().get(shaft_cb.get(), fit.shaft_dim_id)
            self._save()
            self.refresh_fit_badge(fit.id)
            self.refresh_summary_tab()

        hole_cb.bind("<<ComboboxSelected>>", on_hole)
        shaft_cb.bind("<<ComboboxSelected>>", on_shaft)

    def refresh_fits_tab(self):
        model_ids = {f.id for f in self.model.fits}
        for fid in list(self._fit_widgets.keys()):
            if fid not in model_ids:
                self._fit_widgets.pop(fid)["card"].destroy()
        for f in self.model.fits:
            if f.id not in self._fit_widgets:
                self._build_fit_card(self.fits_scroll.inner, f)

        names = sorted(self._dim_name_map().keys())
        id_to_disp = self._dim_id_to_display()
        for f in self.model.fits:
            w = self._fit_widgets[f.id]
            w["hole_cb"]["values"] = names
            w["shaft_cb"]["values"] = names
            w["hole_cb"].set(id_to_disp.get(f.hole_dim_id, ""))
            w["shaft_cb"].set(id_to_disp.get(f.shaft_dim_id, ""))
            if w["name_var"].get() != f.name:
                w["name_var"].set(f.name)
            self.refresh_fit_badge(f.id)

    def refresh_fit_badge(self, fid):
        f = next((x for x in self.model.fits if x.id == fid), None)
        w = self._fit_widgets.get(fid)
        if f is None or w is None:
            return
        dims = self.model.dims_by_id()
        cls = f.classification(dims)
        mn, mx = f.min_clear(dims), f.max_clear(dims)
        w["badge"].configure(text=cls.upper(), bg=BADGE_COLORS.get(cls, BADGE_COLORS["Invalid"]))
        w["detail"].configure(text=f"min={_fmt(mn)}  max={_fmt(mx)}")

    # ---- Axial Stack-up tab ----
    def _build_axial_tab(self):
        top = ttk.Frame(self.axial_tab)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Stack:").pack(side="left")
        self.stack_cb = ttk.Combobox(top, state="readonly", width=28)
        self.stack_cb.pack(side="left", padx=(4, 8))
        self.stack_cb.bind("<<ComboboxSelected>>", self._on_stack_selected)
        ttk.Button(top, text="Add Stack", command=self._add_stack).pack(side="left", padx=2)
        ttk.Button(top, text="Rename", command=self._rename_stack).pack(side="left", padx=2)
        ttk.Button(top, text="Delete Stack", command=self._delete_stack).pack(side="left", padx=2)

        method_frame = ttk.Frame(top)
        method_frame.pack(side="right")
        self.stack_method_var = tk.StringVar(value="WC")
        ttk.Radiobutton(method_frame, text="Worst-Case", value="WC",
                         variable=self.stack_method_var,
                         command=self._on_stack_method_change).pack(side="left")
        ttk.Radiobutton(method_frame, text="RSS", value="RSS",
                         variable=self.stack_method_var,
                         command=self._on_stack_method_change).pack(side="left")

        body = ttk.Frame(self.axial_tab)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, width=340)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        self.link_tree = ttk.Treeview(left, columns=("sense", "dim", "contribution"),
                                       show="headings", height=10, style="Mono.Treeview")
        for cid, text, w in (("sense", "±", 40), ("dim", "Dimension", 190),
                             ("contribution", "Contribution", 110)):
            self.link_tree.heading(cid, text=text)
            self.link_tree.column(cid, width=w, anchor=("center" if cid != "dim" else "w"))
        self.link_tree.pack(fill="both", expand=True)

        link_btns = ttk.Frame(left)
        link_btns.pack(fill="x", pady=4)
        self.add_link_cb = ttk.Combobox(link_btns, state="readonly", width=20)
        self.add_link_cb.pack(side="left", padx=(0, 4))
        ttk.Button(link_btns, text="Add Link", command=self._add_link).pack(side="left", padx=2)
        ttk.Button(link_btns, text="Remove Link", command=self._remove_link).pack(side="left", padx=2)
        ttk.Button(link_btns, text="Move Up", command=lambda: self._move_link(-1)).pack(side="left", padx=2)
        ttk.Button(link_btns, text="Move Down", command=lambda: self._move_link(1)).pack(side="left", padx=2)
        ttk.Button(link_btns, text="Toggle ±", command=self._toggle_link_sense).pack(side="left", padx=2)
        ttk.Button(link_btns, text="Edit Tol...", command=self._jump_to_dim_from_link).pack(side="left", padx=2)

        tgt = ttk.LabelFrame(left, text="Target (mm)")
        tgt.pack(fill="x", pady=6)
        ttk.Label(tgt, text="Min:").grid(row=0, column=0, padx=4, pady=6)
        self.target_min_entry = ttk.Entry(tgt, width=10, font=MONO_FONT)
        self.target_min_entry.grid(row=0, column=1, padx=4)
        ttk.Label(tgt, text="Max:").grid(row=0, column=2, padx=4)
        self.target_max_entry = ttk.Entry(tgt, width=10, font=MONO_FONT)
        self.target_max_entry.grid(row=0, column=3, padx=4)
        for e in (self.target_min_entry, self.target_max_entry):
            e.bind("<Return>", self._on_target_change)
            e.bind("<FocusOut>", self._on_target_change)

        res = ttk.LabelFrame(left, text="Result")
        res.pack(fill="x", pady=6)
        self.stack_result_label = tk.Label(res, text="—", font=("Segoe UI", 11, "bold"),
                                            anchor="w", justify="left")
        self.stack_result_label.pack(fill="x", padx=8, pady=8)

        chart_holder = ttk.LabelFrame(right, text="Chain")
        chart_holder.pack(fill="both", expand=True)
        if HAVE_MPL:
            self.chain_fig = Figure(figsize=(3.6, 2.8), dpi=100)
            self.chain_ax = self.chain_fig.add_subplot(111)
            self.chain_canvas = FigureCanvasTkAgg(self.chain_fig, master=chart_holder)
            self.chain_canvas.get_tk_widget().pack(fill="both", expand=True)
        else:
            ttk.Label(chart_holder,
                      text="matplotlib not installed —\nchain chart unavailable.",
                      foreground="#a05a00", justify="center").pack(padx=10, pady=20)

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

    def _on_stack_method_change(self):
        s = self._active_stack()
        if s is None:
            return
        s.method = self.stack_method_var.get()
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
            self.stack_result_label.configure(text="No stack selected. Click 'Add Stack' to start.",
                                               fg="#666666")
            self.target_min_entry.delete(0, tk.END)
            self.target_max_entry.delete(0, tk.END)
            self._draw_chain()
            return

        for link in stack.links:
            d = dims.get(link.dim_id)
            name = d.name if d else "(missing dimension)"
            contrib = (link.sense * d.nominal) if d else None
            self.link_tree.insert("", "end", iid=link.id,
                                   values=("+" if link.sense > 0 else "-", name, _fmt(contrib)))

        self.stack_method_var.set(stack.method)
        self.target_min_entry.delete(0, tk.END)
        if stack.target_min is not None:
            self.target_min_entry.insert(0, _fmt(stack.target_min))
        self.target_max_entry.delete(0, tk.END)
        if stack.target_max is not None:
            self.target_max_entry.insert(0, _fmt(stack.target_max))

        res = stack.compute(dims)
        pf = res["pass"]
        pf_txt = "—" if pf is None else ("PASS" if pf else "FAIL")
        pf_color = {"—": "#666666", "PASS": "#1b8a3a", "FAIL": "#c0392b"}[pf_txt]
        invalid_note = (f"  ({res['invalid_links']} link(s) reference a missing dimension)"
                         if res["invalid_links"] else "")
        self.stack_result_label.configure(
            text=(f"nominal = {_fmt(res['nominal'])} mm\n"
                  f"range   = [{_fmt(res['min'])}, {_fmt(res['max'])}] mm\n"
                  f"{pf_txt}{invalid_note}"),
            fg=pf_color)

        self._draw_chain()

    def _draw_chain(self):
        if not HAVE_MPL:
            return
        stack = self._active_stack()
        ax = self.chain_ax
        ax.clear()
        if stack is None or not stack.links:
            ax.text(0.5, 0.5, "No links in this stack", ha="center", va="center",
                     transform=ax.transAxes, fontsize=9, color="#888888")
            ax.set_xticks([])
            ax.set_yticks([])
            self.chain_canvas.draw_idle()
            return

        dims = self.model.dims_by_id()
        cum = 0.0
        for link in stack.links:
            d = dims.get(link.dim_id)
            if d is None:
                continue
            delta = link.sense * d.nominal
            left = cum if delta >= 0 else cum + delta
            width = abs(delta)
            color = "#2f8f4f" if link.sense > 0 else "#c9532b"
            ax.barh(0, width, left=left, height=0.5, color=color, edgecolor="white")
            if width > 0:
                ax.text(left + width / 2, 0, d.name, ha="center", va="center",
                         fontsize=7, color="white", clip_on=True)
            cum += delta

        if stack.target_min is not None:
            ax.axvline(stack.target_min, color="#888888", linestyle="--", linewidth=1)
        if stack.target_max is not None:
            ax.axvline(stack.target_max, color="#888888", linestyle="--", linewidth=1)
        ax.axvline(cum, color="#111111", linestyle="-", linewidth=1.2)
        ax.set_yticks([])
        ax.set_xlabel("mm", fontsize=8)
        ax.tick_params(labelsize=8)
        try:
            self.chain_fig.tight_layout()
        except Exception:
            pass
        self.chain_canvas.draw_idle()

    # ---- Radial Air-gap tab ----
    def _build_radial_tab(self):
        top = ttk.Frame(self.radial_tab)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Stator ID:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.stator_cb = ttk.Combobox(top, state="readonly", width=22)
        self.stator_cb.grid(row=0, column=1, padx=4)
        ttk.Label(top, text="Rotor OD:").grid(row=0, column=2, sticky="w", padx=4)
        self.rotor_cb = ttk.Combobox(top, state="readonly", width=22)
        self.rotor_cb.grid(row=0, column=3, padx=4)
        self.stator_cb.bind("<<ComboboxSelected>>", self._on_radial_dims_change)
        self.rotor_cb.bind("<<ComboboxSelected>>", self._on_radial_dims_change)

        self.g0_label = tk.Label(top, text="g0 = —", font=("Segoe UI", 10, "bold"))
        self.g0_label.grid(row=0, column=4, padx=16)

        method_frame = ttk.Frame(top)
        method_frame.grid(row=0, column=5, padx=8)
        self.radial_method_var = tk.StringVar(value="WC")
        ttk.Radiobutton(method_frame, text="WC", value="WC", variable=self.radial_method_var,
                         command=self._on_radial_method_change).pack(side="left")
        ttk.Radiobutton(method_frame, text="RSS", value="RSS", variable=self.radial_method_var,
                         command=self._on_radial_method_change).pack(side="left")

        body = ttk.Frame(self.radial_tab)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, width=260)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        self.radial_tree = ttk.Treeview(left, columns=("label", "type", "fit", "e"),
                                         show="headings", height=8, style="Mono.Treeview")
        for cid, text, w in (("label", "Label", 170), ("type", "Type", 120),
                             ("fit", "Fit", 130), ("e", "e (mm)", 70)):
            self.radial_tree.heading(cid, text=text)
            self.radial_tree.column(cid, width=w, anchor=("w" if cid != "e" else "center"))
        self.radial_tree.pack(fill="both", expand=True)
        self.radial_tree.bind("<<TreeviewSelect>>", self._on_radial_select)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Add Contributor", command=self._add_contributor).pack(side="left", padx=2)
        ttk.Button(btns, text="Remove", command=self._remove_contributor).pack(side="left", padx=2)

        form = ttk.LabelFrame(left, text="Selected contributor")
        form.pack(fill="x", pady=6)
        ttk.Label(form, text="Label:").grid(row=0, column=0, padx=4, pady=6, sticky="w")
        self.contrib_label_entry = ttk.Entry(form, width=26)
        self.contrib_label_entry.grid(row=0, column=1, padx=4, pady=6)
        ttk.Label(form, text="Type:").grid(row=0, column=2, padx=4, sticky="w")
        self.contrib_type_cb = ttk.Combobox(form, state="readonly", width=16, values=(
            "fitClearance", "coaxiality", "runout", "bearingClearance"))
        self.contrib_type_cb.grid(row=0, column=3, padx=4)
        ttk.Label(form, text="Fit:").grid(row=1, column=0, padx=4, pady=6, sticky="w")
        self.contrib_fit_cb = ttk.Combobox(form, state="readonly", width=26)
        self.contrib_fit_cb.grid(row=1, column=1, padx=4, pady=6)
        ttk.Label(form, text="Value:").grid(row=1, column=2, padx=4, sticky="w")
        self.contrib_value_entry = ttk.Entry(form, width=10, font=MONO_FONT)
        self.contrib_value_entry.grid(row=1, column=3, padx=4)

        for w in (self.contrib_label_entry, self.contrib_value_entry):
            w.bind("<Return>", self._on_contrib_form_commit)
            w.bind("<FocusOut>", self._on_contrib_form_commit)
        self.contrib_type_cb.bind("<<ComboboxSelected>>", self._on_contrib_type_change)
        self.contrib_fit_cb.bind("<<ComboboxSelected>>", self._on_contrib_form_commit)

        res = ttk.LabelFrame(right, text="Result")
        res.pack(fill="x")
        self.radial_result_label = tk.Label(res, text="—", justify="left", anchor="w",
                                             font=("Segoe UI", 10))
        self.radial_result_label.pack(fill="x", padx=8, pady=8)

    def _on_radial_dims_change(self, _evt=None):
        names = self._dim_name_map()
        self.model.radial.stator_id_dim_id = names.get(self.stator_cb.get())
        self.model.radial.rotor_od_dim_id = names.get(self.rotor_cb.get())
        self._save()
        self.refresh_radial_tab()

    def _on_radial_method_change(self):
        self.model.radial.method = self.radial_method_var.get()
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
        self.contrib_value_entry.delete(0, tk.END)
        self.contrib_value_entry.insert(0, _fmt(c.value))
        is_fit = (c.type == "fitClearance")
        self.contrib_fit_cb.configure(state=("readonly" if is_fit else "disabled"))
        self.contrib_value_entry.configure(state=("disabled" if is_fit else "normal"))

    def _on_contrib_type_change(self, _evt=None):
        self._on_contrib_form_commit()
        self._on_radial_select()

    def _on_contrib_form_commit(self, _evt=None):
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
        self.radial_method_var.set(self.model.radial.method)

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
            self.radial_tree.insert("", "end", iid=c.id, values=(c.label, c.type, fit_disp, _fmt(e)))
        if sel:
            try:
                self.radial_tree.selection_set(sel)
            except Exception:
                pass

        if result is None:
            self.g0_label.configure(text="g0 = —")
            self.radial_result_label.configure(
                text="Select a Stator ID and Rotor OD dimension above.")
        else:
            self.g0_label.configure(text=f"g0 = {_fmt(result['g0'])} mm")
            self.radial_result_label.configure(text=(
                f"E (WC)  = {_fmt(result['E_wc'])} mm\n"
                f"E (RSS) = {_fmt(result['E_rss'])} mm\n"
                f"Min air-gap (WC)  = {_fmt(result['min_airgap_wc'])} mm\n"
                f"Min air-gap (RSS) = {_fmt(result['min_airgap_rss'])} mm\n"
                f"Eccentricity ({self.model.radial.method}) = {_fmt(result['ecc_pct'], 1)} %"))

    # ---- Summary tab ----
    def _build_summary_tab(self):
        top = ttk.Frame(self.summary_tab)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Export CSV", command=self._export_csv).pack(side="left", padx=2)
        ttk.Button(top, text="Export JSON", command=self._export_json).pack(side="left", padx=2)
        ttk.Button(top, text="Import JSON", command=self._import_json).pack(side="left", padx=2)
        xlsx_btn = ttk.Button(top, text="Import .xlsx (dimensions)", command=self._import_xlsx)
        xlsx_btn.pack(side="left", padx=2)
        if not HAVE_OPENPYXL:
            xlsx_btn.configure(state="disabled")
            ttk.Label(top, text="(openpyxl not installed)",
                      foreground="#a05a00").pack(side="left", padx=6)

        self.summary_tree = ttk.Treeview(self.summary_tab, columns=("kind", "name", "status", "detail"),
                                          show="headings", style="Mono.Treeview")
        for cid, text, w in (("kind", "Kind", 80), ("name", "Name", 210),
                             ("status", "Status", 100), ("detail", "Detail", 320)):
            self.summary_tree.heading(cid, text=text)
            self.summary_tree.column(cid, width=w, anchor="w")
        self.summary_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def refresh_summary_tab(self):
        for iid in self.summary_tree.get_children():
            self.summary_tree.delete(iid)
        dims = self.model.dims_by_id()
        fits = self.model.fits_by_id()

        for f in self.model.fits:
            cls = f.classification(dims)
            mn, mx = f.min_clear(dims), f.max_clear(dims)
            self.summary_tree.insert("", "end", iid=f"fit::{f.id}",
                                      values=("Fit", f.name, cls, f"min={_fmt(mn)}  max={_fmt(mx)}"))

        for s in self.model.stacks:
            res = s.compute(dims)
            status = "—" if res["pass"] is None else ("PASS" if res["pass"] else "FAIL")
            self.summary_tree.insert("", "end", iid=f"stack::{s.id}",
                                      values=("Stack", s.name, status,
                                              f"[{_fmt(res['min'])}, {_fmt(res['max'])}] mm ({s.method})"))

        rres = self.model.radial.compute(dims, fits)
        if rres is not None:
            status = "OK" if min(rres["min_airgap_wc"], rres["min_airgap_rss"]) > 0 else "RISK"
            self.summary_tree.insert("", "end", iid="radial",
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

    def _export_json(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                             filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.model.to_dict(), fh, indent=2)
            messagebox.showinfo("Export JSON", f"Saved to {path}")
        except Exception as exc:
            messagebox.showerror("Export JSON", str(exc))

    def _import_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.model = Model.from_dict(data)
            self.active_stack_id = self.model.stacks[0].id if self.model.stacks else None
            self._fit_widgets = {}
            for child in list(self.fits_scroll.inner.winfo_children()):
                child.destroy()
            self._save()
            self.refresh_all()
            messagebox.showinfo("Import JSON", f"Loaded {path}")
        except Exception as exc:
            messagebox.showerror("Import JSON", str(exc))

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
            i_up = col("tolup", "+tol", "tol_up")
            i_lo = col("tollo", "-tol", "tol_lo")
            if i_name is None or i_nom is None:
                raise ValueError("Sheet needs at least 'Name' and 'Nominal' columns")

            added = 0
            for row in rows[1:]:
                if row is None or i_name >= len(row) or row[i_name] is None:
                    continue
                name = str(row[i_name])
                group = (str(row[i_group]) if i_group is not None and i_group < len(row)
                         and row[i_group] is not None else "Imported")
                nominal = _to_float(row[i_nom] if i_nom < len(row) else None, 0.0)
                tol_up = (abs(_to_float(row[i_up], 0.0)) if i_up is not None and i_up < len(row) else 0.0)
                tol_lo = (-abs(_to_float(row[i_lo], 0.0)) if i_lo is not None and i_lo < len(row) else 0.0)
                self.model.add_dimension(name=name, group=group, nominal=nominal,
                                          tol_up=tol_up, tol_lo=abs(tol_lo))
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


# --------------------------------------------------------------------------- #
#  Standalone demo (drop-in usage shown in the module docstring above)        #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Tolerance Studio -- standalone demo")
    root.geometry("1280x820")
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True)
    page = ToleranceStudioPage(nb, data_path="tolerance_studio.json")
    nb.add(page, text="Tolerance & Stack-up")
    root.mainloop()
