# CLAUDE.md — Vehicle ↔ Motor Integration Suite (VMI)

Guidance for working in this repository. Read this before making changes — the
codebase grows by accretion and has several conventions that are easy to break.

---

## 1. What this project is

A **desktop powertrain assessment tool** for two/three-wheeler EVs (and IC
engines for comparison). It is a CustomTkinter + matplotlib GUI that an engineer
uses to size a motor against a vehicle and evaluate:

- **Torque / Force / Acceleration** curves at the wheel or at the motor
- **Parametric studies** (effect of CdA / Crr on top speed, acceleration, gradability)
- **Drive cycle** plotting and torque–speed scatter / heatmaps over a cycle
- **Drive cycle efficiency** with motor efficiency maps (Motor 1 vs Motor 2, difference map)
- **Engine analysis** (multi-gear IC engine torque/force at the wheel)
- **Compare standard motor data** (overlay a saved library of reference motors)
- **Range analysis** (battery pack model → power/energy/C-rate/loss/range over a cycle)

It is single-user, runs locally, reads/writes Excel and JSON, and exports
figures, data, HTML reports, and scenarios.

This is an **engineering tool: numbers must stay correct.** Several modules carry
the note *"copied VERBATIM from the original program — the numbers are
unchanged."* Treat the physics/formulas as a contract. Do not "tidy up" a
formula, change rounding, or alter a default value without an explicit request —
those defaults are calibrated against real vehicles.

---

## 2. How to run

```bash
python main.py        # primary entry point
python -m vmi         # equivalent (vmi/__main__.py)
```

There is **no build step**. It is a plain Python GUI app. There IS now a small
test suite: `python -m pytest tests/` runs golden-value regression tests over
`physics.py`, `calc_ext.py`, `units.py`, `formatting.py`, and `validation.py`.
**These tests lock the calibrated numbers** — a failure means a formula
changed, which must only happen on an explicit request. GUI behavior still
needs manual verification by running the app.

### Dependencies (install with `pip install -r requirements.txt`)
`customtkinter`, `matplotlib`, `numpy`, `pandas`, `scipy`, `seaborn`,
`pillow` (PIL), `openpyxl` (pandas Excel backend), plus `requests`, `chromadb`,
`pypdf`, `python-docx` for the assistant sidebar (§7c). Standard-library
`tkinter` must be present (ships with CPython on Windows).

Versions in `requirements.txt` are pinned to the tested set (done 2026-07).

### Sample test data
`python generate_sample_data.py` writes ready-to-load dummy Excel files into
`sample_data/` — one per upload slot (drive cycle, motor data, engine torque/RPM,
gear efficiency G1..G6, two efficiency maps, three MTPA/MTPV d-q saturation maps
`mtpa_{ld,lq,psi}_map.xlsx`). They match the exact formats the loaders expect;
use them to exercise every analysis without real data.

### Optional assets (app starts fine without them)
Place next to `main.py`:
- `std_motor_data_sample.json` — the standard-motor library (the app
  reads it on startup and **rewrites it** when you save a motor). Missing → starts
  with an empty library.
- `tvs_logo.webp`, `motor.jpg` — header images.

### Range analysis section flow
`analysis_sections["Range analysis"]` order is deliberate:
`['drivecycle_data', 'efficiency_data', 'range_plot', 'range_battery',
'range_efficiency', 'vehicle', 'dynamics', 'motor', 'env']` — Input Data (drive
cycle, then Motor/Controller maps) → Plot View (foldable list) → Battery Inputs
→ everything else. There is no `range_advanced` section anymore; its four
controls were relocated into sections that are already shared across analyses,
so they show up wherever that section appears rather than being Range-only:
- `alt_density_toggle` / `altitude_m` → **Environment Conditions** (`env`)
- `crr_speed_coeff` (Crr1, velocity-dependent rolling resistance) → **Vehicle
  Parameters** (`vehicle`)
- `regen_cap_w` / `integration_method` → **Range Analysis - Battery Inputs**
  (`range_battery`), since they're battery-facing, not physics-facing
Attribute names are unchanged, so `range_analysis.py`'s `getattr(self,
"alt_density_toggle", None)` etc. needed no code changes — only their parent
frame moved. `range_plot_toggle` (the 9-way view selector) is a `CTkComboBox`
(a folding dropdown) rather than a `CTkSegmentedButton`, for the same reason:
same `.get()`/`.set()`/`command=` contract, far less vertical/horizontal space.
`create_control_row(parent, label)` in `ui_helpers.py` is the shared
label-left/control-right row builder for non-entry widgets (switches, segmented
buttons) — reuse it instead of hand-rolling a frame.

### Motor capability envelope masking
Every torque-vs-RPM efficiency contour (`plot_efficiency_map_motor1/2`,
`plot_efficiency_map_combined`, `plot_efficiency_difference_map`, and the Range
`_plot_range_efficiency_map_panel`) is clipped to what the motor can physically
reach via `_motor_capability_mask()`: peak torque up to rated (base) speed, then
power-limited (T=P/ω) out to max speed, nothing beyond max speed — symmetric in
torque so it covers regen too. Points outside the envelope are set to NaN before
`contourf`, so they render as blank rather than an interpolated/extrapolated
color. All four map views (including the Controller map) mask against the
**Motor's** own envelope (`get_motor_params(motor=1)`), since the controller
can't drive the shaft past what the motor itself can reach. If Motor Input
Parameters aren't filled in yet, `_motor_capability_mask()` returns `None` and
the map shows unmasked (no behavior change until those fields are populated).
Three related contracts (2026-07):
- **Uploaded NaN cells stay NaN.** `_normalize_efficiency_map_data` no longer
  back-fills blank cells with the median — datasheet maps leave the region
  above the torque-speed curve empty, and filling it painted "valid"
  efficiency there. Downstream interpolation already substitutes the constant
  default for NaN results.
- **Autofill respects map holes.** `_autofill_motor_params_from_map` takes the
  eff matrix; when it contains NaN holes, Max Power = max |T|·ω over the
  *populated* cells (≈ the real corner power) and Max Torque = max |T| with
  data. Fully populated maps keep the old full-rectangle values exactly.
- **The envelope is always drawn.** `_draw_motor_capability_curve()` overlays
  the Motor-1 mask boundary (flat peak torque to base RPM = P/T, then P/ω) on
  all five map views so the user can visually confirm nothing is colored above
  it. Locked by tests/test_eff_map_nan.py alongside test_capability_mask.py.
- **Grid lines render above the colormap.** `setup_plot_style()`'s
  `seaborn-v0_8-whitegrid` base style sets `axes.axisbelow=True` globally,
  which puts gridlines below *every* artist including filled contours/hexbins
  -- invisible under a map's fill. `apply_graph_style()` now calls
  `ax.set_axisbelow('line')` (matplotlib's real default: above patches, below
  plotted lines) before drawing the grid; `_plot_range_efficiency_map_panel`
  sets it directly since Range has no Graph Settings panel to route through.
- **Extrapolate-to-envelope toggle (Drive Cycle Efficiency only).** A
  datasheet map's own blank cells mostly line up with the capability mask's
  rejection region already, but `contourf` kills an ENTIRE grid quad the
  moment any one of its four corners is NaN -- so a handful of rejected nodes
  right next to the curve can visually erase a much bigger swath of already-
  valid, accepted cells than the mask itself rejects. The
  `extrapolate_gaps` Graph Settings toggle (off by default): (1)
  `_extrapolate_eff_gaps()` nearest-neighbor-fills NaN cells on the map's own
  axis, then (2) `_dense_regrid_eff()` bilinearly resamples onto a dense
  200x200 grid -- the same fine-grid technique Combined/Difference already use
  unconditionally -- before the capability mask (which always still wins) is
  applied. Wired into Motor 1/2; Combined/Difference get the native-grid
  extrapolation step only, since they already interpolate onto a dense common
  grid regardless of the toggle.

### Drive Cycle Efficiency (Motor × Controller)
`efficiency.py` computes drive-cycle efficiency two ways via
`compute_drive_cycle_efficiency_metrics()`: **energy-based** (sum each point's
mechanical energy ÷ the battery energy it needs = mech/(η_motor·η_controller)
while motoring) and **average-of-points** (unweighted mean of per-point combined
η). Regen is included — braking energy × combined η, optionally capped by the
Range `regen_cap_w` field. `_drive_cycle_operating_points()` is the shared
force-model helper (same equations as `range_analysis.py`) used by both the
metrics and the map overlay so they never diverge. Views: `plot_efficiency_map_motor1`
(Motor), `_motor2` (Controller), `plot_efficiency_map_combined`,
`plot_efficiency_difference_map` (Controller−Motor), `plot_efficiency_map_regen`
(Regen/braking, Motor only). The drive-cycle overlay
(`_overlay_drive_cycle_on_efficiency_plot`) supports Scatter / Heatmap / Both via
the `overlay_*` Graph-Settings keys (hexbin weighted by point count or tractive Wh).
- **Regen (Braking) Efficiency Map** (2026-07): no datasheet map measures
  regen efficiency separately, and the app already *assumed* motoring
  efficiency applies to braking too (`_interpolate_efficiency_or_constant`
  looks maps up via `np.abs(torque)`, and `_motor_capability_mask` is
  symmetric in torque) — but nothing ever showed that assumption visually.
  `plot_efficiency_map_regen` mirrors the Motor 1 map about T=0 (negating the
  torque axis and reversing the matrix rows, dropping a duplicate T=0 row if
  present) and plots only the resulting T≤0 half, with the capability mask
  and `_draw_motor_capability_curve`'s existing negative-torque mirroring
  applied the same as every other map. Because `contourf`'s mesh still spans
  the (all-NaN) positive half, the view explicitly clips `ax.set_ylim` to the
  negative range afterward — otherwise half the plot would be blank space.
  `_overlay_drive_cycle_on_efficiency_plot` gained a `regen=False` parameter;
  `regen=True` overlays braking points (`motor_torque < 0`) instead of
  motoring ones. Shares the "Drive Cycle Efficiency" Graph Settings namespace
  with the other four map views (same cmap/levels/extrapolate/overlay
  controls — no separate schema entry needed, since none of these views are
  behind a sub-mode selector the way Compare Standard Motor Data's four
  radios are). Wired into the report generator right after the Motor map.

### Platform
Developed on **Windows 11**. `theme.apply_appearance()` calls
`ctk.deactivate_automatic_dpi_awareness()` **before** the CTk window is built:
CustomTkinter's 100 ms `check_dpi_scaling` loop reconfigures every registered
widget on a DPI change, but destroyed CTkComboBox dropdowns (the Graph Settings
panel is rebuilt often) stay registered → `TclError: invalid command name
...dropdownmenu`, and because that loop drops the window to alpha 0.15 during the
rescale, the crash left the whole window stuck semi-transparent. Keep the tracker
off unless you re-solve the dangling-dropdown cleanup. Some glyphs in source are mojibake (`Â²` for `²`,
emoji in comments) from an earlier encoding round-trip — cosmetic, in labels and
comments only. Keep new strings clean UTF-8; don't mass-rewrite the existing ones
unless asked (risk of touching a label a user recognizes).

---

## 3. Architecture — the mixin pattern (read this first)

The entire application is **one class, `TorqueSpeedApp`**, assembled from many
mixins. This is the single most important thing to understand.

```
vmi/app.py
  class TorqueSpeedApp(
      EnhancementsMixin,
      HelpersMixin, LimitsMixin, TorqueForceMixin, ParametricMixin,
      DriveCycleMixin, EngineMixin, EfficiencyMixin, RangeAnalysisMixin,
      CompareStdMixin, DataIOMixin, DownloadsMixin, DispatchMixin,
      ctk.CTk,
  )
```

- **`app.py::__init__`** builds the *entire* widget tree (~960 lines) and stores
  every input widget as an attribute on `self` (e.g. `self.m_ref`,
  `self.peak_torque`, `self.xlim_rpm_motor`). All mixins read inputs by calling
  `self.<widget>.get()`.
- Each mixin is a `class XxxMixin:` with **no `__init__`** — it only contributes
  methods. They all share the one `self`. There is **no encapsulation between
  mixins**: any method can read/write any `self.*` attribute or widget. This is
  powerful and dangerous — a renamed attribute breaks code in a different file
  with no import to flag it.
- Mixin order in the `class` statement is the **MRO**. `EnhancementsMixin` is
  first so its wrappers win. If two mixins ever define the same method name, the
  earliest in the list wins — grep before naming a new method.

### Module map

| File | Mixin / role | Responsibility |
|------|--------------|----------------|
| `app.py` | `TorqueSpeedApp.__init__` | Builds all widgets, sections, the `analysis_sections` map, the figure/canvas. |
| `theme.py` | (module) | `COLORS`, `FONTS`, CTk + matplotlib styling, dark-plot post-processor. |
| `physics.py` | (module) | `calculate_crr_cd_a()` + the reference-mass lookup `df`, gravity `g`. **Core formulas — verbatim.** |
| `calc_ext.py` | (module) | Optional/advanced physics helpers: ISA air density, speed-dependent rolling force, trapezoidal energy, regen cap, invariant checks. |
| `formatting.py` | (module) | `fmt`, `fmt_wh`, `fmt_km`, `fmt_pct` number formatters. |
| `units.py` | (module) | Tested km/h↔m/s↔RPM↔rad/s conversion helpers. New code should use these; existing verbatim plot code may keep its inline math. |
| `applog.py` | (module) | Rotating error log (`vmi_app.log`). Uncaught Tk-callback errors are logged via the `report_callback_exception` hook installed in `app.py`; use `from .applog import logger` for module-level error logging. |
| `validation.py` | (module) | `parse_float()` with red-border marking, `ValidationError`, `clear_marks`. |
| `ui_helpers.py` | `HelpersMixin` | `create_section`, `create_labeled_entry`, header, menu bar, axis-clearing, all the `on_*_manual_edit` flag setters, tyre→radius. |
| `limits.py` | `LimitsMixin` | All axis-limit logic (`get_x_limits`, `get_y_limits`, force variants, compare-std limits). |
| `dispatch.py` | `DispatchMixin` | **The router.** `plot_graph()` reads inputs and dispatches to the right plot method by `self.plot_mode`; `show_sections_for_analysis()` shows/hides input sections; `update_plot()`. |
| `torque_force.py` | `TorqueForceMixin` | `plot_torque_graph`, `plot_force_graph`, `plot_vehicle_max_speed_vs_time`, intersection annotation. |
| `parametric.py` | `ParametricMixin` | Parametric sweeps + the estimators (top speed, accel time, gradability). |
| `drive_cycle.py` | `DriveCycleMixin` | Drive-cycle plotting, torque-speed-over-cycle scatter/heatmap, cycle property stats. |
| `engine.py` | `EngineMixin` | IC-engine multi-gear analysis, gear-efficiency interpolation, sync engine curve into motor inputs. |
| `efficiency.py` | `EfficiencyMixin` | Efficiency-map reading/normalizing/interpolation, Motor 1/2 maps, difference map, drive-cycle overlay. |
| `range_analysis.py` | `RangeAnalysisMixin` | `plot_power_energy_cycle()` — the full battery/range model and its multi-panel plot. |
| `mtpa_mtpv.py` | `MtpaMtpvMixin` + `solve_mtpa_mtpv()` | "MTPA / MTPV (PMSM)" analysis: pure d-q solver (module function, tested in tests/test_mtpa_mtpv.py) + input section + 4 plot views. Short-circuits in `dispatch.plot_graph` before vehicle-input parsing (like Range). Model per `knowledge_base/scenarios/MTPA_MTPV.pdf`; stator resistance neglected. Region classification is geometric (MTPV = optimum detached from the current circle), not torque-comparison. Current spec: `phase_peak_current()` converts Line/Phase × RMS/Peak × Star/Delta to the solver's peak phase current; `dc_link_to_vmax()` maps Vdc through the selected PWM scheme (SVPWM Vdc/√3 default, sine Vdc/2, six-step 2Vdc/π). Base speed is analytic (Vmax/(p·\|ψ_MTPA\|)), not the last feasible speed-grid sample. Optional Ld/Lq/ψ_PM saturation maps over an (id×iq) Excel grid (`mtpa_{ld,lq,psi}_map` dicts `{'id','iq','m'}`, mH/mH/Wb, auto-detects H; \|id\| axes accepted) switch the solver to a dense-grid + circle-boundary search; missing maps fall back to the constant entries, no maps = original path (golden-locked). Maps persist via `_dataset_slots()` and show in the data checklist. Graph Settings are applied per panel by `_mtpa_apply_gs` (the "All" view has 4 axes; `apply_graph_style` only handles one). |
| `mechanical_design.py` | `MechanicalDesignMixin` + pure formula functions | "Mechanical Design (Motor)" analysis (2026-07): five hand-calc design checks from `knowledge_base/standards/EV_Motor_Mechanical_Design_Formula_Handbook.md` (Shigley/Roark/Timoshenko/DIN 7190/ISO 281/ISO 21940-11) — Rotor Stress & Burst Speed, Shaft Design (Static + Fatigue), Press / Shrink Fit, Bearing Life (L10), Critical Speed & Balancing. A "Design Check" combobox swaps between five per-check input subframes (`_mech_sync_subframe`; when the section is collapsed it edits the section's `_vmi_saved` re-pack list instead of packing, and `plot_mechanical_design` re-syncs so session-restored combo values show the right inputs). Each check has one single-axis plot (stress-vs-radius, SF-vs-diameter, pressure-vs-speed with loss-of-contact, life-vs-load, critical-speed-vs-diameter) + a `mech_results_label` numeric summary quoting the handbook's target safety factors. All formula functions are module-level pure functions (SI) golden-locked in tests/test_mechanical_design.py — note `de_goodman_diameter` uses Shigley's real Eq. 7-8 coefficients (sqrt(4(Kf·M)²+3(Kfs·T)²)); the handbook's own §2.5 transcription drops the factor 2 (verified by SF round-trip). Short-circuits in `dispatch.plot_graph` before vehicle-input parsing (like MTPA/Range); no uploads, no `efficiency_data` injection. Report inputs come from `_mech_report_input_rows()` (walks the active subframe's widgets, so it never goes stale). |
| `bom.py` | `BomMixin` + pure tree/layout helpers | "Motor BOM (Cost & Weight)" analysis (2026-07): ONE nested plain-dict tree `self.bom_tree` (`{name, qty, cost ₹/u, weight g/u, category, active, children[]}`) — nothing architecture-specific is hard-coded, hub vs mid-mount are just different trees. Totals ALWAYS roll up from leaves; `qty` multiplies its whole subtree. Sources: built-in `BOM_TEMPLATES` (Mid-Mount / Hub, placeholder values), Excel import/export (flat rows Assembly \| Sub-assembly \| Part \| Qty \| Unit Cost \| Unit Weight \| Category \| Active; ancestor qty folded into exported Qty so values round-trip exactly; forgiving header matching), and an in-app ttk.Treeview editor (add assembly/part, edit popup, remove — freshly created empty assemblies carry an `asm: True` flag so `_bom_is_assembly` doesn't mistake them for parts). Views (all driven by one Metric combo, Cost/Weight): custom-drawn Sankey (`sankey_layout()` pure function — depth limit + "min branch share %" folds small children into gray "Others" bands per parent; node `value` is passed explicitly, NOT span×total, since spans are gap-shrunk), Pareto max→min with cumulative-% line drawn scaled onto the value axis (deliberately no twin axes — keeps `apply_graph_style`/dark-mode working), Group Split (Top Assembly / Category / Active–Non-active). Sankey is axis-off so it skips `apply_graph_style`; the other views apply it. **Compare mode:** an optional second tree `self.bom_tree_b` (Template → B / Excel → B / Copy A → B; "Swap A ↔ B" exchanges the two so B can be edited — the tree editor always edits A) feeds the "Compare A vs B" view: `compare_groups()` (pure) takes the UNION of group keys sorted by max(a,b) desc, so architecture-only assemblies (hub's Axle vs mid-mount's Front End Cover) show one-sided bars with 0 on the other; deltas annotated green (B lower) / red (B higher). Both trees persist via `_dataset_slots()` (plain dicts — `_enc_obj` recurses them). Report renders Sankey-Cost, Sankey-Weight, Pareto-Cost, plus Compare-Cost when B is loaded. Pure helpers golden-locked in tests/test_bom.py. |
| `compare_std.py` | `CompareStdMixin` | Standard-motor comparison table + overlay plotting (torque/force/acceleration/efficiency — see §7e). |
| `data_io.py` | `DataIOMixin` | All Excel/JSON load/save + the column-picker popups + delete handlers. |
| `downloads.py` | `DownloadsMixin` | Save current figure / export torque-speed data to Excel. |
| `enhancements.py` | `EnhancementsMixin` | Cross-cutting *new* features: toolbar, status bar, figure/data export, multi-analysis HTML report, save/load scenario, dark-plot toggle, Enter-to-plot, `_safe_plot` error wrapper, loss waterfall. |
| `graph_settings.py` | `GraphSettingsMixin` | Per-analysis "Graph Settings" panel (line colors/styles/widths, grid/legend/title sizes, colormap, contour levels). Schema-driven; values in `self._gs_values`; read in plot code via `self.gs_*()`. |
| `assistant.py` | `AssistantMixin` | Collapsible chat sidebar (local LLM + RAG). See §7c. |
| `llm_client.py` | (module) | Thin `requests` wrapper around a local Ollama server (`chat()`, `embed()`). |
| `rag_store.py` | (module) | Chroma-backed knowledge-base ingestion (`rebuild_index()`) and retrieval (`query()`). |

---

## 4. The central control flow

Understanding one round-trip lets you navigate everything:

1. User picks an **Analysis Type** in `self.plot_type` (combobox in `app.py`),
   or presses **Update Plot** / **Enter**.
2. `DispatchMixin.update_plot()` sets `self.plot_mode = self.plot_type.get()`,
   then calls `show_sections_for_analysis()` then routes through
   `EnhancementsMixin._safe_plot()` (busy cursor, red-bordered bad fields,
   errors to the status bar) which dispatches to `plot_graph()` or
   `update_compare_std_plot()`. `plot_graph()` itself validates its inputs
   with `validation.parse_float` (collect-all-errors, no modal spam) before
   computing anything.
3. `show_sections_for_analysis()` uses **`self.analysis_sections`** (a dict in
   `app.py`, ~line 894) mapping each analysis name → the list of section keys to
   show. It `pack_forget()`s everything then re-packs the relevant sections.
4. `plot_graph()` reads inputs, computes `params` via `calculate_crr_cd_a`,
   resolves axis limits, then branches on `self.plot_mode` to the matching
   `plot_*` method. `Range analysis` short-circuits to
   `plot_power_energy_cycle()` before the generic input parsing.
5. The chosen `plot_*` method draws on `self.ax` / `self.figure` and the wrapped
   `self.canvas.draw()` applies dark mode if enabled.

**Torque and Force are one merged analysis called `"Powertrain Sizing"`.** The
dropdown has a single `"Powertrain Sizing"` entry (there is no separate `"Torque"`
or `"Force"` analysis); the **`self.output_combo`** selector ("Torque" / "Force")
in the *Plot Mode* section chooses which quantity is plotted. Both share the same
x-axis controls — `Speed Unit` (RPM / Km/hr) and `Plotting Part` (At Wheel / At
Motor) — so `get_x_limits` and the km/h↔RPM(wheel/motor) x-limit entries drive
**both**. For Force the y-axis is always *wheel force (N)*; because force is a wheel
quantity, **`Plotting Part` is auto-locked to "At Wheel" (disabled) whenever Output
= Force** via `_sync_plot_part_lock()` (called from `update_plot` and at end of
`__init__`); Speed Unit still switches the force x-axis between km/h and wheel RPM.
`dispatch.plot_graph` branches on `self.output_combo.get()` inside the
`plot_mode == "Powertrain Sizing"` case; `get_y_limits` detects Force via
`output_combo` (returns `ylim_wheel_force`); `show_sections_for_analysis` shows the
Nm vs N y-limit fields by output.

Torque and Force keep **separate Graph Settings** even though they share the
analysis: `graph_settings.SCHEMA` has keys `"Powertrain Sizing"` and
`"Powertrain Sizing::Force"`, and `_gs_analysis()` (used by `gs()` and the
`populate_graph_settings` call in `dispatch`) resolves to the Force namespace when
Output = Force. So grid spacing / colors / etc. set for torque do not bleed into
force. A `legend_loc` control in `_UNIVERSAL_LINE` sets legend position for every
single-axis analysis (applied in `apply_graph_style`).

**Loading a motor curve from Excel auto-replots** — `load_motor_data_excel`'s
`on_confirm` (and `delete_motor_data`) call `update_plot()` so the plot and the
auto x/y limits refresh without pressing *Update Plot*.

**If you add a new analysis mode you must touch all of:**
- `self.plot_type` values list (`app.py`)
- `self.analysis_sections` map (`app.py`)
- a new section built in `__init__` (if it needs inputs)
- a `plot_*` method
- the `if/elif self.plot_mode == ...` branch in `dispatch.py::plot_graph`
- `show_sections_for_analysis` if the mode needs special section/sim-field handling

---

## 5. State & data conventions (don't break these)

### Input widgets
- `create_labeled_entry(parent, label, default, var_name)` creates a `CTkEntry`
  and assigns it to `self.<var_name>`. **`var_name` is the contract** — many
  files read `self.<var_name>.get()`. Renaming one is a cross-file change; grep
  the whole `vmi/` package first.
- Numeric inputs are read as **strings** via `.get()` and cast with `float(...)`.
  A blank field is meaningful in several places (e.g. blank `crr`/`cd_a` ⇒
  auto-calculate). Preserve blank-handling semantics.

### "Manual edit" flags
A recurring pattern: a field has auto-computed defaults **unless the user typed
in it**. Each such field has a `self.<name>_manual` boolean and an
`on_<name>_manual_edit` / inline `<KeyRelease>` binding that sets the flag.
`plot_graph()` only overwrites the field when the flag is `False`. Examples:
`xlim_manual`, `xlim_rpm_vehicle_manual`, `xlim_rpm_motor_manual`, `ylim_manual`,
`ylim_wheel_manual`, `crr_manual`, `cda_manual`, `motor1_max_speed_manual`, …
**If you add an auto-filled field, add the matching `_manual` flag + binding**,
or you'll clobber user input on every replot.

### DataFrames (canonical column names — rename map is the API)
Excel loads go through column-picker popups that **rename to fixed internal
names**. Downstream code depends on these:
- Drive cycle → `self.dataframe` with columns `dc_time`, `dc_speed` (km/h)
- Motor data → `self.motor_dataframe` with `motor_torque`, `motor_speed` (RPM);
  also sets `self.motor_curve_source = "uploaded_motor"`
- Engine data → `self.engine_dataframe`
- Efficiency maps → `self.efficiency_data_1/2` plus parsed
  `eff{1,2}_map_torques / _rpms / _matrix`. **Slot 1 = Motor, slot 2 = Controller**
  (the "Efficiency Maps (Motor & Controller)" section, `analysis_sections` key
  `efficiency_data`, is now injected into *every* analysis as one shared source of
  truth; Range's `_resolve_range_efficiency_map` already assumed this mapping).
  Combined powertrain efficiency = motor × controller.
- Range maps → `self.range_motor_efficiency_map`, `self.range_controller_efficiency_map`

`None` means "not loaded"; code guards with `hasattr(...)` / `is not None`.
Delete handlers reset to `None`, flip the `✅/❌` indicator label, and disable
the relevant buttons. Follow that exact triplet (data, indicator, button-state)
when adding an uploadable dataset.

### The standard-motor library
`std_motor_data_sample.json` is loaded into `self.std_motor_data` on startup and
**overwritten** by `save_std_motor_data_popup`. It is the persisted state of the
app — treat writes carefully.

---

## 6. Physics / formula reference

- `physics.calculate_crr_cd_a(m_ref, rear_load_ratio, ambient_temp,
  ambient_pressure, crr, cd_a)` — looks up a mass band in `df`, derives `Crr`
  from coefficient `a` and `CdA` from coefficient `b` (temperature/pressure
  corrected), or uses manually supplied values. Returns
  `{"m_i", "Crr", "CdA"}`, rounded to 5 dp. **The lookup table `data` and the two
  formulas are calibration data — do not edit values.**
- `calc_ext.py` holds the **opt-in advanced physics** wired to the "Advanced
  Physics (optional)" section: ISA air density vs altitude/temp, velocity-
  dependent rolling resistance (`crr1`), trapezoidal vs rectangular energy
  integration, and a regen power cap. The UI defaults reproduce the original
  model exactly; keep that property — advanced features must be no-ops when off.
---

## 7. Conventions to follow when editing

- **Match the surrounding style.** This code is verbose, explicit, and uses
  `try/except Exception: pass` liberally to keep the GUI alive on bad input.
  New GUI wiring should fail soft the same way; surface errors via
  `self.set_status(msg, "error")` or a `messagebox`, not an uncaught exception.
- **Colors and fonts come from `theme.COLORS` / `theme.FONTS`.** Don't hardcode
  hex literals in widget code — add/look up a key in `theme.py`.
- **Don't reach into matplotlib globals.** Per-plot methods set their own
  colors/sizes; dark mode is applied as a *post-process* to the finished figure
  in `theme.apply_dark_to_figure` so it works for every plot without editing each
  one. Keep that pattern — don't bake dark colors into individual plots.
- **Section show/hide is data-driven** through `analysis_sections`. Prefer adding
  a key there over hand-coding `pack`/`pack_forget` in new places.
- **Number formatting** goes through `formatting.fmt*`. **Input validation**
  should go through `validation.parse_float` (red-border + error collection)
  rather than bare `float()` where you want user-friendly failure.
- New cross-cutting features (export, report, theming, anything not specific to
  one analysis) belong in **`EnhancementsMixin`**, keeping the analysis mixins
  focused.

---

## 7b. Graph Settings, collapsible sections, axis auto-cap

Three systems added on top of the original app — know how they hang together:

**Collapsible sections.** `create_section` (in `ui_helpers.py`) now makes the
section header a clickable `CTkButton` (▼/▶) wired to `toggle_section`. Collapse
records each non-header child's `pack_info()` and `pack_forget()`s it; expand
re-packs from the saved list. All sections **start collapsed**
(`collapse_all_sections()` at the end of `__init__`). Because
`show_sections_for_analysis` re-packs subframes when the analysis changes,
`reapply_collapsed_states()` is called at its end to keep user-collapsed sections
collapsed. `self.input_scroll_canvas` is stored so `_refresh_scrollregion()` can
update the scrollbar after a toggle.

**Grid control is per-axis.** `apply_graph_style` reads `grid_x`, `grid_y`,
`grid_style`, `grid_alpha` (not a single on/off) and enables vertical/horizontal
gridlines independently. The drive-cycle **heatmap** bins are defined by *width*,
not count: `bin_factor_x_entry` = RPM bin width, `bin_factor_y_entry` = Nm bin
width (`bin_factor_entry` is kept as a back-compat alias = the X entry). The
heatmap can be weighted by **point count or tractive energy (Wh)** — the
`hm_weight` graph-setting; per-point energy = `max(net_motor_torque,0)·ω·dt/3600`.
`_annotate_top_bins()` selects the busiest bins that together make up the top
`hm_top_pct`% of the cycle's count/energy (however many that is), marks them with
numbered circles, and lists them in a share-% table placed beside the colormap
(`hm_top_pct` / `hm_show_top`). Heatmap appearance
(`hm_cmap`, `hm_alpha`, `hm_show_scatter`) lives in the Drive Cycle graph
settings. `plot_torque_speed_drive_cycle(show_popup=…)` only shows the
input-parameters modal on an explicit button press, not on graph-setting replots.

**Drive Cycle auto-shows.** When the analysis is "Drive Cycle" and
`self.dataframe` is loaded, `plot_graph` calls `plot_drive_cycle()` automatically
instead of showing the "click the plot button" placeholder.

**Sessions auto-persist.** The full UI state (same collector as scenarios,
`_collect_scenario_data`) is silently written to `vmi_last_session.json` on
window close (`_on_app_close`, wired via `WM_DELETE_WINDOW`) and restored on
the next launch (`restore_last_session`). Delete the file to start clean.

**Data checklist.** `update_data_checklist()` (`ui_helpers.py`) renders a
✔/✖ required-vs-optional upload summary under the Analysis Type selector,
refreshed from `show_sections_for_analysis`. If you add an analysis that
needs uploads, add its entry to the `needs` dict there.

**Scenarios persist loaded data.** `save_scenario`/`load_scenario` in
`enhancements.py` now also serialize loaded datasets under a `__datasets__` key
(drive cycle, motor data, engine data + gear-eff curves, efficiency maps M1/M2,
range motor/controller maps). `_dataset_slots()` is the registry: each slot lists
its attributes, the `primary` attr that means "present", and the
`indicator`/`buttons` to tick. `_enc_obj`/`_dec_obj` round-trip DataFrames,
ndarrays and dicts through JSON; `_set_indicator` restores the ✅/❌ marks and
button states. **To make a new uploadable dataset survive scenarios, add one slot
to `_dataset_slots()`.**

**Graph Settings (`graph_settings.py`).** One adaptive section (key
`graph_settings`, in every single-axis analysis's `analysis_sections` list) whose
controls are rebuilt per analysis from `SCHEMA`. To add a control: add a field
spec to `SCHEMA[analysis]` and read it in the plot method with the matching
`self.gs_*()` helper, **passing the current hard-coded value as the default** so
an untouched setting reproduces the original look. Values persist in
`self._gs_values[(analysis, key)]`. Changing a control calls `_gs_replot()`,
which re-runs `_safe_plot()` for line plots, or the recorded `_last_eff_plot` /
`_last_dc_plot` for button-driven map/drive-cycle plots. `apply_graph_style()`
applies grid/legend/title/label-size to `self.ax` and is called at the end of
`plot_graph` and inside the map/DC plot methods.
- **Wired:** Torque/Force/Acceleration (per-line color/style/width + universal),
  Efficiency maps M1/M2 (colormap + fill/line contour levels), difference map
  (levels), Parametric (colormap + fill/line contour levels + universal), Engine
  (universal), Drive Cycle (universal + heatmap colormap/weight/opacity/top-N),
  MTPA/MTPV (per-line envelope/power/id/iq/|i_s|, region shading on/off +
  opacity, trajectory marker size, grid/legend/fonts — applied per panel by
  `_mtpa_apply_gs`, since the "All" view is multi-axis), Mechanical Design
  (shared line width + universal; single-axis, so plain `apply_graph_style`),
  Motor BOM (universal block; applies to Pareto/Group Split only — the Sankey
  view is axis-off and skips it).
- The universal grid block also exposes **per-axis fixed tick spacing**
  (`grid_x_step` / `grid_y_step`, 0 = auto) applied via `MultipleLocator` in
  `apply_graph_style` (capped at ~1000 ticks). Torque/Force/Acceleration no
  longer hard-code a grid — `apply_graph_style` is the sole authority and is
  called both at the end of `plot_graph` and inside `plot_force_graph` /
  `plot_vehicle_max_speed_vs_time`.
- **Not yet wired:** Range analysis (multi-panel — `apply_graph_style` only
  touches one axis). It intentionally has no `graph_settings` entry.
- **Compare Standard Motor Data** (2026-07) has its own vehicle/motor/sim/env
  inputs and a `graph_settings` entry, added because that analysis previously
  showed only the `compare_std` section — the user had no way to edit mass,
  gear ratio, peak torque, axis limits, etc. while comparing, and had to switch
  to another analysis type to change them. `analysis_sections["Compare
  Standard Motor Data"]` is now `['compare_std', 'vehicle', 'dynamics',
  'motor', 'sim', 'env', 'graph_settings']` (plus the usual auto-injected
  `efficiency_data`, needed for the "Compare Efficiency Map" mode). Because the
  four Compare radio buttons (Torque/Force/Acceleration/Efficiency) pick
  fundamentally different plots with different relevant axis fields, all four
  now call `update_plot()` (was `update_compare_std_plot()` directly) so
  `show_sections_for_analysis`'s `Compare Standard Motor Data` branch inside
  the `key == 'sim'` block (`dispatch.py`) can re-show only the matching
  fields: Torque → km/h X-limit + both Nm Y-limits (motor/wheel); Force → X
  + wheel-N Y-limit; Acceleration → max time + target speed; Efficiency →
  none (the diff map's color range is data-driven). `torque_compare_mode`
  (Wheel/Motor) also gained `command=self.update_plot` for the same reason —
  it previously did nothing until the pinned Update Plot button was pressed.
  `_gs_analysis()` splits Compare's graph settings into four namespaces the
  same way it splits Powertrain Sizing by Output (`"Compare Standard Motor
  Data::Torque/Force/Acceleration/Efficiency"`), keyed off
  `compare_std_plot_var`. Torque/Force/Acceleration get a shared `line_width`
  (impractical to expose per-line color pickers since the motor count is
  dynamic) + the universal grid/legend/font block; Efficiency gets the diff
  map's colormap/contour-level controls, mirroring Drive Cycle Efficiency's
  Difference map. `update_compare_std_plot()` and
  `_plot_compare_std_efficiency_map()` now end by calling
  `apply_graph_style()` instead of a hardcoded `ax.legend()`/`ax.grid(True)`.
  Two correctness bugs were fixed alongside this: (1) the x-axis speed unit
  passed to `get_x_limits` is now hardcoded to `"Km/hr"` instead of read from
  the (hidden-in-this-analysis) `speed_unit_combo`, which could be left on
  "RPM" from a different analysis and return an RPM-scaled limit pair against
  the km/h-valued `speeds` array Compare always plots; (2) the torque
  comparison's y-limit read/write now picks the `ylim_wheel`/`ylim_wheel_manual`
  entry when `torque_compare_mode == "Wheel"` and `ylim`/`ylim_manual` when
  `"Motor"`, instead of always using the motor-labeled field regardless of
  mode.

**Intersection-based x-cap.** In Torque/Force, `_auto_cap_xlimits` trims the
x-axis to `1.1 ×` the furthest peak-curve/resistive intersection (dead space past
top speed). Only applies when the matching axis isn't in manual mode; updates the
corresponding xlim entry box so the shown limit matches the axis.

## 7c. Assistant sidebar (local LLM + RAG)

A third pane in the top-level `tk.PanedWindow` (`self.paned`, `self.container`
are stored on `self` in `app.py::__init__` for this purpose), toggled by the
"💬 Assistant" toolbar button (`toggle_assistant_panel`, `enhancements.py`'s
`build_toolbar`). Closed by default — `paned.add(self.assistant_panel,
before=self.container, ...)` / `paned.forget(...)`.

**Everything runs locally through Ollama** (`llm_client.py`,
`http://localhost:11434`) — no cloud calls. Requires `ollama pull llama3.1:8b`
(chat) and `ollama pull nomic-embed-text` (embeddings) once, outside the app.
`TIMEOUT_S = 300`: the first chat after Ollama (re)starts loads the 8B model
into memory, which alone measured >120 s on this CPU-only machine.

**Persona system prompt (2026-07).** `SYSTEM_PROMPT` in `assistant.py` casts
the assistant as a senior EV powertrain design engineer (machines, power
electronics, mechanical, thermal, batteries, standards) with two explicit
modes: KB context present → answer from it, marking each fact
`(source: <file>)`; context missing → prefix `"Not in the knowledge base —
engineering judgment:"` and answer anyway from expertise (the old prompt told
it to stop when context was missing, which made it useless without data).
Two things llama3.1:8b needs to follow this reliably, both verified by live
test: (1) the prompt must NOT contain a concrete example citation with a fake
filename/number — the model parrots it verbatim as if it were data ("only
cite file names that literally appear in [brackets]" instead); (2)
`_chat_worker` restates the citation rule right next to the question (the
`reminder` suffix, only when retrieval hits exist) — with the rule only in
the system prompt the model used the data but dropped the source markings.

**Knowledge base** (`rag_store.py`, Chroma `PersistentClient` under
`knowledge_base/.index/`): indexes everything under `knowledge_base/`
(`standards/`, `datasheets/`, `products/`, `scenarios/` — drop PDFs/Word/Excel/
text files in any of these) plus `CLAUDE.md`, `std_motor_data_sample.json`,
and `sample_data/vmi_scenario*.json`. **To add or remove something from what
the assistant knows: add/delete the file, then click "Rebuild Knowledge
Base."** `rebuild_index()` diffs against `knowledge_base/.index/manifest.json`
(path → mtime) so only changed files are re-embedded and deleted files have
their chunks dropped — safe to click after every small change, not just a
full rescan.

**This is the app's first background-threading code.** Everything else in
`vmi/` runs synchronously on the Tk main loop (see §8) — `AssistantMixin`
introduces `threading.Thread` for LLM calls / index rebuilds, with a
`queue.Queue` + `self.after(150, self._poll_assistant_queue)` to marshal
results back to the main thread. Don't touch any Tk widget from inside
`_chat_worker` / the rebuild `worker()` — push onto `self._assistant_queue`
instead, exactly like the existing "chat_reply"/"kb_done"/etc. message types.

## 7d. Multi-analysis HTML report

The toolbar "Report" button (`generate_report` in `enhancements.py`) opens a
checklist popup (`_open_report_picker`) listing every key in
`self.analysis_sections` (a new analysis mode is automatically offered —
nothing to wire here), defaulting to just the analysis currently active; the
user can select any subset or "Select All".

**One report section per plot, not per analysis.** `_render_report_views(
analysis)` is the per-analysis dispatcher — explicit branches on purpose (not
a generic loop), because each analysis's "views" are controlled by completely
different widgets:
- **Powertrain Sizing**: Torque (At Wheel) always; Torque (At Motor) too, but
  *only* when the gear ratio isn't 1:1 (wheel and motor numbers are otherwise
  identical, so a second view would be a duplicate); Force (always wheel-only
  — the Plotting Part selector auto-locks to wheel for Force, so there's no
  motor-side Force to show).
- **Drive Cycle**: Speed-vs-Time, Torque-Speed Scatter, and — only if a drive
  cycle is loaded — Torque-Speed Heatmap (toggles `self.heatmap_var` around
  the same `plot_torque_speed_drive_cycle(show_popup=False)` call the "Plot
  Torque-Speed Heatmap" button uses; `show_popup=False` is required here, the
  button's own `show_popup=True` would pop a blocking modal during report
  generation).
- **Drive Cycle Efficiency**: Motor / Controller / Combined / Difference maps
  — called *directly* (`plot_efficiency_map_motor1/2`,
  `_combined`, `plot_efficiency_difference_map`), never through
  `update_plot()`. This analysis has **no branch in `dispatch.plot_graph`
  at all** (it's purely button-driven in the UI), so routing it through the
  generic `update_plot()` path — as the report used to — silently produces
  the "Insert Data or Update Plot" placeholder instead of a real map. Only
  the maps that are actually loaded are included.
- **Compare Standard Motor Data**: Torque / Force / Acceleration always (if
  at least one motor is selected); Efficiency Map too, if the current session
  has a Motor map loaded *and* the selected motor(s) include a saved one
  (see §7e).
- **Motor BOM**: Sankey (Cost), Sankey (Weight), Pareto (Cost), and Compare
  A vs B (Cost) when BOM B is loaded — the BOM view/metric combos are set per
  snapshot and restored in a `finally`.
- **Everything else** (Acceleration, Parametric Study, Engine analysis, Range
  analysis, MTPA/MTPV, Mechanical Design): one view via plain `update_plot()`.
  Range and MTPA already default to their "All" multi-panel dashboard, so one
  view is already comprehensive for those; Mechanical Design reports the
  currently selected Design Check's plot + inputs.

**Inputs come first.** `_report_core_inputs_html()` builds one table of the
shared vehicle/motor inputs (mass, Crr/CdA, gear ratio, wheel radius, peak
torque/power, gradients, ...) — read once and shown at the very top of the
report (`id="inputs"`, before the table of contents), not repeated per
section. `_report_analysis_inputs_html(analysis)` adds a second, small inputs
block for analyses with their own separate input model that the shared table
doesn't cover (MTPA/MTPV's d-q parameters, Mechanical Design's active
Design-Check widgets via `_mech_report_input_rows()`); it's prepended to
that analysis's *first* section only.

Per-view numeric summaries still come from `_REPORT_LABEL_ATTRS[analysis]`
(the relevant `*_results_label` / `params_label` widget(s); Range analysis
also gets its `_last_range_metrics` table, Compare Standard Motor Data gets
its selected-motors table) — the same summary is repeated under each of that
analysis's views, since the label reflects whatever was last computed for the
analysis as a whole, not one specific view.

Every widget the report touches to switch views (`plot_type`, `output_combo`,
`plot_part_combo`, `heatmap_var`, `compare_std_plot_var`) is snapshotted
before generation and restored in a `finally` block afterward, so the app
ends up back exactly where the user left it, regardless of the checklist.

**If you add a new analysis mode:** add its result-label attribute(s) to
`_REPORT_LABEL_ATTRS` (skip it if the mode has no numeric summary — the
report falls back to "no numeric summary for this view" and still includes
the figure). **If the new mode has more than one meaningful view** (its own
"which map/part/side" toggle), add a branch to `_render_report_views` — the
`else` fallback only ever captures one.

## 7e. Compare Standard Motor Data: saved efficiency maps + decluttered torque compare

Saving a motor (`save_std_motor_data_popup` in `data_io.py`) now also snapshots
whatever Motor efficiency map is currently loaded (`self.eff1_map_torques/
_rpms/_matrix`) via `_current_eff_map_for_save()`, storing it as an `eff_map`
key (`{"torque_axis", "rpm_axis", "matrix"}`, plain lists) alongside the usual
`speed_rpm`/`torque`/`gear_ratio_std`/`wheel_radius` in
`std_motor_data_sample.json`. A motor saved with no map loaded simply has no
`eff_map` key (`None` via `.get("eff_map")`), same as motors saved before this
existed — nothing breaks reading old entries. `choose_std_motor_popup` carries
that key straight through into `self.selected_std_motors` entries (now
`{name, gear_ratio, wheel_radius, eff_map}` — if you add a new attribute to
these entries, update the comment next to `self.selected_std_motors = []` in
`app.py`).

A 4th radio button, **"Compare Efficiency Map"** (`compare_std_plot_var` value
`"efficiency"`), dispatches `update_compare_std_plot` to
`_plot_compare_std_efficiency_map()` before any of the vehicle/torque inputs
are parsed (that parsing is irrelevant here and shouldn't be able to block the
view on an unrelated blank field). It diffs the *currently loaded* Motor map
against the *first* selected standard motor that has a saved `eff_map`
(mirrors the pre-existing "first selected motor" fallback in
`save_std_motor_data_popup`) — same diverging-colormap technique as
`plot_efficiency_difference_map` (Drive Cycle Efficiency): interpolate both
onto a common fine grid, `saved − current`, `_motor_capability_mask()` +
`_draw_motor_capability_curve()` for masking/envelope, `ax.set_axisbelow
('line')` so the grid renders above the fill. Missing map on either side, or
no overlapping torque/RPM region, shows a placeholder message instead of
erroring.

**Compare Torque Plot decluttering.** `plot_torque_graph()` gained a
`show_continuous` parameter (default `True`); Compare Standard Motor Data
passes `show_continuous=False`, which drops the continuous/"rated" torque
curve and its gradient-intersection markers entirely for the *current*
motor — comparing several motors' rated torque added clutter with no real
use, the comparison is about peak capability. Each *saved* motor's own peak
curve now gets its own gradient-intersection markers too (previously only the
current motor did): `update_compare_std_plot` recomputes the per-gradient
resistive-torque array (same formula `plot_torque_graph` uses internally, not
exposed by that call) and calls `_annotate_intersections()` with the plotted
line's own color and a diamond marker, so each motor's crossing point is
visually tied to its curve.

## 8. Known rough edges (be aware, fix only if asked)

- **`COLORS_FONT`** in `enhancements.py` is a loose module-level constant
  (`= "Segoe UI"`, defined at the *bottom* of the file, line ~456) used by the
  toolbar/status-bar font tuples. It duplicates `FONTS["family"]` in `theme.py`;
  prefer `FONTS["family"]` for new code and consider consolidating.
- Several files begin with the identical *"Auto-generated module (method bodies
  copied verbatim from the original app)"* header and import the **same broad set
  of modules** whether or not each is used. Don't be surprised by unused imports;
  don't bulk-remove them blind.
- `app.py::__init__` is very long and mixes layout with logic, and there's
  commented-out dead code throughout. Refactors are welcome **only when scoped
  and behavior-preserving** — this is a numbers tool with no tests, so large
  rewrites are high-risk. Prefer additive, localized changes.
- Mojibake in labels/comments (`Â²`, `°`, emoji) is pre-existing encoding noise.

---

## 9. Quick "where do I…" index

| I want to… | Go to |
|------------|-------|
| Add an input field | `app.py::__init__` (`create_labeled_entry`), add `_manual` flag if auto-filled |
| Add a new analysis mode | `app.py` (plot_type list + `analysis_sections`) → `dispatch.py::plot_graph` branch → new `plot_*` method |
| Change which inputs show per mode | `app.py::analysis_sections` and `dispatch.py::show_sections_for_analysis` |
| Change a color/font | `theme.py::COLORS` / `FONTS` |
| Add a per-plot appearance control | `graph_settings.py::SCHEMA` + read it via `self.gs_*()` in the plot method |
| Make a section collapsible / change start state | `ui_helpers.py::create_section` / `collapse_all_sections` |
| Generate test data | `generate_sample_data.py` -> `sample_data/` |
| Change axis-limit behavior | `limits.py` |
| Touch the Crr/CdA model | `physics.py` (verbatim — be careful) |
| Add advanced/optional physics | `calc_ext.py` + the "Advanced Physics" section in `app.py` |
| Load/save an Excel or JSON | `data_io.py` (follow the data/indicator/button triplet) |
| Add an export / report / scenario / theme feature | `enhancements.py` |
| Edit the range/battery model | `range_analysis.py` + `efficiency.py` |
| Edit efficiency maps | `efficiency.py` |
| Add/remove a document the assistant can answer from | Drop/delete a file under `knowledge_base/`, then click "Rebuild Knowledge Base" |
| Change the assistant's chat/embedding model | `llm_client.py` (`CHAT_MODEL` / `EMBED_MODEL`) |

---

*Keep this file current.* When you add an analysis mode, a persisted file, a new
`self.*` data attribute, or a cross-file convention, update the relevant section
above — future changes depend on these contracts being documented.
