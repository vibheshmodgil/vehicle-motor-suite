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
gear efficiency G1..G6, two efficiency maps). They match the exact formats the
loaders expect; use them to exercise every analysis without real data.

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
`plot_efficiency_difference_map` (Controller−Motor). The drive-cycle overlay
(`_overlay_drive_cycle_on_efficiency_plot`) supports Scatter / Heatmap / Both via
the `overlay_*` Graph-Settings keys (hexbin weighted by point count or tractive Wh).

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
| `mtpa_mtpv.py` | `MtpaMtpvMixin` + `solve_mtpa_mtpv()` | "MTPA / MTPV (PMSM)" analysis: pure d-q solver (module function, tested in tests/test_mtpa_mtpv.py) + input section + 4 plot views. Short-circuits in `dispatch.plot_graph` before vehicle-input parsing (like Range). Model per `knowledge_base/scenarios/MTPA_MTPV.pdf`; stator resistance neglected. Region classification is geometric (MTPV = optimum detached from the current circle), not torque-comparison. |
| `compare_std.py` | `CompareStdMixin` | Standard-motor comparison table + overlay plotting. |
| `data_io.py` | `DataIOMixin` | All Excel/JSON load/save + the column-picker popups + delete handlers. |
| `downloads.py` | `DownloadsMixin` | Save current figure / export torque-speed data to Excel. |
| `enhancements.py` | `EnhancementsMixin` | Cross-cutting *new* features: toolbar, status bar, figure/data export, HTML report, save/load scenario, dark-plot toggle, Enter-to-plot, `_safe_plot` error wrapper, loss waterfall. |
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
  (universal), Drive Cycle (universal + heatmap colormap/weight/opacity/top-N).
- The universal grid block also exposes **per-axis fixed tick spacing**
  (`grid_x_step` / `grid_y_step`, 0 = auto) applied via `MultipleLocator` in
  `apply_graph_style` (capped at ~1000 ticks). Torque/Force/Acceleration no
  longer hard-code a grid — `apply_graph_style` is the sole authority and is
  called both at the end of `plot_graph` and inside `plot_force_graph` /
  `plot_vehicle_max_speed_vs_time`.
- **Not yet wired:** Range analysis (multi-panel — `apply_graph_style` only
  touches one axis) and Compare Standard Motor Data (separate plot path). These
  intentionally have no `graph_settings` entry.

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
