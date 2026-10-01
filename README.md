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
- **MTPA / MTPV (PMSM)** — d-q model analysis of a PM synchronous motor:
  torque–speed envelope with MTPA / flux-weakening / MTPV regions, power
  curve, id–iq current trajectory with the current circle and voltage
  ellipses, and base-speed / corner-power / characteristic-current results
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
tools/                      Assistant benchmark scripts (questionnaire.py, benchmark_assistant.py)
docs/                       Assistant model evaluation results
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

Click **💬 Assistant** in the toolbar to open a chat panel that answers
questions about EV motors and control, your current analysis, how this app
calculates things, and the reference documents you put in `knowledge_base/`.
It runs entirely on your own computer through [Ollama](https://ollama.com) —
nothing is sent to the internet. If you skip this section, the rest of the
app works exactly the same.

### Setup (home PC or office laptop)

1. **Install Ollama** — download *OllamaSetup.exe* from
   <https://ollama.com/download> and run it. It installs for your user
   account (no admin rights normally needed) and runs in the system tray.
2. **Download the two models** (once, about 2.8 GB total) in a terminal:
   ```
   ollama pull qwen3:4b-instruct
   ollama pull nomic-embed-text
   ```
   | Model | Size | What it does |
   |---|---|---|
   | `qwen3:4b-instruct` | 2.5 GB | Writes the answers (the default chat model) |
   | `nomic-embed-text` | 274 MB | Searches your `knowledge_base/` documents |
3. **Install the Python packages** if you haven't already:
   `pip install -r requirements.txt` (includes `requests`, `chromadb`,
   `pypdf`, `python-docx`).
4. **Copy your reference documents** into the `knowledge_base/` subfolders
   (`standards/`, `datasheets/`, `products/`, `scenarios/`). These folders
   are git-ignored on purpose (datasheets and standards are often licensed),
   so they are **not** in the GitHub repo — copy them over by hand
   (USB / OneDrive).
5. Start the app, open the Assistant, click **⚙ → Rebuild knowledge base**
   (once, and again whenever you add or remove documents).
6. Ask a question.

Check it works: `ollama list` should show both models, and
`ollama run qwen3:4b-instruct "hello"` should reply.

**Office network blocks `ollama pull`?** Copy the downloaded models from a PC
that has them: the folder `C:\Users\<you>\.ollama\models` → the same path on
the laptop (or set the `OLLAMA_MODELS` environment variable to wherever you
put the folder), then restart Ollama.

### Hardware and model choice

| Laptop | What to expect with `qwen3:4b-instruct` |
|---|---|
| NVIDIA GPU with 4 GB+ (e.g. GTX 1650) | ~35 words/s; casual replies instant, most answers 5–10 s, document questions 20–30 s |
| Smaller / no NVIDIA GPU | Works automatically (the app falls back to Ollama's own CPU/GPU split) but several times slower |

On a slow CPU-only laptop, `llama3.2:3b` (`ollama pull llama3.2:3b`, 2 GB) is
the fastest alternative, at noticeably lower accuracy on motor questions. Pick
it under **⚙ → Model** — the choice is remembered. See
[`docs/assistant_evaluation.md`](docs/assistant_evaluation.md) for the
measured comparison of seven local models.

### Using it

- **Use current analysis** (switch under the input box): for questions about
  *your* work ("why is my top speed low?", "is my motor enough for 20%?") the
  assistant receives selected structured inputs (including core fields in
  collapsed sections), computed observations, dataset presence, and a sample
  of plotted lines. The plot sample can predate the latest input edit; press
  **Update Plot** before asking about the graph.
- The assistant can read and explain current inputs. It cannot change fields,
  recalculate analyses, create plots, export files, or use a microphone from
  chat. Use the application controls for those actions.
- **Quick actions:** *Suggest improvements*, *Explain my plot*, *What can you do?*
- **Documents:** answers that use your files cite them, e.g.
  `[EV_Motor_Testing_India_2W_3W_Hub_MidMount.docx#chunk-2]`. Excel curves
  and maps are indexed with their key facts (peak torque and the speed range
  it holds over, peak efficiency and where it occurs).
- **Checked arithmetic:** fully specified shaft-power, DC-to-shaft
  efficiency, wheel torque / tractive force, grade force and range questions
  are calculated exactly in code rather than by the model. Greetings and
  small talk are answered instantly without the model.
- Document requests without indexed evidence report that limitation. If the
  model omits a valid citation, the sidebar shows a short source excerpt for
  inspection instead of presenting the generated answer as a verified claim.
  Verify engineering and compliance decisions against the original document.
- **⚙ → Compare models** runs any installed models on the same motor
  questions so you can review the answers and timings side by side.

### Evaluating the assistant

`tests/assistant_questionnaire.json` holds 41 questions in three groups —
casual conversation, motor design and control, and this software's analyses
and parameters — each with automatic checks (required facts, forbidden
content, word limit, expected document). Run it against any models:
```
python tools/questionnaire.py --models qwen3:4b-instruct llama3.2:3b
python tools/questionnaire.py --report reports/questionnaire_*.jsonl
```
It goes through the same routing as the chat panel and records load time,
retrieval time, time to first word, total time, words/s, accuracy and style.

Every question and answer is also logged locally to `assistant_chat_log.jsonl`
so you can review how the assistant performs over time. This file is just for
you — it is not uploaded to GitHub (see `.gitignore`).

For broader regression coverage, run `python tools/assistant_qa.py` (406
intent and safety cases), `python tools/assistant_rag_probe.py` (40 retrieval
questions), and `python -m pytest`. The architecture, tested limits, and
manual review procedure are in [`docs/assistant_qa_report.md`](docs/assistant_qa_report.md).

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
