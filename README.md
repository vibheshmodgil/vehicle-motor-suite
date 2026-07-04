# Vehicle ↔ Motor Integration Suite (VMI)

A desktop tool for sizing an electric motor against a two/three-wheeler
vehicle (and comparing against IC engines). It's a graphical app — no coding
required to use it — for engineers to plot torque/force/acceleration curves,
run parametric studies, analyze drive cycles, check motor efficiency maps,
and estimate EV range from a battery pack model.

It runs entirely on your own computer. Your data never leaves your machine.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What it does

- **Powertrain Sizing** — torque/force vs. speed curves at the wheel or motor
- **Parametric studies** — how CdA / Crr affect top speed, acceleration, gradability
- **Drive cycle analysis** — plot a drive cycle and see torque–speed scatter/heatmaps over it
- **Drive cycle efficiency** — motor + controller efficiency maps applied over a drive cycle
- **Engine analysis** — multi-gear IC engine torque/force at the wheel, for comparison
- **Compare standard motors** — overlay a saved library of reference motors
- **Range analysis** — battery pack model → power, energy, C-rate, losses, and range over a cycle
- **Assistant sidebar** — an optional local AI chat assistant that can answer
  questions about your results using your own reference documents (fully
  local, no data sent to the cloud)

### Quality-of-life features

- **Your inputs survive restarts** — the app silently saves everything on
  close (to `vmi_last_session.json`) and restores it on the next launch.
- **Data checklist** — under the Analysis Type selector, a ✔/✖ line shows
  which files the selected analysis needs and which are already loaded.
- **Friendly input errors** — invalid fields get a red border and one
  status-bar message listing everything wrong, instead of popup after popup.
- **Tyre picker** — choose a tyre size (e.g. `90/90-12`) and the wheel radius
  is calculated from the specification automatically, including a dynamic
  rolling-radius factor you can adjust.
- **Error log** — unexpected errors are recorded in `vmi_app.log` so problems
  can be diagnosed after the fact.

## Screenshots

*(Add a screenshot or two here once you have the app running — drag an image
file into this README on GitHub's web editor, or place it in a `docs/`
folder and reference it with `![Torque plot](docs/screenshot1.png)`.)*

---

## Getting started (no coding experience needed)

### 1. Install Python

You need Python 3.10 or newer.

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download and run the installer.
3. **Important:** on the first installer screen, check the box that says
   **"Add Python to PATH"** before clicking Install.

To check it worked, open a terminal (search for "PowerShell" in the Windows
Start menu) and type:

```
python --version
```

You should see something like `Python 3.12.x`.

### 2. Get this project onto your computer

If you're viewing this on GitHub, click the green **Code** button → **Download
ZIP**, then extract it somewhere on your computer (e.g. your Desktop).

If you already have `git` installed, you can instead run:

```
git clone <this-repo's-URL>
```

### 3. Open a terminal in the project folder

- In the extracted folder, click the address bar at the top of File Explorer,
  type `powershell`, and press Enter. This opens a terminal already pointed at
  the right folder.

### 4. Install the required packages

Copy-paste this into the terminal and press Enter:

```
pip install -r requirements.txt
```

This downloads the libraries the app needs (it only has to be done once,
or again later if `requirements.txt` changes). It may take a couple of minutes.

### 5. Run the app

```
python main.py
```

A window should open. That's the app.

### 6. (Optional) Load sample data to try it out

If you want to explore the app with realistic-looking dummy data instead of
your own files, run:

```
python generate_sample_data.py
```

This creates a `sample_data/` folder with ready-to-upload Excel files for
every data slot in the app (drive cycle, motor data, engine torque/RPM, gear
efficiency, efficiency maps). Use the app's "Upload" buttons to load them.

---

## Project structure

```
main.py                    Entry point — run this to start the app
requirements.txt            List of packages to install (step 4 above)
generate_sample_data.py     Creates dummy Excel files to try the app with
vmi/                        The application's source code
tests/                      Automated tests that lock the physics formulas (see below)
sample_data/                Generated sample files (created by step 6, not tracked in git)
knowledge_base/             Your own reference documents for the Assistant sidebar (see below)
```

You don't need to open or understand the code in `vmi/` to use the app.

## Tests (for anyone changing the code)

This is an engineering tool, so the calculation formulas are protected by a
test suite with "golden values" — known-correct outputs captured from the
calibrated model. Run it with:

```
python -m pytest tests/
```

If a test fails after a code change, a physics formula or calibration value
changed — which should only ever happen deliberately.

## The Assistant sidebar (optional, local AI chat)

The app has a collapsible chat panel (click "💬 Assistant" in the toolbar)
that can answer questions using your own documents — testing standards,
datasheets, saved scenarios, etc. It runs a small AI model entirely on your
own computer using [Ollama](https://ollama.com) — nothing is sent to the
internet.

To use it:

1. Install [Ollama](https://ollama.com/download) for Windows and run it once.
2. In a terminal, run:
   ```
   ollama pull llama3.1:8b
   ollama pull nomic-embed-text
   ```
3. Drop any PDF, Word, or Excel reference files you want the assistant to
   know about into the `knowledge_base/` subfolders (`standards/`,
   `datasheets/`, `products/`, `scenarios/`).
4. In the app's Assistant panel, click **"Rebuild Knowledge Base"**.
5. Ask it questions.

If you skip this section entirely, the rest of the app works exactly the
same — the Assistant is optional.

Every question and answer is logged locally to `assistant_chat_log.jsonl` in
this folder, so you can review how the assistant is performing over time.
This file is just for you — it is not uploaded to GitHub (see `.gitignore`).

---

## Notes on the data in this repo

- The `knowledge_base/` subfolders are intentionally empty in this repo (only
  placeholder files) — any standards, datasheets, or product documents you
  add there are your own reference material and are kept off GitHub by
  default (see `.gitignore`) since such documents are often licensed and not
  yours to redistribute.
- `sample_data/`, the Assistant's chat log, and its local search index are
  all generated on your machine and are not part of this repo either — they
  regenerate automatically as described above.

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for
details. In short: you're free to use, modify, and share it, just keep the
copyright notice.
