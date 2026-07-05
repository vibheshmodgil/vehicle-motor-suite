"""Motor BOM (Cost & Weight) analysis.

A hierarchical bill of materials for the motor: assemblies -> sub-assemblies
-> parts, each leaf carrying qty, unit cost (₹) and unit weight (g), plus two
classification tags (Category: Electrical/Mechanical/Electronics/Other, and
Active: in the electromagnetic energy path or not). Totals always roll up
from the leaves; `qty` multiplies its whole subtree (bearings ×2, left/right
covers ×2, ...).

Modularity: the BOM is ONE plain JSON-able tree (`self.bom_tree`) — nothing
about the architecture is hard-coded, so a hub motor and a mid-mount motor
are just different trees. Ways to build/maintain one:
  * Load a built-in starting template (Mid-Mount / Hub) and edit it,
  * edit in-app (tree editor: add assembly / add part / edit / delete),
  * round-trip through Excel (flat rows: Assembly | Sub-assembly | Part |
    Qty | Unit Cost | Unit Weight | Category | Active) for bulk editing.
The tree persists in scenarios and the session autosave via _dataset_slots().

Views (Metric switch: Cost ₹ / Weight g drives all of them):
  * Sankey Diagram — Motor -> assemblies -> parts flow, branch control via a
    depth limit and a "min branch share %" that folds small parts into an
    "Others" band per parent.
  * Pareto (Max -> Min) — every part sorted descending with cumulative-% line,
    bars colored by Category / Active / Assembly.
  * Group Split — totals by Top Assembly, Category, or Active/Non-active.

All module-level functions are pure and tested in tests/test_bom.py.
"""

import copy

import numpy as np
import customtkinter as ctk
from tkinter import ttk, filedialog, messagebox

from .theme import COLORS
from .validation import parse_float

ANALYSIS_NAME = "Motor BOM (Cost & Weight)"

CATEGORY_CHOICES = ["Electrical", "Mechanical", "Electronics", "Other"]
BOM_METRICS = {"Cost (₹)": "cost", "Weight (g)": "weight"}
BOM_VIEWS = ["Sankey Diagram", "Pareto (Max → Min)", "Group Split",
             "Compare A vs B"]
GROUP_BY_CHOICES = ["Top Assembly", "Category", "Active / Non-active"]
DEPTH_CHOICES = ["All", "2", "3"]

# Palette for top-level assemblies (bars + ribbons inherit the ancestor's
# color); gray is reserved for the root and "Others" bands.
_BOM_PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
                "#0891b2", "#be185d", "#65a30d", "#b45309", "#4f46e5"]
_OTHER_COLOR = "#9ca3af"


def new_node(name, qty=1.0, cost=0.0, weight=0.0, category="Mechanical",
             active=False, children=None):
    """One BOM tree node. Leaves carry the numbers; assembly nodes carry
    children (their own cost/weight are ignored — totals roll up)."""
    return {"name": name, "qty": float(qty), "cost": float(cost),
            "weight": float(weight), "category": category,
            "active": bool(active), "children": list(children or [])}


def node_value(node, metric):
    """Rolled-up value of a node: qty × (own value for a leaf, else the sum
    of the children's rolled-up values). metric: 'cost' | 'weight'."""
    qty = float(node.get("qty", 1.0) or 0.0)
    children = node.get("children") or []
    if children:
        return qty * sum(node_value(c, metric) for c in children)
    return qty * float(node.get(metric, 0.0) or 0.0)


def iter_leaves(node, path=(), mult=1.0):
    """Yield (path_tuple, leaf_node, effective_qty) for every leaf part.
    effective_qty already includes every ancestor's qty."""
    qty = float(node.get("qty", 1.0) or 0.0)
    children = node.get("children") or []
    if not children:
        yield path + (node["name"],), node, mult * qty
        return
    for c in children:
        yield from iter_leaves(c, path + (node["name"],), mult * qty)


def flatten_parts(tree, metric):
    """Every leaf part as {name, path, assembly, value, category, active},
    with `value` = effective_qty × unit value. Unsorted."""
    out = []
    for path, leaf, eff_qty in iter_leaves(tree):
        out.append({
            "name": leaf["name"],
            "path": " / ".join(path[1:-1]),          # between root and leaf
            "assembly": path[1] if len(path) > 2 else leaf["name"],
            "value": eff_qty * float(leaf.get(metric, 0.0) or 0.0),
            "category": leaf.get("category", "Other"),
            "active": bool(leaf.get("active", False)),
        })
    return out


def group_totals(tree, metric, group_by="Top Assembly"):
    """Totals per group, sorted descending. group_by is one of
    GROUP_BY_CHOICES."""
    totals = {}
    for part in flatten_parts(tree, metric):
        if group_by == "Category":
            key = part["category"]
        elif group_by == "Active / Non-active":
            key = "Active" if part["active"] else "Non-active"
        else:
            key = part["assembly"]
        totals[key] = totals.get(key, 0.0) + part["value"]
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def sankey_layout(tree, metric, max_depth=None, min_share=0.02,
                  gap_frac=0.15):
    """Compute a left-to-right sankey layout for the BOM tree.

    Returns {"nodes": [...], "links": [...], "total": float, "ncols": int}.
    Each node: {label, depth, y0, y1, value, color_key} with y in [0, 1],
    y0 > y1 (spans are laid out top-down, biggest child first). Each link:
    {depth, py0, py1, cy0, cy1, color_key} joining a parent-side span segment
    to the child's span. Children under min_share of the GRAND total are
    folded into one "Others" node per parent (color_key None -> gray).
    gap_frac: fraction of each parent's span spent on gaps between children.
    """
    total = node_value(tree, metric)
    if total <= 0:
        return {"nodes": [], "links": [], "total": 0.0, "ncols": 0}
    nodes, links = [], []

    def visit(node, depth, y_top, y_bot, color_key, value):
        # `value` is passed explicitly: the drawn span is shrunk by the
        # inter-child gaps, so span × total would under-report it.
        nodes.append({"label": node["name"], "depth": depth,
                      "y0": y_top, "y1": y_bot,
                      "value": value,
                      "color_key": color_key})
        children = node.get("children") or []
        if not children or (max_depth is not None and depth + 1 > max_depth):
            return
        qty = float(node.get("qty", 1.0) or 0.0)
        vals = [(c, qty * node_value(c, metric)) for c in children]
        vals = [(c, v) for c, v in vals if v > 0]
        if not vals:
            return
        vals.sort(key=lambda cv: -cv[1])
        # Fold children below min_share of the grand total into "Others".
        kept = [(c, v) for c, v in vals if v / total >= min_share]
        others_v = sum(v for _, v in vals) - sum(v for _, v in kept)
        entries = kept + ([(None, others_v)] if others_v > 0 else [])

        span = y_top - y_bot
        subtotal = sum(v for _, v in entries)
        n = len(entries)
        gap = (span * gap_frac / (n - 1)) if n > 1 else 0.0
        usable = span - gap * (n - 1)
        cy = y_top          # child column cursor (with gaps)
        py = y_top          # parent-side cursor (contiguous, no gaps)
        for i, (child, v) in enumerate(entries):
            h = usable * v / subtotal
            ph = span * v / subtotal
            key = (color_key if depth > 0 else
                   (i if child is not None else None))
            if child is None:
                key = None
            links.append({"depth": depth, "py0": py, "py1": py - ph,
                          "cy0": cy, "cy1": cy - h, "color_key": key})
            if child is None:
                nodes.append({"label": "Others", "depth": depth + 1,
                              "y0": cy, "y1": cy - h, "value": v,
                              "color_key": None})
            else:
                visit(child, depth + 1, cy, cy - h, key, v)
            cy -= h + gap
            py -= ph

    visit(tree, 0, 1.0, 0.0, None, total)
    ncols = max(nd["depth"] for nd in nodes) + 1
    return {"nodes": nodes, "links": links, "total": total, "ncols": ncols}


def compare_groups(tree_a, tree_b, metric, group_by="Top Assembly"):
    """Group totals of two BOMs side by side: [(key, a_value, b_value), ...]
    over the union of group keys, sorted by max(a, b) descending. Groups
    missing on one side show 0 there (e.g. a hub motor has no 'Front End
    Cover Assembly')."""
    ga = group_totals(tree_a, metric, group_by)
    gb = group_totals(tree_b, metric, group_by)
    keys = sorted(set(ga) | set(gb),
                  key=lambda k: -max(ga.get(k, 0.0), gb.get(k, 0.0)))
    return [(k, ga.get(k, 0.0), gb.get(k, 0.0)) for k in keys]


def bom_to_rows(tree):
    """Flatten the tree to Excel-friendly rows (one per leaf part). Ancestor
    qty is folded into the exported Qty so values are preserved exactly."""
    rows = []
    for path, leaf, eff_qty in iter_leaves(tree):
        inner = path[1:-1]  # between root and the leaf itself
        rows.append({
            "Assembly": inner[0] if inner else "(top level)",
            "Sub-assembly": " / ".join(inner[1:]) if len(inner) > 1 else "",
            "Part": leaf["name"],
            "Qty": eff_qty,
            "Unit Cost (₹)": float(leaf.get("cost", 0.0) or 0.0),
            "Unit Weight (g)": float(leaf.get("weight", 0.0) or 0.0),
            "Category": leaf.get("category", "Other"),
            "Active": "Yes" if leaf.get("active", False) else "No",
        })
    return rows


def bom_from_rows(rows, root_name="Motor"):
    """Build a tree from flat rows (dicts keyed like bom_to_rows's output;
    header matching is case-insensitive and forgiving about units in the
    header text). Rows missing a Part name are skipped."""
    def pick(row, *names):
        for k, v in row.items():
            kl = str(k).strip().lower()
            for n in names:
                if kl.startswith(n):
                    return v
        return None

    root = new_node(root_name)
    assemblies = {}
    for row in rows:
        part = pick(row, "part")
        if part is None or str(part).strip() in ("", "nan"):
            continue
        asm_name = str(pick(row, "assembly") or "(top level)").strip()
        sub_name = str(pick(row, "sub") or "").strip()
        if sub_name.lower() == "nan":
            sub_name = ""

        def num(v, default):
            try:
                f = float(v)
                return default if np.isnan(f) else f
            except (TypeError, ValueError):
                return default

        leaf = new_node(
            str(part).strip(),
            qty=num(pick(row, "qty", "quantity"), 1.0),
            cost=num(pick(row, "unit cost", "cost"), 0.0),
            weight=num(pick(row, "unit weight", "weight"), 0.0),
            category=str(pick(row, "category") or "Other").strip().title() or "Other",
            active=str(pick(row, "active") or "").strip().lower()
            in ("yes", "y", "true", "1", "active"),
        )
        if leaf["category"] not in CATEGORY_CHOICES:
            leaf["category"] = "Other"

        if asm_name not in assemblies:
            assemblies[asm_name] = new_node(asm_name)
            root["children"].append(assemblies[asm_name])
        parent = assemblies[asm_name]
        if sub_name:
            sub = next((c for c in parent["children"]
                        if c["name"] == sub_name and c["children"]), None)
            if sub is None:
                sub = new_node(sub_name)
                parent["children"].append(sub)
            parent = sub
        parent["children"].append(leaf)
    return root


# ---------------------------------------------------------------------------#
#  Built-in starting templates (placeholder ₹ / g values — edit to program)  #
# ---------------------------------------------------------------------------#

def _p(name, qty, cost, weight, category, active=False):
    return new_node(name, qty, cost, weight, category, active)


MID_MOUNT_TEMPLATE = new_node("Mid-Mount Motor", children=[
    new_node("Stator Assembly", children=[
        _p("Stator Housing", 1, 650, 1800, "Mechanical"),
        _p("Stator Lamination Stack", 1, 1200, 2600, "Electrical", True),
        _p("Copper Winding", 1, 1800, 1500, "Electrical", True),
        _p("Slot Insulation / Liners", 1, 90, 60, "Electrical"),
        _p("Impregnation Varnish", 1, 120, 100, "Other"),
        _p("Potting Compound", 1, 150, 120, "Other"),
    ]),
    new_node("Rotor Assembly", children=[
        _p("Shaft", 1, 450, 900, "Mechanical"),
        _p("Rotor Lamination Stack", 1, 700, 1400, "Electrical", True),
        _p("Magnets (NdFeB)", 1, 1600, 450, "Electrical", True),
        _p("Balancing Weights", 1, 30, 20, "Mechanical"),
    ]),
    new_node("Bearings & Seals", children=[
        _p("Front Bearing", 1, 220, 120, "Mechanical"),
        _p("Rear Bearing", 1, 180, 90, "Mechanical"),
        _p("Oil Seal", 1, 60, 15, "Mechanical"),
        _p("Circlips / Retainers", 1, 25, 10, "Mechanical"),
    ]),
    new_node("Front End Cover Assembly", children=[
        _p("Front End Cover", 1, 380, 700, "Mechanical"),
        _p("Cover Gasket / O-ring", 1, 35, 10, "Mechanical"),
        _p("Cover Fasteners", 6, 8, 5, "Mechanical"),
    ]),
    new_node("Back End Plate Assembly", children=[
        _p("Back End Plate", 1, 350, 650, "Mechanical"),
        _p("Encoder Mount", 1, 90, 60, "Mechanical"),
        _p("Plate Fasteners", 6, 8, 5, "Mechanical"),
    ]),
    new_node("Sensing & Cables", children=[
        _p("Encoder / Resolver", 1, 700, 90, "Electronics"),
        _p("Temperature Sensor", 1, 120, 10, "Electronics"),
        _p("Phase Cables + Lugs", 1, 260, 350, "Electrical"),
        _p("Signal Harness + Connector", 1, 180, 80, "Electronics"),
    ]),
    new_node("Hardware & Misc", children=[
        _p("Name Plate & Labels", 1, 20, 10, "Other"),
        _p("Paint / Coating", 1, 60, 40, "Other"),
    ]),
])

HUB_TEMPLATE = new_node("Hub Motor", children=[
    new_node("Stator Assembly", children=[
        _p("Stator Holder / Spindle", 1, 550, 1600, "Mechanical"),
        _p("Stator Lamination Stack", 1, 1100, 2400, "Electrical", True),
        _p("Copper Winding", 1, 1700, 1400, "Electrical", True),
        _p("Slot Insulation / Liners", 1, 80, 55, "Electrical"),
        _p("Impregnation Varnish", 1, 110, 90, "Other"),
    ]),
    new_node("Rotor Assembly", children=[
        _p("Rotor Rim / Housing", 1, 900, 2800, "Mechanical"),
        _p("Rotor Back-Iron Ring", 1, 400, 1200, "Electrical", True),
        _p("Magnets (Ferrite/NdFeB)", 1, 1300, 600, "Electrical", True),
        _p("Magnet Adhesive", 1, 90, 30, "Other"),
    ]),
    new_node("Axle & Bearings", children=[
        _p("Axle", 1, 420, 1100, "Mechanical"),
        _p("Bearing", 2, 190, 100, "Mechanical"),
        _p("Axle Nuts & Washers", 1, 40, 60, "Mechanical"),
    ]),
    new_node("Left Cover Assembly", children=[
        _p("Left Side Cover", 1, 320, 600, "Mechanical"),
        _p("Cover Seal", 1, 30, 8, "Mechanical"),
        _p("Cover Fasteners", 6, 6, 4, "Mechanical"),
    ]),
    new_node("Right Cover Assembly", children=[
        _p("Right Side Cover", 1, 320, 600, "Mechanical"),
        _p("Cover Seal", 1, 30, 8, "Mechanical"),
        _p("Cover Fasteners", 6, 6, 4, "Mechanical"),
    ]),
    new_node("Sensing & Cables", children=[
        _p("Hall Sensor PCB", 1, 250, 30, "Electronics"),
        _p("Temperature Sensor", 1, 120, 10, "Electronics"),
        _p("Phase + Signal Cable (through axle)", 1, 300, 320, "Electrical"),
    ]),
])

BOM_TEMPLATES = {"Mid-Mount Motor": MID_MOUNT_TEMPLATE,
                 "Hub Motor": HUB_TEMPLATE}


def _fmt_val(value, metric):
    if metric == "cost":
        return f"₹{value:,.0f}"
    if value >= 1000:
        return f"{value / 1000.0:.2f} kg"
    return f"{value:.0f} g"


# ---------------------------------------------------------------------------#
#  Mixin: BOM section (templates, tree editor, Excel I/O) + plots            #
# ---------------------------------------------------------------------------#

class BomMixin:

    # ------------------------------------------------------------------ #
    #  Input section                                                     #
    # ------------------------------------------------------------------ #
    def _build_bom_section(self, input_frame):
        self.bom_tree = None
        self.bom_tree_b = None
        self.sections['bom'] = self.create_section(
            input_frame, "Motor BOM (Cost & Weight)", "#f1f5f9")
        frame = self.sections['bom']

        ctk.CTkLabel(
            frame,
            text=("Hierarchical bill of materials: assemblies → parts, each\n"
                  "with qty, unit cost (₹) and unit weight (g). Start from a\n"
                  "template or Excel, then edit in the tree below."),
            font=("Segoe UI", 10), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(6, 2))

        # --- Template / Excel data sources -----------------------------
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(2, 2))
        self.bom_template_combo = ctk.CTkComboBox(
            row, values=list(BOM_TEMPLATES.keys()), width=170)
        self.bom_template_combo.set("Mid-Mount Motor")
        self.bom_template_combo.pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="Load Template", width=110,
                      command=self._bom_load_template).pack(side="left")

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkButton(row, text="Import Excel", width=100,
                      command=self._bom_import_excel).pack(side="left", padx=(0, 6))
        self.bom_export_button = ctk.CTkButton(
            row, text="Export Excel", width=100, state="disabled",
            command=self._bom_export_excel)
        self.bom_export_button.pack(side="left", padx=(0, 6))
        self.bom_indicator = ctk.CTkLabel(
            row, text="❌", text_color=COLORS['warning'], font=("Segoe UI", 18))
        self.bom_indicator.pack(side="left", padx=(0, 6))
        self.bom_delete_button = ctk.CTkButton(
            row, text="Delete", fg_color=COLORS['warning'], text_color="white",
            width=60, state="disabled", command=self._bom_delete)
        self.bom_delete_button.pack(side="left")

        # --- BOM B (compare variant) ------------------------------------
        ctk.CTkLabel(
            frame,
            text=("BOM B (optional, for Compare A vs B — e.g. hub vs\n"
                  "mid-mount). The tree editor below edits A; use Swap to\n"
                  "bring B into the editor."),
            font=("Segoe UI", 10), text_color=COLORS['text_muted'],
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 2))
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkButton(row, text="Template → B", width=100,
                      command=self._bom_load_template_b).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="Excel → B", width=90,
                      command=self._bom_import_excel_b).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="Copy A → B", width=90,
                      command=self._bom_copy_a_to_b).pack(side="left")
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkButton(row, text="Swap A ↔ B", width=100,
                      command=self._bom_swap).pack(side="left", padx=(0, 6))
        self.bom_b_indicator = ctk.CTkLabel(
            row, text="❌", text_color=COLORS['warning'], font=("Segoe UI", 18))
        self.bom_b_indicator.pack(side="left", padx=(0, 6))
        self.bom_b_delete_button = ctk.CTkButton(
            row, text="Delete B", fg_color=COLORS['warning'],
            text_color="white", width=70, state="disabled",
            command=self._bom_delete_b)
        self.bom_b_delete_button.pack(side="left")

        # --- Tree editor ------------------------------------------------
        tree_wrap = ctk.CTkFrame(frame, fg_color="transparent")
        tree_wrap.pack(fill="x", padx=8, pady=(4, 2))
        self.bom_treeview = ttk.Treeview(
            tree_wrap, columns=("qty", "cost", "weight", "tcost", "tweight"),
            height=12, selectmode="browse")
        self.bom_treeview.heading("#0", text="Part / Assembly")
        self.bom_treeview.column("#0", width=170, stretch=True)
        for col, text, w in (("qty", "Qty", 36), ("cost", "₹/u", 52),
                             ("weight", "g/u", 52), ("tcost", "₹ tot", 60),
                             ("tweight", "g tot", 60)):
            self.bom_treeview.heading(col, text=text)
            self.bom_treeview.column(col, width=w, anchor="e", stretch=False)
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical",
                            command=self.bom_treeview.yview)
        self.bom_treeview.configure(yscrollcommand=vsb.set)
        self.bom_treeview.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self._bom_iid_map = {}

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(2, 4))
        for text, cmd in (("+ Assembly", self._bom_add_assembly),
                          ("+ Part", self._bom_add_part),
                          ("Edit", self._bom_edit_selected),
                          ("Remove", self._bom_remove_selected)):
            ctk.CTkButton(row, text=text, width=78, command=cmd).pack(
                side="left", padx=(0, 4))

        # --- Plot controls ----------------------------------------------
        row = self.create_control_row(frame, "Plot View")
        self.bom_view_combo = ctk.CTkComboBox(
            row, values=BOM_VIEWS, width=190,
            command=lambda _c: self._bom_replot())
        self.bom_view_combo.set(BOM_VIEWS[0])
        self.bom_view_combo.pack(side="right")

        row = self.create_control_row(frame, "Metric")
        self.bom_metric_combo = ctk.CTkComboBox(
            row, values=list(BOM_METRICS.keys()), width=130,
            command=lambda _c: self._bom_replot())
        self.bom_metric_combo.set("Cost (₹)")
        self.bom_metric_combo.pack(side="right")

        row = self.create_control_row(frame, "Sankey Depth")
        self.bom_depth_combo = ctk.CTkComboBox(
            row, values=DEPTH_CHOICES, width=100,
            command=lambda _c: self._bom_replot())
        self.bom_depth_combo.set("All")
        self.bom_depth_combo.pack(side="right")

        self.create_labeled_entry(frame, "Min Branch Share (%)", "2",
                                  "bom_min_share")

        row = self.create_control_row(frame, "Group / Color By")
        self.bom_group_combo = ctk.CTkComboBox(
            row, values=GROUP_BY_CHOICES, width=170,
            command=lambda _c: self._bom_replot())
        self.bom_group_combo.set(GROUP_BY_CHOICES[0])
        self.bom_group_combo.pack(side="right")

        self.bom_results_label = ctk.CTkLabel(
            frame, text="Load a template or Excel BOM to begin.",
            justify="left", font=("Segoe UI", 12),
            text_color=COLORS['primary'], anchor="w",
        )
        self.bom_results_label.pack(fill="x", padx=16, pady=(4, 8))

    def _bom_replot(self):
        if getattr(self, "plot_mode", None) == ANALYSIS_NAME:
            self.plot_graph()

    # ------------------------------------------------------------------ #
    #  Data sources: template / Excel / delete                           #
    # ------------------------------------------------------------------ #
    def _bom_set_loaded(self, loaded):
        if hasattr(self, "_set_indicator"):
            self._set_indicator("bom_indicator", loaded,
                                ["bom_export_button", "bom_delete_button"])
        if hasattr(self, "update_data_checklist"):
            self.update_data_checklist()

    def _bom_load_template(self):
        name = self.bom_template_combo.get()
        template = BOM_TEMPLATES.get(name)
        if template is None:
            return
        if self.bom_tree is not None and not messagebox.askyesno(
                "Replace BOM?",
                "A BOM is already loaded. Replace it with the "
                f"'{name}' template?"):
            return
        self.bom_tree = copy.deepcopy(template)
        self._bom_set_loaded(True)
        self._bom_refresh_tree()
        self.set_status(f"BOM template '{name}' loaded — placeholder values, "
                        "edit to your program.", "ok")
        self._bom_replot()

    def _bom_read_excel_tree(self, slot_label):
        """Pick an Excel file and parse it to a BOM tree, or None."""
        file_path = filedialog.askopenfilename(
            title=f"Select BOM Excel for {slot_label} (Assembly | "
                  "Sub-assembly | Part | Qty | Unit Cost | Unit Weight | "
                  "Category | Active)",
            filetypes=[("Excel Files", "*.xlsx;*.xls")])
        if not file_path:
            return None
        try:
            import pandas as pd
            df = pd.read_excel(file_path)
            tree = bom_from_rows(df.to_dict("records"))
            if not tree["children"]:
                raise ValueError("No parts found — needs at least 'Assembly' "
                                 "and 'Part' columns with data rows.")
        except Exception as exc:
            messagebox.showerror("BOM Import Error", str(exc))
            return None
        return tree

    def _bom_import_excel(self):
        tree = self._bom_read_excel_tree("BOM A")
        if tree is None:
            return
        self.bom_tree = tree
        self._bom_set_loaded(True)
        self._bom_refresh_tree()
        n = len(list(iter_leaves(tree)))
        self.set_status(f"BOM imported: {n} parts.", "ok")
        self._bom_replot()

    def _bom_export_excel(self):
        if self.bom_tree is None:
            return
        file_path = filedialog.asksaveasfilename(
            title="Export BOM to Excel", defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx")])
        if not file_path:
            return
        try:
            import pandas as pd
            pd.DataFrame(bom_to_rows(self.bom_tree)).to_excel(
                file_path, index=False)
            self.set_status(f"BOM exported to {file_path}", "ok")
        except Exception as exc:
            messagebox.showerror("BOM Export Error", str(exc))

    def _bom_delete(self):
        if self.bom_tree is not None and not messagebox.askyesno(
                "Delete BOM?", "Remove the loaded BOM from this session?"):
            return
        self.bom_tree = None
        self._bom_set_loaded(False)
        self._bom_refresh_tree()
        self._bom_replot()

    # --- BOM B (compare variant) ---------------------------------------
    def _bom_set_loaded_b(self, loaded):
        if hasattr(self, "_set_indicator"):
            self._set_indicator("bom_b_indicator", loaded,
                                ["bom_b_delete_button"])
        if hasattr(self, "update_data_checklist"):
            self.update_data_checklist()

    def _bom_load_template_b(self):
        name = self.bom_template_combo.get()
        template = BOM_TEMPLATES.get(name)
        if template is None:
            return
        if self.bom_tree_b is not None and not messagebox.askyesno(
                "Replace BOM B?",
                f"BOM B is already loaded. Replace it with the '{name}' "
                "template?"):
            return
        self.bom_tree_b = copy.deepcopy(template)
        self._bom_set_loaded_b(True)
        self.set_status(f"BOM B: template '{name}' loaded.", "ok")
        self._bom_replot()

    def _bom_import_excel_b(self):
        tree = self._bom_read_excel_tree("BOM B")
        if tree is None:
            return
        self.bom_tree_b = tree
        self._bom_set_loaded_b(True)
        self.set_status(f"BOM B imported: {len(list(iter_leaves(tree)))} "
                        "parts.", "ok")
        self._bom_replot()

    def _bom_copy_a_to_b(self):
        if self.bom_tree is None:
            self.set_status("Load BOM A first.", "error")
            return
        if self.bom_tree_b is not None and not messagebox.askyesno(
                "Replace BOM B?", "Replace BOM B with a copy of A?"):
            return
        self.bom_tree_b = copy.deepcopy(self.bom_tree)
        self.bom_tree_b["name"] = self.bom_tree.get("name", "Motor") + " (variant)"
        self._bom_set_loaded_b(True)
        self.set_status("BOM B = copy of A. Swap A ↔ B to edit it.", "ok")
        self._bom_replot()

    def _bom_swap(self):
        if self.bom_tree is None and self.bom_tree_b is None:
            self.set_status("Nothing to swap — load a BOM first.", "error")
            return
        self.bom_tree, self.bom_tree_b = self.bom_tree_b, self.bom_tree
        self._bom_set_loaded(self.bom_tree is not None)
        self._bom_set_loaded_b(self.bom_tree_b is not None)
        self._bom_refresh_tree()
        self.set_status("Swapped: the editor now shows the other BOM.", "ok")
        self._bom_replot()

    def _bom_delete_b(self):
        if self.bom_tree_b is not None and not messagebox.askyesno(
                "Delete BOM B?", "Remove BOM B from this session?"):
            return
        self.bom_tree_b = None
        self._bom_set_loaded_b(False)
        self._bom_replot()

    # ------------------------------------------------------------------ #
    #  Tree editor                                                       #
    # ------------------------------------------------------------------ #
    def _bom_refresh_tree(self):
        tv = self.bom_treeview
        for iid in tv.get_children(""):
            tv.delete(iid)
        self._bom_iid_map = {}
        if self.bom_tree is None:
            return

        def insert(node, parent_iid, parent_node):
            tc = node_value(node, "cost")
            tw = node_value(node, "weight")
            is_leaf = not self._bom_is_assembly(node)
            iid = tv.insert(
                parent_iid, "end", text=node["name"],
                values=(f"{node.get('qty', 1):g}",
                        f"{node.get('cost', 0):g}" if is_leaf else "",
                        f"{node.get('weight', 0):g}" if is_leaf else "",
                        f"{tc:,.0f}", f"{tw:,.0f}"),
                open=(parent_iid == ""))
            self._bom_iid_map[iid] = (node, parent_node)
            for c in node.get("children") or []:
                insert(c, iid, node)

        insert(self.bom_tree, "", None)

    def _bom_selected(self):
        sel = self.bom_treeview.selection()
        if not sel:
            return None, None
        return self._bom_iid_map.get(sel[0], (None, None))

    def _bom_add_assembly(self):
        self._bom_add_node(is_assembly=True)

    def _bom_add_part(self):
        self._bom_add_node(is_assembly=False)

    @staticmethod
    def _bom_is_assembly(node):
        """Assemblies have children; a freshly created (still empty) assembly
        carries an explicit 'asm' flag so it isn't mistaken for a part."""
        return bool(node.get("children")) or bool(node.get("asm"))

    def _bom_add_node(self, is_assembly):
        if self.bom_tree is None:
            self.bom_tree = new_node("Motor")
            self.bom_tree["asm"] = True
            self._bom_set_loaded(True)
        node, parent = self._bom_selected()
        # New children go under the selected assembly; if a leaf part is
        # selected, add as its sibling (parts can't contain parts).
        if node is None:
            target = self.bom_tree
        elif self._bom_is_assembly(node):
            target = node
        else:
            target = parent if parent is not None else self.bom_tree
        child = new_node("New Assembly" if is_assembly else "New Part")
        if is_assembly:
            child["asm"] = True
        self._bom_edit_popup(child, on_save=lambda: (
            target["children"].append(child),
            self._bom_refresh_tree(), self._bom_replot()),
            is_assembly=is_assembly)

    def _bom_edit_selected(self):
        node, _parent = self._bom_selected()
        if node is None:
            self.set_status("Select a row in the BOM tree first.", "error")
            return
        self._bom_edit_popup(node, on_save=lambda: (
            self._bom_refresh_tree(), self._bom_replot()),
            is_assembly=self._bom_is_assembly(node) or node is self.bom_tree)

    def _bom_remove_selected(self):
        node, parent = self._bom_selected()
        if node is None or parent is None:
            self.set_status("Select a (non-root) row to remove.", "error")
            return
        if not messagebox.askyesno("Remove?", f"Remove '{node['name']}' "
                                   "and everything under it?"):
            return
        try:
            parent["children"].remove(node)
        except ValueError:
            pass
        self._bom_refresh_tree()
        self._bom_replot()

    def _bom_edit_popup(self, node, on_save, is_assembly):
        """Small modal editor for one node. Assemblies only get name + qty
        (their cost/weight roll up from children)."""
        win = ctk.CTkToplevel(self)
        win.title("Edit Assembly" if is_assembly else "Edit Part")
        win.geometry("340x360")
        win.transient(self)
        win.grab_set()

        entries = {}

        def add_row(label, key, default):
            ctk.CTkLabel(win, text=label, anchor="w").pack(
                fill="x", padx=14, pady=(8, 0))
            e = ctk.CTkEntry(win)
            e.insert(0, str(default))
            e.pack(fill="x", padx=14)
            entries[key] = e

        add_row("Name", "name", node.get("name", ""))
        add_row("Qty (multiplies everything below)", "qty", node.get("qty", 1))
        if not is_assembly:
            add_row("Unit Cost (₹)", "cost", node.get("cost", 0))
            add_row("Unit Weight (g)", "weight", node.get("weight", 0))
            ctk.CTkLabel(win, text="Category", anchor="w").pack(
                fill="x", padx=14, pady=(8, 0))
            cat = ctk.CTkComboBox(win, values=CATEGORY_CHOICES)
            cat.set(node.get("category", "Mechanical"))
            cat.pack(fill="x", padx=14)
            active_var = ctk.BooleanVar(value=node.get("active", False))
            ctk.CTkCheckBox(win, text="Active part (electromagnetic path)",
                            variable=active_var).pack(
                fill="x", padx=14, pady=(8, 0))

        def save():
            name = entries["name"].get().strip()
            if not name:
                messagebox.showerror("BOM", "Name can't be empty.", parent=win)
                return
            try:
                qty = float(entries["qty"].get())
            except ValueError:
                messagebox.showerror("BOM", "Qty must be a number.", parent=win)
                return
            node["name"] = name
            node["qty"] = qty
            if not is_assembly:
                try:
                    node["cost"] = float(entries["cost"].get() or 0)
                    node["weight"] = float(entries["weight"].get() or 0)
                except ValueError:
                    messagebox.showerror("BOM", "Cost/Weight must be numbers.",
                                         parent=win)
                    return
                node["category"] = cat.get()
                node["active"] = bool(active_var.get())
            win.destroy()
            on_save()

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=14)
        ctk.CTkButton(btns, text="Save", command=save).pack(
            side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(btns, text="Cancel", fg_color=COLORS['warning'],
                      command=win.destroy).pack(side="left", expand=True,
                                                fill="x")

    # ------------------------------------------------------------------ #
    #  Plotting                                                          #
    # ------------------------------------------------------------------ #
    def plot_motor_bom(self):
        """Route to the selected BOM view. Called from dispatch.plot_graph
        (figure already cleared to a fresh single self.ax)."""
        self._bom_refresh_tree()
        if self.bom_tree is None:
            self.show_placeholder_message(
                "Load a BOM template or import an Excel BOM")
            self.canvas.draw()
            return
        metric = BOM_METRICS.get(self.bom_metric_combo.get(), "cost")
        view = self.bom_view_combo.get()
        if node_value(self.bom_tree, metric) <= 0:
            self.show_placeholder_message(
                "All values are zero for this metric — edit the BOM")
            self.canvas.draw()
            return
        if view == "Compare A vs B":
            if self.bom_tree_b is None:
                self.show_placeholder_message(
                    "Load BOM B (Template → B / Excel → B / Copy A → B)\n"
                    "to compare")
                self.canvas.draw()
                return
            self._bom_plot_compare(metric)
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
        elif view == "Sankey Diagram":
            self._bom_plot_sankey(metric)
        elif view == "Pareto (Max → Min)":
            self._bom_plot_pareto(metric)
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
        else:
            self._bom_plot_group_split(metric)
            if hasattr(self, "apply_graph_style"):
                self.apply_graph_style()
        self._bom_update_results()
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self.canvas.draw()
        self.set_status(f"BOM: {view} done.", "ok")

    def _bom_min_share_frac(self):
        errors = []
        pct = parse_float(self.bom_min_share, "Min Branch Share",
                          minimum=0, maximum=49, errors=errors)
        return 0.02 if errors or pct is None else pct / 100.0

    def _bom_color(self, key):
        if key is None:
            return _OTHER_COLOR
        return _BOM_PALETTE[int(key) % len(_BOM_PALETTE)]

    def _bom_plot_sankey(self, metric):
        import matplotlib.path as mpath
        import matplotlib.patches as mpatches

        depth_sel = self.bom_depth_combo.get()
        max_depth = None if depth_sel == "All" else int(depth_sel)
        layout = sankey_layout(self.bom_tree, metric,
                               max_depth=max_depth,
                               min_share=self._bom_min_share_frac())
        ax = self.ax
        ax.set_axis_off()
        total = layout["total"]
        ncols = max(layout["ncols"], 2)
        col_x = {d: d / (ncols - 1) * 0.82 for d in range(ncols)}
        barw = 0.014

        # Ribbons first (under the bars).
        P = mpath.Path
        for lk in layout["links"]:
            x0 = col_x[lk["depth"]] + barw
            x1 = col_x[lk["depth"] + 1]
            xm = (x0 + x1) / 2.0
            verts = [(x0, lk["py0"]), (xm, lk["py0"]), (xm, lk["cy0"]),
                     (x1, lk["cy0"]), (x1, lk["cy1"]), (xm, lk["cy1"]),
                     (xm, lk["py1"]), (x0, lk["py1"]), (x0, lk["py0"])]
            codes = [P.MOVETO, P.CURVE4, P.CURVE4, P.CURVE4, P.LINETO,
                     P.CURVE4, P.CURVE4, P.CURVE4, P.CLOSEPOLY]
            ax.add_patch(mpatches.PathPatch(
                P(verts, codes), facecolor=self._bom_color(lk["color_key"]),
                alpha=0.35, edgecolor="none"))

        for nd in layout["nodes"]:
            x = col_x[nd["depth"]]
            h = nd["y0"] - nd["y1"]
            ax.add_patch(mpatches.Rectangle(
                (x, nd["y1"]), barw, h,
                facecolor=self._bom_color(nd["color_key"]) if nd["depth"] else "#374151",
                edgecolor="white", linewidth=0.4))
            share = nd["value"] / total * 100.0
            label = f"{nd['label']}  {_fmt_val(nd['value'], metric)} ({share:.0f}%)"
            if nd["depth"] == 0:
                label = f"{nd['label']}  {_fmt_val(nd['value'], metric)}"
            if h >= 0.012 or nd["depth"] == 0:
                ax.text(x + barw + 0.006, (nd["y0"] + nd["y1"]) / 2.0, label,
                        va="center", ha="left",
                        fontsize=9 if nd["depth"] <= 1 else 8,
                        weight="bold" if nd["depth"] == 0 else "normal")
        ax.set_xlim(-0.02, 1.18)
        ax.set_ylim(-0.03, 1.05)
        metric_label = self.bom_metric_combo.get()
        ax.set_title(f"BOM Breakdown — {metric_label}  "
                     f"(total {_fmt_val(total, metric)})",
                     fontsize=14, weight="bold")

    def _bom_plot_pareto(self, metric):
        parts = sorted(flatten_parts(self.bom_tree, metric),
                       key=lambda p: -p["value"])
        parts = [p for p in parts if p["value"] > 0]
        shown = parts[:25]
        group_by = self.bom_group_combo.get()

        def key_of(p):
            if group_by == "Category":
                return p["category"]
            if group_by == "Active / Non-active":
                return "Active" if p["active"] else "Non-active"
            return p["assembly"]

        keys = list(dict.fromkeys(key_of(p) for p in shown))
        colors = {k: self._bom_color(i) for i, k in enumerate(keys)}
        ax = self.ax
        y = np.arange(len(shown))
        ax.barh(y, [p["value"] for p in shown],
                color=[colors[key_of(p)] for p in shown])
        ax.set_yticks(y)
        ax.set_yticklabels([p["name"] for p in shown], fontsize=8)
        ax.invert_yaxis()  # biggest on top (max -> min)
        metric_label = self.bom_metric_combo.get()
        ax.set_xlabel(metric_label)
        title = f"Parts Sorted Max → Min — {metric_label}"
        if len(parts) > len(shown):
            title += f"  (top {len(shown)} of {len(parts)})"
        ax.set_title(title, fontsize=14, weight="bold")
        # Cumulative-% line over the shown bars (Pareto), scaled onto the
        # value axis (no twin axes -- keeps apply_graph_style/dark mode
        # working on the one real axis).
        total = sum(p["value"] for p in parts)
        cum = np.cumsum([p["value"] for p in shown]) / total * 100.0
        xmax = shown[0]["value"] * 1.05
        ax.plot(cum / 100.0 * xmax, y, color="black", linestyle="--",
                linewidth=1.4, marker=".", markersize=5)
        step = max(1, len(y) // 8)
        for yy, cc in zip(y[::step], cum[::step]):
            ax.annotate(f"{cc:.0f}%", xy=(cc / 100.0 * xmax, yy), fontsize=7,
                        va="bottom", ha="left", color="black")
        ax.set_xlim(0, xmax * 1.06)
        import matplotlib.patches as mpatches
        ax.legend(handles=[mpatches.Patch(color=colors[k], label=k)
                           for k in keys]
                  + [mpatches.Patch(color="black", label="Cumulative %")],
                  fontsize=8, loc="lower right")

    def _bom_plot_group_split(self, metric):
        group_by = self.bom_group_combo.get()
        totals = group_totals(self.bom_tree, metric, group_by)
        ax = self.ax
        names = list(totals.keys())
        vals = list(totals.values())
        total = sum(vals) or 1.0
        x = np.arange(len(names))
        ax.bar(x, vals, color=[self._bom_color(i) for i in range(len(names))])
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=20, ha="right", fontsize=9)
        metric_label = self.bom_metric_combo.get()
        ax.set_ylabel(metric_label)
        ax.set_title(f"{metric_label} by {group_by} (Max → Min)",
                     fontsize=14, weight="bold")
        for xi, v in zip(x, vals):
            ax.annotate(f"{_fmt_val(v, metric)}\n{v / total * 100.0:.0f}%",
                        xy=(xi, v), ha="center", va="bottom", fontsize=8)
        ax.set_ylim(0, max(vals) * 1.18)

    def _bom_plot_compare(self, metric):
        """Grouped bars: BOM A vs BOM B per group, delta annotated on top.
        Green delta = B is lower (cheaper/lighter), red = B is higher."""
        rows = compare_groups(self.bom_tree, self.bom_tree_b,
                              metric, self.bom_group_combo.get())
        name_a = self.bom_tree.get("name", "A")
        name_b = self.bom_tree_b.get("name", "B")
        ax = self.ax
        x = np.arange(len(rows))
        w = 0.38
        ax.bar(x - w / 2, [r[1] for r in rows], w, color="#2563eb",
               label=f"A: {name_a}")
        ax.bar(x + w / 2, [r[2] for r in rows], w, color="#d97706",
               label=f"B: {name_b}")
        for xi, (_k, a, b) in zip(x, rows):
            d = b - a
            ax.annotate(f"{d:+,.0f}", xy=(xi, max(a, b)),
                        ha="center", va="bottom", fontsize=8,
                        color="#059669" if d <= 0 else "#dc2626")
        ax.set_xticks(x)
        ax.set_xticklabels([r[0] for r in rows], rotation=20, ha="right",
                           fontsize=9)
        metric_label = self.bom_metric_combo.get()
        ax.set_ylabel(metric_label)
        total_a = node_value(self.bom_tree, metric)
        total_b = node_value(self.bom_tree_b, metric)
        ax.set_title(
            f"A {_fmt_val(total_a, metric)}  vs  B {_fmt_val(total_b, metric)}"
            f"  (Δ B−A: {total_b - total_a:+,.0f})",
            fontsize=14, weight="bold")
        tallest = max(max(r[1], r[2]) for r in rows) if rows else 1.0
        ax.set_ylim(0, tallest * 1.18)
        ax.legend(fontsize=9)

    def _bom_update_results(self):
        tree = self.bom_tree
        cost = node_value(tree, "cost")
        weight = node_value(tree, "weight")
        parts = sorted(flatten_parts(tree, "cost"), key=lambda p: -p["value"])
        top3 = ", ".join(f"{p['name']} ({_fmt_val(p['value'], 'cost')})"
                         for p in parts[:3])
        act_c = group_totals(tree, "cost", "Active / Non-active")
        act_w = group_totals(tree, "weight", "Active / Non-active")
        cat_c = group_totals(tree, "cost", "Category")
        lines = [
            f"Total: {_fmt_val(cost, 'cost')}, {_fmt_val(weight, 'weight')} "
            f"({len(parts)} parts)"
            + (f";  ₹/kg = {cost / (weight / 1000.0):,.0f}" if weight > 0 else ""),
            f"Top cost drivers: {top3}",
            "Active share: "
            f"{act_c.get('Active', 0) / cost * 100.0:.0f}% of cost, "
            f"{act_w.get('Active', 0) / weight * 100.0:.0f}% of weight"
            if cost > 0 and weight > 0 else "Active share: n/a",
            "Cost by category: " + ", ".join(
                f"{k} {v / cost * 100.0:.0f}%" for k, v in cat_c.items())
            if cost > 0 else "",
        ]
        if self.bom_tree_b is not None:
            cost_b = node_value(self.bom_tree_b, "cost")
            weight_b = node_value(self.bom_tree_b, "weight")
            lines.append(
                f"B ({self.bom_tree_b.get('name', 'B')}): "
                f"{_fmt_val(cost_b, 'cost')}, {_fmt_val(weight_b, 'weight')}"
                f";  Δ B−A: ₹{cost_b - cost:+,.0f}, "
                f"{weight_b - weight:+,.0f} g")
        try:
            self.bom_results_label.configure(
                text="\n".join(l for l in lines if l))
        except Exception:
            pass
