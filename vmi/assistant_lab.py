"""Local motor benchmarks with explicit, human-reviewed quality ratings."""
import datetime
import json
from pathlib import Path
import queue
import statistics
import threading
import time
import customtkinter as ctk
from . import llm_client
from .assistant_core import answer_case

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_CASES_PATH = ROOT / "assistant_benchmark_cases.json"


class ModelLab(ctk.CTkToplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.title("Motor assistant • model comparison")
        self.geometry("960x760")
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.records = []
        self.cases = json.loads((ROOT / "tests/assistant_cases.json").read_text(encoding="utf-8"))
        try:
            self.cases.extend(json.loads(CUSTOM_CASES_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.protocol("WM_DELETE_WINDOW", self.close)
        ctk.CTkLabel(self, text="Compare installed models on motor engineering", font=("Arial", 20, "bold")).pack(pady=10)
        ctk.CTkLabel(self, text="Controlled questions and evidence • sequential runs • quality requires your review").pack()
        self.models = ctk.CTkEntry(self, placeholder_text="Comma-separated installed model names")
        self.models.pack(fill="x", padx=16, pady=8)
        self.models.insert(0, owner._chosen_model or llm_client.CHAT_MODEL)
        row = ctk.CTkFrame(self)
        row.pack(fill="x", padx=16)
        ctk.CTkLabel(row, text="Repeats per question").pack(side="left", padx=5)
        self.repeats = ctk.CTkOptionMenu(row, values=["1", "2", "3"])
        self.repeats.pack(side="left")
        self.run_button = ctk.CTkButton(row, text="Run comparison", command=self.run)
        self.run_button.pack(side="left", padx=5)
        ctk.CTkButton(row, text="Stop after current", command=self.stop.set).pack(side="left", padx=5)
        self.custom_question = ctk.CTkEntry(self, placeholder_text="Add your own motor question")
        self.custom_question.pack(fill="x", padx=16, pady=(8, 2))
        self.custom_rubric = ctk.CTkEntry(self, placeholder_text="Expected answer or review criteria")
        self.custom_rubric.pack(fill="x", padx=16, pady=2)
        ctk.CTkButton(self, text="Add question", command=self.add_case).pack(fill="x", padx=16, pady=(2, 6))
        self.status = ctk.CTkLabel(self, text="Every benchmark question runs for each model.")
        self.status.pack(pady=6)
        self.summary = ctk.CTkLabel(self, text="", justify="left", wraplength=900)
        self.summary.pack(fill="x", padx=16)
        self.result_picker = ctk.CTkOptionMenu(self, values=["No results"], command=self.show_result)
        self.result_picker.pack(fill="x", padx=16, pady=6)
        self.answer = ctk.CTkTextbox(self, wrap="word")
        self.answer.pack(fill="both", expand=True, padx=16)
        review = ctk.CTkFrame(self)
        review.pack(fill="x", padx=16, pady=10)
        self.rating = ctk.CTkOptionMenu(review, values=["Unreviewed", "0 — Incorrect", "1 — Major errors", "2 — Partly correct", "3 — Mostly correct", "4 — Correct and complete"])
        self.rating.pack(side="left", padx=5)
        ctk.CTkButton(review, text="Save quality review", command=self.review).pack(side="left", padx=5)
        ctk.CTkButton(review, text="Use selected model", command=self.apply).pack(side="left", padx=5)
        self.after(100, self.poll)

    def add_case(self):
        question, rubric = self.custom_question.get().strip(), self.custom_rubric.get().strip()
        if not question or not rubric:
            self.status.configure(text="Enter both a question and expected answer or review criteria.")
            return
        case = dict(id="custom_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
                    question=question, rubric=rubric)
        try:
            custom = [c for c in self.cases if c['id'].startswith('custom_')]
            custom.append(case)
            CUSTOM_CASES_PATH.write_text(json.dumps(custom, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.status.configure(text=f"Could not save question: {exc}")
            return
        self.cases.append(case)
        self.custom_question.delete(0, "end")
        self.custom_rubric.delete(0, "end")
        self.status.configure(text=f"Added question ({len(self.cases)} questions total).")

    def close(self):
        self.stop.set()
        if self.running:
            self.status.configure(text="Stopping after current request; results will be saved. Close when finished.")
        else:
            self.destroy()

    def run(self):
        if self.running or self.owner._assistant_busy:
            self.status.configure(text="Wait for the active assistant request or comparison to finish.")
            return
        models = list(dict.fromkeys(m.strip() for m in self.models.get().split(",") if m.strip()))
        if not models:
            self.status.configure(text="Enter at least one installed model.")
            return
        cases = list(self.cases)
        analysis_types = tuple(self.owner.plot_type.cget("values"))
        self.records = []
        self.result_picker.configure(values=["No results"])
        self.result_picker.set("No results")
        self.answer.delete("1.0", "end")
        self.summary.configure(text="")
        self.stop.clear()
        self.running = True
        self.started = time.perf_counter()
        self.current = "Starting"
        self.output = ROOT / "reports" / ("model_lab_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
        self.run_button.configure(state="disabled")
        self.owner._set_assistant_busy(True)
        repeats = int(self.repeats.get())
        def worker():
            try:
                for model in models:
                    for case in cases:
                        for repeat in range(repeats):
                            if self.stop.is_set():
                                return
                            self.events.put(("progress", f"{model} • {case['id']} • repeat {repeat+1}"))
                            record = dict(model=model, case=case["id"], question=case["question"], rubric=case["rubric"], repeat=repeat+1, rating=None)
                            try:
                                answer, metrics, _sources = answer_case(case, model, analysis_types)
                                record.update(answer=answer, metrics=metrics, status="ok")
                            except Exception as exc:
                                record.update(answer=str(exc), status="error")
                            self.events.put(("result", record))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=worker, daemon=True).start()

    def persist(self):
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text(json.dumps(self.records, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.status.configure(text=f"Could not save benchmark: {exc}")

    def selected(self):
        try:
            return self.records[int(self.result_picker.get().split(".")[0])-1]
        except (ValueError, IndexError):
            return None

    def show_result(self, _=None):
        record = self.selected()
        if record is None:
            return
        self.answer.delete("1.0", "end")
        self.answer.insert("end", f"QUESTION\n{record['question']}\n\nREVIEW CRITERIA\n{record['rubric']}\n\nMODEL ANSWER ({record['status']})\n{record['answer']}\n\nTIMING\n{json.dumps(record.get('metrics', {}), indent=2)}")
        values = self.rating.cget("values")
        self.rating.set(values[0 if record['rating'] is None else record['rating']+1])

    def review(self):
        record = self.selected()
        if record is None or record['status'] != 'ok':
            return
        choice = self.rating.get()
        record['rating'] = None if choice == 'Unreviewed' else int(choice[0])
        self.persist()
        self.summarize()

    def summarize(self):
        lines = []
        candidates = []
        for model in dict.fromkeys(r['model'] for r in self.records):
            rows = [r for r in self.records if r['model'] == model]
            ok = [r for r in rows if r['status'] == 'ok']
            reviewed = [r['rating'] for r in ok if r['rating'] is not None]
            latency = statistics.median(r['metrics']['total_s'] for r in ok) if ok else None
            lines.append(f"{model}: {len(ok)}/{len(rows)} completed; " + (f"median {latency:.1f}s; " if latency is not None else "") + (f"reviewed quality {statistics.mean(reviewed):.1f}/4 ({len(reviewed)}/{len(ok)} reviewed)" if reviewed else "quality unreviewed"))
            if len(ok) == len(rows) and len(reviewed) == len(ok):
                candidates.append((model, statistics.mean(reviewed), latency))
        advice = "Review every answer before choosing a model."
        if any(r['status'] != 'ok' for r in self.records):
            advice = "Some runs failed. Check model availability or shorten the test, then rerun before assigning a model."
        if len(candidates) == len({r['model'] for r in self.records}) and candidates:
            acceptable = [row for row in candidates if row[1] >= 3]
            if not acceptable:
                advice = "No model averaged at least 3/4 in review. Try another installed model or improve the prompt before assigning one."
            else:
                winner = min(acceptable, key=lambda row: (-row[1], row[2]))
                advice = f"Suggested: {winner[0]} (best reviewed quality, then speed)."
        self.summary.configure(text="\n".join(lines) + "\n" + advice + " Load time is included in latency; repeated runs show warm performance.")

    def apply(self):
        record = self.selected()
        if record is None or self.running or self.owner._assistant_busy:
            return
        self.owner._chosen_model = record['model']
        self.owner.model_picker.set(record['model'])
        self.owner._persist_model_choices()
        self.status.configure(text=f"Now using {record['model']}")

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'progress':
                    self.current = payload
                elif kind == 'result':
                    self.records.append(payload)
                    labels = [f"{i+1}. {r['model']} / {r['case']} / {r['repeat']}" for i, r in enumerate(self.records)]
                    self.result_picker.configure(values=labels)
                    self.result_picker.set(labels[-1])
                    self.show_result()
                    self.persist()
                    self.summarize()
                elif kind == 'done':
                    self.running = False
                    self.run_button.configure(state="normal")
                    self.owner._set_assistant_busy(False)
                    self.status.configure(text=f"{'Stopped' if self.stop.is_set() else 'Finished'} • results: {self.output.name}")
        except queue.Empty:
            pass
        if self.running:
            self.status.configure(text=f"{self.current} • {time.perf_counter()-self.started:.1f}s elapsed")
        self.after(100, self.poll)
