"""AssistantMixin -- a collapsible local-LLM + RAG chat sidebar.

Docks into the app's existing tk.PanedWindow (see app.py) as a third pane,
toggled open/closed from a toolbar button -- closed by default, like the
Claude/ChatGPT sidebar in VS Code. All LLM work (retrieval + Ollama calls,
knowledge-base rebuilds) runs on a background thread; results come back
through a queue.Queue that a self.after() poller drains on the Tk main
thread -- this is the app's first background-threading code, so it's kept
self-contained rather than introducing a general async framework.
"""

import datetime
import json
import queue
import threading
from pathlib import Path

import customtkinter as ctk

from .theme import COLORS, FONTS
from . import llm_client
from . import rag_store

from .assistant_core import MAX_TOKENS, build_messages, system_prompt, checked_calculation_reply, markdown_spans, small_talk_reply, summarize_line, wants_screen_context
import time

CHAT_LOG_PATH = "assistant_chat_log.jsonl"
SETTINGS_PATH = Path(__file__).resolve().parents[1] / "assistant_settings.json"


class AssistantMixin:

    def build_assistant_panel(self):
        """Build the sidebar's contents. Not docked yet -- toggle_assistant_panel
        adds/removes it from the PanedWindow."""
        self._assistant_open = False
        self._assistant_queue = queue.Queue()
        self._assistant_busy = False
        self._conversation = []
        self._available_models = []
        self._chosen_model = ""
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            self._chosen_model = saved.get("model", "") if isinstance(saved.get("model"), str) else ""
        except (OSError, ValueError, AttributeError):
            pass
        self._stream_mark = None

        panel = ctk.CTkFrame(self.paned, fg_color=COLORS["background"], corner_radius=0)
        self.assistant_panel = panel
        small = (FONTS["family"], 11)

        # Header bar: title + the two actions used most (new chat, settings).
        header = ctk.CTkFrame(panel, fg_color=COLORS["header_bg"], corner_radius=0)
        header.pack(side="top", fill="x")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=12, pady=8)
        ctk.CTkLabel(titles, text="Engineering Assistant", font=(FONTS["family_semibold"], 16, "bold"),
                     text_color=COLORS["on_header"]).pack(anchor="w")
        ctk.CTkLabel(titles, text="Runs locally on this PC • private", font=small,
                     text_color=COLORS["on_header_muted"]).pack(anchor="w")
        header_btn = dict(width=34, height=30, fg_color="transparent", hover_color=COLORS["primary"],
                          text_color=COLORS["on_header"], border_width=1, border_color=COLORS["on_header_muted"])
        ctk.CTkButton(header, text="⚙", command=self._toggle_assistant_settings,
                      font=(FONTS["family"], 15), **header_btn).pack(side="right", padx=(4, 10))
        ctk.CTkButton(header, text="New chat", command=self._clear_conversation, font=small,
                      **{**header_btn, "width": 76}).pack(side="right")

        # Settings drawer, collapsed by default: model choice and maintenance actions.
        self._assistant_settings = ctk.CTkFrame(panel, fg_color=COLORS["card"], corner_radius=8,
                                                border_width=1, border_color=COLORS["border"])
        ctk.CTkLabel(self._assistant_settings, text="Model", font=small,
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=10, pady=(8, 0))
        model_row = ctk.CTkFrame(self._assistant_settings, fg_color="transparent")
        model_row.pack(fill="x", padx=10, pady=(2, 6))
        self.model_picker = ctk.CTkComboBox(model_row, values=[llm_client.CHAT_MODEL], command=self._save_model_choice)
        self.model_picker.set(self._chosen_model or llm_client.CHAT_MODEL)
        self.model_picker.pack(side="left", fill="x", expand=True)
        self.model_refresh = ctk.CTkButton(model_row, text="↻", width=32, command=self._refresh_models)
        self.model_refresh.pack(side="left", padx=(6, 0))
        tools_row = ctk.CTkFrame(self._assistant_settings, fg_color="transparent")
        tools_row.pack(fill="x", padx=10, pady=(0, 10))
        secondary = dict(fg_color="transparent", text_color=COLORS["primary"], border_width=1,
                         border_color=COLORS["border"], hover_color=COLORS["header_bg_soft"], font=small, height=28)
        ctk.CTkButton(tools_row, text="Compare models", command=self._open_model_lab,
                      **secondary).pack(side="left", fill="x", expand=True)
        self.assistant_kb_btn = ctk.CTkButton(tools_row, text="Rebuild knowledge base",
                                              command=self._rebuild_kb_async, **secondary)
        self.assistant_kb_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Bottom area first (side="bottom") so the chat takes whatever height is left.
        footer = ctk.CTkFrame(panel, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=10, pady=(0, 8))
        self.screen_toggle = ctk.CTkSwitch(footer, text="Use current analysis", font=small,
                                           text_color=COLORS["text_muted"], switch_width=32, switch_height=16)
        self.screen_toggle.select()
        self.screen_toggle.pack(side="left")
        self.assistant_status_label = ctk.CTkLabel(footer, text="", font=small, anchor="e",
                                                   text_color=COLORS["text_muted"])
        self.assistant_status_label.pack(side="right", fill="x", expand=True)

        input_card = ctk.CTkFrame(panel, fg_color=COLORS["card"], corner_radius=10,
                                  border_width=1, border_color=COLORS["border"])
        input_card.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        self.assistant_entry = ctk.CTkEntry(
            input_card, placeholder_text="Ask about motors, your results, or this app…",
            height=38, border_width=0, fg_color=COLORS["card"], font=(FONTS["family"], 12),
        )
        self.assistant_entry.pack(side="left", fill="x", expand=True, padx=(6, 4), pady=4)
        self.assistant_entry.bind("<Return>", lambda e: self._send_chat_message())
        self.assistant_send_btn = ctk.CTkButton(
            input_card, text="Send", width=64, height=32, command=self._send_chat_message,
            fg_color=COLORS["primary"], hover_color=COLORS["primary_hover"],
        )
        self.assistant_send_btn.pack(side="right", padx=(0, 6), pady=6)

        chips = ctk.CTkFrame(panel, fg_color="transparent")
        chips.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        chip = dict(height=26, corner_radius=13, font=small, fg_color=COLORS["header_bg_soft"],
                    text_color=COLORS["primary"], hover_color=COLORS["border"])
        for label, action in (("Suggest improvements", self._suggest_improvements),
                              ("Explain my plot", lambda: self._ask("Explain the analysis currently shown and what stands out.")),
                              ("What can you do?", lambda: self._ask("How can you help me?"))):
            ctk.CTkButton(chips, text=label, command=action, **chip).pack(side="left", padx=(0, 6))

        self.assistant_history = ctk.CTkTextbox(
            panel, fg_color=COLORS["card"], text_color=COLORS["text"], corner_radius=10,
            border_width=1, border_color=COLORS["border"],
            wrap="word", font=(FONTS["family"], 12), state="disabled",
        )
        self.assistant_history.pack(side="top", fill="both", expand=True, padx=10, pady=(8, 6))
        text = self.assistant_history._textbox
        # CTk intentionally disallows font in tag_config; use the underlying Tk Text.
        text.configure(padx=10, pady=8, spacing1=2, spacing3=2)
        text.tag_configure("assistant", foreground=COLORS["text"], lmargin1=4, lmargin2=4)
        text.tag_configure("user", foreground=COLORS["text"], background=COLORS["header_bg_soft"],
                           lmargin1=40, lmargin2=40, rmargin=4, spacing1=2, spacing3=4)
        text.tag_configure("user_label", foreground=COLORS["primary"], font=(FONTS["family"], 10, "bold"),
                           justify="right", spacing1=10)
        text.tag_configure("assistant_label", foreground=COLORS["header_bg"],
                           font=(FONTS["family"], 10, "bold"), spacing1=10)
        text.tag_configure("meta", foreground=COLORS["text_muted"], font=(FONTS["family"], 10),
                           lmargin1=4, lmargin2=4, spacing3=4)
        for tag, font in {"heading": (FONTS["family"], 13, "bold"),
                          "bold": (FONTS["family"], 12, "bold"),
                          "code": (FONTS["mono"], 11)}.items():
            text.tag_configure(tag, font=font, lmargin1=4, lmargin2=4)
        text.tag_configure("heading", spacing1=8, spacing3=4)

        self._append_history(
            "Hi! Ask about EV motors and control, your current analysis, how this app "
            "computes something, or the documents in knowledge_base/. Answers run on a "
            "local model; change it under ⚙.",
            "meta",
        )
        self._poll_assistant_queue()
        self._refresh_models()

    def _persist_model_choices(self):
        try:
            temporary = SETTINGS_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps({"model": self._chosen_model}, indent=2), encoding="utf-8")
            temporary.replace(SETTINGS_PATH)
        except OSError as exc:
            self._set_assistant_status(f"Cannot save model choices: {exc}")

    def _save_model_choice(self, model):
        self._chosen_model = model.strip()
        self._persist_model_choices()
        self._warm_up_model()

    def _warm_up_model(self):
        """Preload the chosen model with the system prompt in the background so
        the first question doesn't pay the load + prompt-processing cost."""
        model = self.model_picker.get().strip()
        try:
            prompt = system_prompt(tuple(self.plot_type.cget("values")))
        except Exception:
            return
        if not model:
            return
        def worker():
            try:
                llm_client.warm_up(model, prompt)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _open_model_lab(self):
        from .assistant_lab import ModelLab
        if getattr(self, "_model_lab", None) is not None and self._model_lab.winfo_exists():
            self._model_lab.lift()
            return
        self._model_lab = ModelLab(self)

    def _refresh_models(self):
        def worker():
            try:
                self._assistant_queue.put(("models", llm_client.list_models()))
            except llm_client.OllamaError as exc:
                self._assistant_queue.put(("notice", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _clear_conversation(self):
        if self._assistant_busy:
            return
        self._conversation.clear()
        self.assistant_history.configure(state="normal")
        self.assistant_history.delete("1.0", "end")
        self.assistant_history.configure(state="disabled")

    def _screen_snapshot(self):
        """Live app state for the model, read on the Tk thread: the visible
        non-empty inputs, the app's own computed observations for the current
        analysis, displayed result labels, and a short summary of each plotted line."""
        if not self.screen_toggle.get():
            return {}
        analysis = self.plot_type.get()
        inputs = {}
        for name, widget, kind in self._scenario_widgets():
            if kind != "entry" or name == "assistant_entry" or len(inputs) >= 40:
                continue
            try:
                value = str(widget.get()).strip()
                if value and widget.winfo_ismapped():
                    inputs[name] = value[:40]
            except Exception:
                continue
        results = []
        try:
            results += list(self._report_observations(analysis))
        except Exception:
            pass
        for name in ("thermal_results_label", "engine_results_label", "range_results_label",
                     "mech_results_label", "params_label"):
            widget = getattr(self, name, None)
            try:
                if widget is not None and widget.winfo_ismapped():
                    text = " ".join(str(widget.cget("text")).split())
                    if text:
                        results.append(text[:600])
            except Exception:
                continue
        plots = []
        figure = getattr(self, "figure", None)
        for axis in (figure.axes[:3] if figure is not None else []):
            lines = []
            for line in axis.lines[:5]:
                label = line.get_label()
                if label.startswith("_"):
                    continue
                try:
                    x, y = line.get_data()
                    text = summarize_line(label, list(x), list(y))
                except (TypeError, ValueError):
                    continue
                if text:
                    lines.append(text)
            if lines:
                plots.append(f"{axis.get_title() or 'untitled'} ({axis.get_ylabel()} vs {axis.get_xlabel()}): "
                             + "; ".join(lines))
        return {"analysis": analysis, "inputs": inputs, "results": results, "plots": plots}

    def toggle_assistant_panel(self):
        if self._assistant_open:
            self.paned.forget(self.assistant_panel)
            self._assistant_open = False
        else:
            # No before=/after= -> PanedWindow appends at the end, i.e. the
            # rightmost pane (container and plot_frame were already added).
            self.paned.add(self.assistant_panel, minsize=380)
            self._assistant_open = True
            self._warm_up_model()

    def _append_history(self, text, tag="assistant"):
        """assistant: Markdown body (its "Assistant" label is written when the
        question is sent, so streamed text appears under it); user: a tinted,
        indented block under a "You" label; meta: small muted notes."""
        self.assistant_history.configure(state="normal")
        if tag == "assistant":
            for content, style in markdown_spans(text):
                self.assistant_history.insert("end", content, style)
            self.assistant_history.insert("end", "\n")
        elif tag == "user":
            self.assistant_history.insert("end", "You\n", "user_label")
            self.assistant_history.insert("end", text.strip() + "\n", "user")
        else:
            self.assistant_history.insert("end", text.rstrip() + "\n\n", tag)
        self.assistant_history.configure(state="disabled")
        self.assistant_history.see("end")

    def _set_assistant_status(self, text):
        try:
            self.assistant_status_label.configure(text=text)
        except Exception:
            pass

    def _set_assistant_busy(self, busy):
        self._assistant_busy = busy
        state = "disabled" if busy else "normal"
        self.assistant_send_btn.configure(state=state)
        self.assistant_kb_btn.configure(state=state)
        self.model_picker.configure(state=state)
        self.model_refresh.configure(state=state)

    def _send_chat_message(self):
        if self._assistant_busy:
            return
        question = self.assistant_entry.get().strip()
        if not question:
            return
        model = self.model_picker.get().strip()
        local_reply = small_talk_reply(question) or checked_calculation_reply(question)
        if not model and not local_reply:
            self._set_assistant_status("Choose an installed model first.")
            return
        try:
            screen = ({} if local_reply or not wants_screen_context(question, self._conversation)
                      else self._screen_snapshot())
            analysis_types = tuple(self.plot_type.cget("values"))
        except Exception as exc:
            self._set_assistant_status(f"Cannot capture analysis: {exc}")
            screen, analysis_types = {}, ()
        self.assistant_entry.delete(0, "end")
        self._append_history(question, "user")
        self.assistant_history.configure(state="normal")
        self.assistant_history.insert("end", "Assistant\n", "assistant_label")
        self.assistant_history.configure(state="disabled")
        self._set_assistant_busy(True)
        if model and model != self._chosen_model:
            self._chosen_model = model
            self._persist_model_choices()
        self._started = time.perf_counter()
        self._stream_text = ""
        self._stream_mark = self.assistant_history.index("end-1c")
        self._assistant_phase = "Searching documents / loading model"
        self._set_assistant_status(self._assistant_phase + "...")
        self._tick_response_timer()
        threading.Thread(target=self._chat_worker, args=(question, model, screen,
                         list(self._conversation), analysis_types, local_reply), daemon=True).start()

    def _suggest_improvements(self):
        self.screen_toggle.select()
        self._ask("Review my current analysis results. Suggest the three most effective design changes "
                  "or checks, each tied to a specific input or result, and say what data is missing.")

    def _ask(self, question):
        """Send a canned question (quick-action chips)."""
        if self._assistant_busy:
            return
        self.assistant_entry.delete(0, "end")
        self.assistant_entry.insert(0, question)
        self._send_chat_message()

    def _toggle_assistant_settings(self):
        if self._assistant_settings.winfo_ismapped():
            self._assistant_settings.pack_forget()
        else:
            self._assistant_settings.pack(side="top", fill="x", padx=10, pady=(8, 0),
                                          before=self.assistant_history)

    def _tick_response_timer(self):
        if self._assistant_busy and self._stream_mark is not None:
            self._set_assistant_status(f"{self._assistant_phase} • {time.perf_counter() - self._started:.1f}s elapsed")
            self.after(100, self._tick_response_timer)

    def _chat_worker(self, question, model, screen, history, analysis_types, local_reply):
        started = time.perf_counter()
        try:
            hits = []
            if not local_reply:
                try:
                    hits = rag_store.query(question)
                except Exception as exc:
                    self._assistant_queue.put(("notice", f"Document search unavailable: {exc}"))
            retrieval_s = time.perf_counter() - started
            if local_reply:
                reply = local_reply
                metrics = {"model": "Local calculation" if reply.startswith("**") else "Conversation",
                           "tokens_per_s": None, "truncated": False}
            else:
                messages = build_messages(question, screen, hits, history, analysis_types)
                announced = False

                def on_chunk(chunk):
                    nonlocal announced
                    if not announced:
                        self._assistant_queue.put(("phase", "Generating answer"))
                        announced = True
                    self._assistant_queue.put(("chunk", chunk))
                reply, metrics = llm_client.stream_chat(messages, model, MAX_TOKENS, on_chunk=on_chunk)
            metrics.update(retrieval_s=retrieval_s, end_to_end_s=time.perf_counter() - started,
                           sources=sorted({h["source"] for h in hits}), used_app_state=bool(screen))
            self._log_chat_exchange(question, reply, "ok", metrics)
            self._assistant_queue.put(("chat_reply", (question, reply, metrics)))
        except Exception as exc:
            self._log_chat_exchange(question, str(exc), "error", {"model": model})
            self._assistant_queue.put(("chat_error", str(exc)))

    def _log_chat_exchange(self, question, answer, status, metrics=None):
        """Append one question/answer pair to CHAT_LOG_PATH (JSON Lines) so
        the conversation history can be reviewed or exported later for
        fine-tuning / evaluation. Runs on the background chat thread -- no
        Tk widgets touched here, so no queue hop is needed."""
        record = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "answer": answer,
            "status": status, "metrics": metrics or {},
        }
        try:
            with open(CHAT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _rebuild_kb_async(self):
        if self._assistant_busy:
            return
        self._set_assistant_busy(True)
        self._set_assistant_status("Rebuilding knowledge base...")

        def progress(msg):
            self._assistant_queue.put(("kb_progress", msg))

        def worker():
            try:
                n_files, n_chunks, warnings = rag_store.rebuild_index(progress=progress)
                self._assistant_queue.put(("kb_done", (n_files, n_chunks, warnings)))
            except llm_client.OllamaError as e:
                self._assistant_queue.put(("kb_error", str(e)))
            except Exception as e:
                self._assistant_queue.put(("kb_error", f"Unexpected error: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_assistant_queue(self):
        try:
            while True:
                kind, payload = self._assistant_queue.get_nowait()
                if kind == "chat_reply":
                    question, reply, metrics = payload
                    self.assistant_history.configure(state="normal")
                    self.assistant_history.delete(self._stream_mark, "end")
                    self.assistant_history.configure(state="disabled")
                    self._stream_mark = None
                    self._append_history(reply, "assistant")
                    self._conversation.extend([{"role": "user", "content": question},
                                               {"role": "assistant", "content": reply}])
                    self._conversation = self._conversation[-6:]
                    rate = metrics.get("tokens_per_s")
                    details = f"{metrics['model']} • {metrics['end_to_end_s']:.1f}s"
                    if rate:
                        details += f" • {rate:.0f} tok/s"
                    if metrics.get("used_app_state"):
                        details += " • used current analysis"
                    if metrics.get("sources"):
                        details += "\nSources: " + ", ".join(metrics["sources"])
                    if metrics.get("truncated"):
                        details += "\nAnswer hit the length limit; ask it to continue."
                    self._append_history(details, "meta")
                    self._set_assistant_status("Ready")
                    self._set_assistant_busy(False)
                elif kind == "chunk":
                    self._stream_text += payload
                    self.assistant_history.configure(state="normal")
                    self.assistant_history.delete(self._stream_mark, "end")
                    for content, style in markdown_spans(self._stream_text):
                        self.assistant_history.insert("end", content, style)
                    self.assistant_history.configure(state="disabled")
                    self.assistant_history.see("end")
                    self._set_assistant_status(f"Generating ({time.perf_counter() - self._started:.0f}s)")
                elif kind == "models":
                    if payload:
                        self._available_models = payload
                        self.model_picker.configure(values=payload)
                        if self._chosen_model not in payload:
                            self._chosen_model = llm_client.CHAT_MODEL if llm_client.CHAT_MODEL in payload else payload[0]
                            self._persist_model_choices()
                        if not self._assistant_busy:
                            self.model_picker.set(self._chosen_model)
                    else:
                        self._append_history("No chat models installed in Ollama.", "meta")
                elif kind == "notice":
                    self._append_history(payload, "meta")
                    if self._assistant_busy and self._stream_mark is not None and not self._stream_text:
                        self._stream_mark = self.assistant_history.index("end-1c")
                elif kind == "phase":
                    self._assistant_phase = payload
                elif kind == "chat_error":
                    self._append_history(f"[error] {payload}", "meta")
                    self._set_assistant_status("Error -- see message above.")
                    self._set_assistant_busy(False)
                    self._stream_mark = None
                elif kind == "kb_progress":
                    self._set_assistant_status(payload)
                elif kind == "kb_done":
                    n_files, n_chunks, warnings = payload
                    msg = f"Indexed {n_files} changed file(s), {n_chunks} chunk(s)."
                    if warnings:
                        msg += f" {len(warnings)} file(s) skipped."
                    self._append_history(msg, "meta")
                    self._set_assistant_status("Ready." if not warnings else "; ".join(warnings)[:200])
                    self._set_assistant_busy(False)
                elif kind == "kb_error":
                    self._append_history(f"[error] {payload}", "meta")
                    self._set_assistant_status("Error -- see message above.")
                    self._set_assistant_busy(False)
        except queue.Empty:
            pass
        self.after(150, self._poll_assistant_queue)
